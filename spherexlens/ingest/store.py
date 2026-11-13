"""
spherexlens.ingest.store
Versioned Parquet snapshots of a target's catalogues (Phase 2.3).

Layout under the store root (env SPHEREX_CATALOG_STORE, default
<SPHEREX_CACHE or data_cache>/catalogs):

    <target>/<snapshot_id>/gaia.parquet
    <target>/<snapshot_id>/wise.parquet
    <target>/<snapshot_id>/vsx.parquet
    <target>/<snapshot_id>/manifest.json

snapshot_id = "<UTC yyyymmddThhmmssZ>-<first 10 hex of the content hash>".
The manifest records, per catalogue: source, mirror, release, query time,
rows, sha256 (of the canonical JSON of the rows, so it does not depend on
the Parquet writer), plus the snapshot ``version`` (1, 2, ... per target).
Saving the same content again returns the existing snapshot (no new version).

The target meta JSON gets ``catalog_snapshot_id``; its inline "catalogs" stay
where they are, so everything that reads JSON keeps working -- Parquet is
additive.  catalogs_for_meta() reads JSON first and falls back to Parquet.
"""
import hashlib
import json
import os
import time

import pyarrow as pa
import pyarrow.parquet as pq

CATALOG_SOURCES = {"gaia": "Gaia", "wise": "AllWISE (allwise_p3as_psd)",
                   "vsx": "AAVSO VSX (B/vsx/vsx)"}


def store_root(root=None):
    if root:
        return root
    env = os.environ.get("SPHEREX_CATALOG_STORE")
    if env:
        return env
    return os.path.join(os.environ.get("SPHEREX_CACHE", "data_cache"), "catalogs")


def rows_sha256(rows):
    blob = json.dumps(rows or [], sort_keys=True, separators=(",", ":"),
                      default=str, allow_nan=True).encode()
    return hashlib.sha256(blob).hexdigest()


def rows_to_table(rows):
    """List of dicts -> Arrow table (union of keys; mixed columns as text)."""
    keys = []
    for r in rows or []:
        for k in r:
            if k not in keys:
                keys.append(k)
    cols = {}
    for k in keys:
        values = [r.get(k) for r in rows]
        try:
            cols[k] = pa.array(values)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            cols[k] = pa.array([None if v is None else str(v) for v in values])
    if not cols:
        return pa.table({})
    return pa.table(cols)


def table_to_rows(table):
    return table.to_pylist()


def _safe(name):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))


def list_snapshots(target, root=None):
    """Manifests of a target's snapshots, oldest first."""
    base = os.path.join(store_root(root), _safe(target))
    out = []
    if not os.path.isdir(base):
        return out
    for sid in os.listdir(base):
        path = os.path.join(base, sid, "manifest.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                out.append(json.load(f))
    return sorted(out, key=lambda m: (m.get("version", 0), m["snapshot_id"]))


def save_snapshot(target, catalogs, info=None, root=None, query_time=None):
    """Write catalogues as a new snapshot; returns its manifest.

    info: {cat: {"mirror": ..., "release": ..., "status": ..., "source": ...}}
    """
    info = info or {}
    query_time = query_time or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entries = {}
    for cat, rows in (catalogs or {}).items():
        rows = rows or []
        extra = info.get(cat) or {}
        entries[cat] = {"source": extra.get("source", CATALOG_SOURCES.get(cat, cat)),
                        "mirror": extra.get("mirror"),
                        "release": extra.get("release"),
                        "status": extra.get("status"),
                        "query_utc": extra.get("query_utc", query_time),
                        "rows": len(rows), "sha256": rows_sha256(rows),
                        "file": f"{_safe(cat)}.parquet"}
    content = hashlib.sha256(json.dumps(
        {c: e["sha256"] for c, e in sorted(entries.items())}).encode()).hexdigest()

    previous = list_snapshots(target, root)
    for m in previous:
        if m.get("content_sha256") == content:
            return m
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    sid = f"{stamp}-{content[:10]}"
    folder = os.path.join(store_root(root), _safe(target), sid)
    tmp = folder + ".tmp"
    os.makedirs(tmp, exist_ok=True)
    for cat, rows in (catalogs or {}).items():
        pq.write_table(rows_to_table(rows or []),
                       os.path.join(tmp, entries[cat]["file"]))
    manifest = {"target": target, "snapshot_id": sid,
                "version": (previous[-1].get("version", 0) + 1) if previous else 1,
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "content_sha256": content, "catalogs": entries}
    with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    os.replace(tmp, folder)
    return manifest


def load_snapshot(target, snapshot_id=None, root=None, verify=True):
    """(catalogs, manifest) of a snapshot (latest when snapshot_id is None)."""
    if snapshot_id is None:
        snaps = list_snapshots(target, root)
        if not snaps:
            raise FileNotFoundError(f"no catalogue snapshot for {target}")
        snapshot_id = snaps[-1]["snapshot_id"]
    folder = os.path.join(store_root(root), _safe(target), _safe(snapshot_id))
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    catalogs = {}
    for cat, entry in manifest["catalogs"].items():
        rows = table_to_rows(pq.read_table(os.path.join(folder, entry["file"])))
        if verify and len(rows) != entry["rows"]:
            raise ValueError(f"{snapshot_id}/{cat}: {len(rows)} rows, "
                             f"manifest says {entry['rows']}")
        catalogs[cat] = rows
    return catalogs, manifest


def snapshot_meta(record, info=None, root=None):
    """Snapshot a target meta record's catalogues and stamp the record.

    Sets record["catalog_snapshot_id"]; returns the manifest (None when the
    record has no catalogues).
    """
    cats = record.get("catalogs")
    if not cats:
        return None
    manifest = save_snapshot(record.get("target") or "unknown", cats, info, root)
    record["catalog_snapshot_id"] = manifest["snapshot_id"]
    return manifest


def catalogs_for_meta(meta, root=None):
    """The meta's catalogues: inline JSON if present, else its Parquet snapshot."""
    if meta.get("catalogs"):
        return meta["catalogs"]
    sid = meta.get("catalog_snapshot_id")
    if sid:
        cats, _ = load_snapshot(meta.get("target") or "unknown", sid, root)
        return cats
    return {}
