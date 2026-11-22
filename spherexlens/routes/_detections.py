"""
Shared by the votes queue and the catalogue exports: every triaged detection
across the registered fields, flattened to one row each.

Source of dashboards, in order:
  1. the published Cloud snapshot (when this instance reads the bucket);
  2. otherwise app.get_dashboard(target, source="cache") for each registered
     field (step1_ingest.TARGETS), plus the RAM-only custom fields this
     process has loaded.

The result is cached in memory for config.Production.VOTES_QUEUE_CACHE_S.
(Underscore module: not auto-included as a router.)
"""
import threading
import time

import config

_CACHE = {"t": 0.0, "key": None, "value": None}
_LOCK = threading.Lock()

COLUMNS = (
    "target", "detection_id", "ra", "dec", "mjd_a", "mjd_b", "kind", "snr",
    "category", "verdict", "priority", "ml_real_bogus", "evidence_score",
    "asteroid_status", "pipeline_version", "config_hash", "model_version",
)


def _published_dashboards():
    """[(target, dashboard)] from the published snapshot, or None if no store."""
    import app
    store = app._snapshot_store()
    if store is None:
        return None, None
    pointer, manifest = app._published_state(store)
    out = []
    for target, entry in sorted((manifest.get("targets") or {}).items()):
        name = (entry or {}).get("dashboard")
        if not name:
            continue
        try:
            dashboard = store.read_json(name)
        except Exception:
            dashboard = None
        if isinstance(dashboard, dict):
            out.append((target, dashboard))
    return out, (pointer or {}).get("manifest")


def _local_dashboards():
    import app
    out = []
    try:
        from step1_ingest import TARGETS
        names = list(TARGETS)
    except Exception:
        names = []
    for target in names:
        try:
            result = app.get_dashboard(target=target, source="cache")
        except Exception:
            continue
        if isinstance(result, dict) and result.get("detections") is not None:
            out.append((target, result))
    for target, dashboard in list(app.FIELD_DASH_CACHE.items()):
        if isinstance(dashboard, dict):
            out.append((target, dashboard))
    return out


def collect_dashboards():
    """[(target, dashboard)] and a cache key naming the snapshot (or 'local')."""
    published, manifest = _published_dashboards()
    if published is not None:
        return published, manifest or "snapshot"
    return _local_dashboards(), "local"


def _num(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def flatten(target, dashboard):
    stamp = dashboard.get("pipeline") or {}
    rows = []
    for det in dashboard.get("detections") or []:
        if not isinstance(det, dict):
            continue
        ml = det.get("ml") or {}
        evidence = det.get("evidence") or {}
        asteroid = det.get("asteroid") or {}
        position = det.get("position") or [None, None]
        detection_id = det.get("detection_id") or (
            f"{target}:{det.get('pair')}:{position[0]},{position[1]}")
        rows.append({
            "target": target,
            "detection_id": detection_id,
            "ra": _num(det.get("ra")),
            "dec": _num(det.get("dec")),
            "mjd_a": _num(det.get("mjd_a")),
            "mjd_b": _num(det.get("mjd_b")),
            "kind": det.get("kind"),
            "snr": _num(det.get("snr")),
            "category": det.get("category"),
            "verdict": det.get("verdict"),
            "priority": det.get("priority"),
            "ml_real_bogus": _num(ml.get("real_bogus")),
            "evidence_score": _num(evidence.get("score")),
            "asteroid_status": asteroid.get("status"),
            "pipeline_version": stamp.get("pipeline_version"),
            "config_hash": stamp.get("config_hash"),
            "model_version": ml.get("model_version") or stamp.get("model_version"),
            "_detection": det,
        })
    return rows


def all_detections(force=False):
    """Every triaged detection as flat rows (cached), plus provenance."""
    now = time.monotonic()
    with _LOCK:
        if (not force and _CACHE["value"] is not None
                and now - _CACHE["t"] < config.Production.VOTES_QUEUE_CACHE_S):
            return _CACHE["value"]
    dashboards, key = collect_dashboards()
    rows = []
    for target, dashboard in dashboards:
        rows.extend(flatten(target, dashboard))
    value = {"rows": rows, "source": key, "n_fields": len(dashboards),
             "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with _LOCK:
        _CACHE.update(t=now, key=key, value=value)
    return value


def reset_cache():
    with _LOCK:
        _CACHE.update(t=0.0, key=None, value=None)
