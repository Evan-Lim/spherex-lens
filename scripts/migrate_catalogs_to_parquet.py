"""
Copy every cached target's catalogues (data_cache/<target>_meta.json) into
versioned Parquet snapshots (spherexlens.ingest.store) and stamp each meta
JSON with ``catalog_snapshot_id``.  The inline JSON catalogues are kept, so
nothing that reads JSON changes.

    python scripts/migrate_catalogs_to_parquet.py            # dry run (default)
    python scripts/migrate_catalogs_to_parquet.py --apply    # write
    python scripts/migrate_catalogs_to_parquet.py --cache data_cache --store /tmp/cats
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spherexlens.ingest import store  # noqa: E402


def migrate(cache, root=None, apply=False, log=print):
    done = []
    for path in sorted(glob.glob(os.path.join(cache, "*_meta.json"))):
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        cats = meta.get("catalogs") or {}
        counts = {k: len(v or []) for k, v in cats.items()}
        name = os.path.basename(path)
        if not cats:
            log(f"  skip {name}: no catalogues")
            continue
        if meta.get("catalog_snapshot_id") and not apply:
            log(f"  {name}: already {meta['catalog_snapshot_id']}")
            continue
        if not apply:
            log(f"  would snapshot {name}: {counts}")
            done.append((name, None))
            continue
        status = meta.get("catalog_status") or {}
        info = {k: {"mirror": "unknown (pre-v2 JSON cache)",
                    "status": status.get(k) if isinstance(status.get(k), str) else None,
                    "query_utc": None}
                for k in cats}
        info.setdefault("gaia", {})["release"] = "DR3"
        manifest = store.snapshot_meta(meta, info, root)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        os.replace(tmp, path)
        log(f"  {name}: snapshot {manifest['snapshot_id']} (v{manifest['version']}) {counts}")
        done.append((name, manifest["snapshot_id"]))
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", default=os.environ.get("SPHEREX_CACHE", "data_cache"))
    ap.add_argument("--store", default=None, help="store root (default <cache>/catalogs)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()
    root = args.store or os.path.join(args.cache, "catalogs")
    print(("APPLY" if args.apply else "DRY RUN") + f": {args.cache} -> {root}")
    migrate(args.cache, root, apply=args.apply)


if __name__ == "__main__":
    main()
