"""Incrementally refresh registered SPHEREx fields and publish snapshots."""

import hashlib
import json
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SPHEREX_CACHE", "/tmp/spherex-data")
# published snapshots must carry the complete asteroid check
os.environ.setdefault("SPHEREX_ASTEROID_TIME_BOX_S", "0")
os.environ["SPHEREX_SNAPSHOT_READS"] = "false"
os.makedirs(os.environ["SPHEREX_CACHE"], exist_ok=True)

import app as portal
import step1_ingest as ingest
import step7_neowise
from snapshot_store import SnapshotStore


CACHE = os.environ["SPHEREX_CACHE"]
import config as _config
COLLECTION = _config.SPHEREX_COLLECTION
SIZE_DEG = float(os.environ.get("SPHEREX_CUTOUT_SIZE_DEG", "0.2"))
CATALOG_REFRESH_DAYS = int(os.environ.get("SPHEREX_CATALOG_REFRESH_DAYS", "7"))
PREVIOUS_SNAPSHOT_RETENTION_HOURS = int(
    os.environ.get("SPHEREX_PREVIOUS_SNAPSHOT_RETENTION_HOURS", "48"))


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_utc(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_fingerprint(rows, ra, dec):
    # Shared with the IRSA-fresh views (step1_ingest), so a fresh check can
    # tell whether IRSA lists anything this snapshot has not processed.
    return ingest.source_fingerprint(rows, ra, dec)


def _target_coordinates(target):
    return ingest.target_coordinates(target)


def _catalog_refresh_due(entry, now):
    last_checked = _parse_utc((entry or {}).get("catalogs_checked_at"))
    return (last_checked is None
            or now - last_checked >= timedelta(days=CATALOG_REFRESH_DAYS))


def _write_refresh_status(store, **updates):
    status = store.read_json("refresh-status/latest.json") or {}
    status.update(updates)
    store.write_json("refresh-status/latest.json", status)


def _detection_positions_hash(dashboard):
    positions = sorted(
        (round(float(detection["ra"]), 6),
         round(float(detection["dec"]), 6))
        for detection in dashboard.get("detections", [])
        if detection.get("ra") is not None and detection.get("dec") is not None
    )
    return _canonical_hash(positions)


def _restore_target_cache(store, entry, target):
    if not entry:
        return None
    meta_object = entry.get("meta_object")
    if not meta_object:
        return None
    meta = store.read_json(meta_object)
    if not meta:
        return None
    for filename, object_name in entry.get("cache_objects", {}).items():
        payload = store.read_bytes(object_name)
        if payload is None:
            raise RuntimeError(
                f"Snapshot cache object is missing for {target}: {object_name}")
        local_path = os.path.join(CACHE, filename)
        with open(local_path, "wb") as file:
            file.write(payload)
    meta_path = os.path.join(CACHE, f"{target}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as file:
        json.dump(meta, file, indent=2)
    return meta


def _store_target_cache(store, target, meta):
    objects = {}
    for path in meta.get("paths", []):
        local_path = portal._normalize_data_path(path)
        if not os.path.isfile(local_path):
            raise RuntimeError(
                f"Pipeline output for {target} is missing: {local_path}")
        with open(local_path, "rb") as file:
            payload = file.read()
        object_name, _ = store.write_bytes_immutable(
            "cache", payload, "application/octet-stream")
        objects[os.path.basename(local_path)] = object_name
    meta_object, _ = store.write_json_immutable(
        f"cache-metadata/{target}", meta)
    return objects, meta_object


def _response_payload(response):
    if isinstance(response, dict):
        return response
    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return {"error": f"Pipeline returned {type(response).__name__}"}


def _existing_dashboard(store, entry):
    return store.read_target_json({"targets": {"target": entry}}, "target",
                                  "dashboard")


def _refresh_target(store, target, prior, source_fingerprint, rows, ra, dec,
                    checked_at, refresh_catalogs):
    if not rows:
        if prior and prior.get("dashboard"):
            print(f"  {target}: IRSA returned no products; retaining last snapshot")
            retained = dict(prior)
            retained["last_checked_at"] = checked_at
            retained["last_check"] = "no_products_retained"
            return retained
        target_meta = ingest.TARGETS[target]
        unavailable = {
            "target": target,
            "common": target_meta.get("common", target),
            "ra": ra,
            "dec": dec,
            "error": "No SPHEREx products are currently available from IRSA.",
            "source": "scheduled",
            "generated_at": checked_at,
        }
        dashboard_object, _ = store.write_json_immutable(
            f"snapshots/dashboards/{target}", unavailable)
        recovery_object, _ = store.write_json_immutable(
            f"snapshots/recovery/{target}",
            {"target": target, "error": unavailable["error"],
             "source": "scheduled", "generated_at": checked_at})
        return {
            "dashboard": dashboard_object,
            "recovery": recovery_object,
            "source_fingerprint": source_fingerprint,
            "last_checked_at": checked_at,
            "catalogs_checked_at": (prior or {}).get("catalogs_checked_at"),
            "last_check": "no_products",
            "cache_objects": {},
        }

    old_meta = _restore_target_cache(store, prior, target) if prior else None
    previous_catalogs = old_meta.get("catalogs") if old_meta else None

    # B1 FIX: restore the prior NEOWISE file to disk BEFORE calling
    # get_dashboard for the first time.  load_neowise() reads from disk, so
    # the initial dashboard must find the file already there; otherwise a
    # catalog-only refresh (detections unchanged → refresh_neowise=False)
    # publishes a dashboard with no NEOWISE panel.
    neowise_path = os.path.join(CACHE, f"{target}_neowise.json")
    neowise_object = (prior or {}).get("neowise_object")
    neowise_checked_at = (prior or {}).get("neowise_checked_at")
    if old_meta and neowise_object:
        previous_neowise = store.read_json(neowise_object)
        if previous_neowise:
            with open(neowise_path, "w", encoding="utf-8") as file:
                json.dump(previous_neowise, file, indent=1)

    meta = ingest.ingest_target(
        target, collection=COLLECTION, size_deg=SIZE_DEG,
        epoch_mode="all", overwrite=False, rows=rows,
        catalogs=None if refresh_catalogs else previous_catalogs,
        strict_catalogs=refresh_catalogs,
    )
    if not meta:
        raise RuntimeError(
            f"{target}: IRSA listed products but no validated cutouts were ingested")
    if refresh_catalogs and previous_catalogs:
        for key in ("gaia", "wise", "vsx"):
            if previous_catalogs.get(key) and not meta["catalogs"].get(key):
                raise RuntimeError(
                    f"{target}: {key} refresh returned no rows; preserving "
                    "the last published snapshot")

    initial_dashboard = portal.get_dashboard(target=target, source="cache")
    dashboard = _response_payload(initial_dashboard)
    if not isinstance(initial_dashboard, dict) or dashboard.get("error"):
        previous_dashboard = _existing_dashboard(store, prior) if prior else None
        if previous_dashboard and previous_dashboard.get("image") is not None:
            print(f"  {target}: new data did not form a usable dashboard; "
                  "retaining last successful snapshot")
            retained = dict(prior)
            retained["last_checked_at"] = checked_at
            retained["last_check"] = "pipeline_error_retained"
            return retained
        error = dashboard.get("error", "Pipeline did not return a dashboard")
        dashboard_object, _ = store.write_json_immutable(
            f"snapshots/dashboards/{target}", dashboard)
        recovery_object, _ = store.write_json_immutable(
            f"snapshots/recovery/{target}",
            {"target": target, "error": error, "source": "scheduled",
             "generated_at": checked_at})
        cache_objects, meta_object = _store_target_cache(store, target, meta)
        return {
            "dashboard": dashboard_object,
            "recovery": recovery_object,
            "source_fingerprint": source_fingerprint,
            "last_checked_at": checked_at,
            "catalogs_checked_at": (
                checked_at if refresh_catalogs
                else (prior or {}).get("catalogs_checked_at")),
            "last_check": "pipeline_unavailable",
            "cache_objects": cache_objects,
            "meta_object": meta_object,
            "neowise_object": neowise_object,
        }

    detections_hash = _detection_positions_hash(dashboard)
    old_detections_hash = prior.get("detections_hash") if prior else None
    refresh_neowise = (
        detections_hash != old_detections_hash
        or not prior
        or not prior.get("neowise_object")
    )
    if refresh_neowise:
        neowise = step7_neowise.run_target(
            target, dashboard=dashboard, meta=meta, strict=True)
        if neowise is None:
            raise RuntimeError(f"{target}: NEOWISE refresh did not produce output")
        neowise_checked_at = neowise.get("generated_utc") or checked_at
        dashboard_result = portal.get_dashboard(target=target, source="cache")
        dashboard = _response_payload(dashboard_result)
        if not isinstance(dashboard_result, dict) or dashboard.get("error"):
            raise RuntimeError(
                f"{target}: dashboard failed after the NEOWISE refresh")
    dashboard["source"] = "scheduled"
    dashboard["generated_at"] = checked_at

    recovery_result = portal.get_recovery(target=target, source="cache")
    recovery = _response_payload(recovery_result)
    if isinstance(recovery_result, dict):
        recovery["target"] = target
    elif "error" not in recovery:
        recovery["error"] = "Recovery grid could not be generated."
    recovery["source"] = "scheduled"
    recovery["generated_at"] = checked_at

    dashboard_object, _ = store.write_json_immutable(
        f"snapshots/dashboards/{target}", dashboard)
    recovery_object, _ = store.write_json_immutable(
        f"snapshots/recovery/{target}", recovery)
    cache_objects, meta_object = _store_target_cache(store, target, meta)
    neowise_object = None
    if os.path.isfile(neowise_path):
        with open(neowise_path, "r", encoding="utf-8") as file:
            neowise_data = json.load(file)
        neowise_object, _ = store.write_json_immutable(
            f"snapshots/neowise/{target}", neowise_data)

    return {
        "dashboard": dashboard_object,
        "recovery": recovery_object,
        "neowise_object": neowise_object,
        "source_fingerprint": source_fingerprint,
        "detections_hash": detections_hash,
        "last_checked_at": checked_at,
        "last_updated_at": checked_at,
        "last_check": "updated",
        "neowise_checked_at": neowise_checked_at,
        "catalogs_checked_at": (
            checked_at if refresh_catalogs
            else (prior or {}).get("catalogs_checked_at")),
        "cache_objects": cache_objects,
        "meta_object": meta_object,
    }


def _referenced_objects(store, manifest, pointer):
    objects = set()
    cutoff = _utcnow() - timedelta(hours=PREVIOUS_SNAPSHOT_RETENTION_HOURS)
    manifest_object = pointer["manifest"]
    current = manifest
    is_current = True
    while current and manifest_object:
        created_at = _parse_utc(current.get("created_at"))
        if (not is_current
                and (created_at is None or created_at < cutoff)):
            break
        objects.add(manifest_object)
        objects.update({current["mosaic"], current["targets_object"]})
        for entry in current.get("targets", {}).values():
            for key in ("dashboard", "recovery", "neowise_object", "meta_object"):
                if entry.get(key):
                    objects.add(entry[key])
            objects.update(entry.get("cache_objects", {}).values())
        is_current = False
        manifest_object = current.get("previous_manifest")
        current = store.read_json(manifest_object) if manifest_object else None
    return objects


def _run_refresh_locked(store, started_at=None):
    started_at = started_at or _utcnow().isoformat()
    total_targets = len(ingest.TARGETS)
    counts = {
        "completed_targets": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "failed_count": 0,
    }
    # Per-field progress for the dashboard's 18-cell refresh grid.  The dict
    # is rewritten in full on every status write, so a reader never sees a
    # field state from a previous run.
    field_states = {name: "queued" for name in ingest.TARGETS}
    _write_refresh_status(
        store, status="running", started_at=started_at,
        total_targets=total_targets, current_target=None,
        current_target_index=0, current_target_state="starting",
        field_states=field_states, **counts)
    current_pointer, generation = store.read_current()
    previous_manifest = store.read_manifest(current_pointer) or {}
    targets = dict(previous_manifest.get("targets", {}))
    checked_at = _utcnow().isoformat()
    now = _parse_utc(checked_at)

    for index, name in enumerate(ingest.TARGETS, start=1):
        field_states[name] = "checking"
        _write_refresh_status(
            store, current_target=name, current_target_index=index,
            current_target_state="checking", field_states=field_states,
            **counts)
        # B3 FIX: catch per-field exceptions so a single bad field does not
        # abort all 18.  Keep the prior manifest entry so the last good data
        # stays visible.  Abort the publish only when a majority fail.
        try:
            ra, dec = _target_coordinates(name)
            rows = ingest.query_sia2(
                ra, dec, radius_deg=0.1, collection=COLLECTION,
                use_cache=False)
            fingerprint = _source_fingerprint(rows, ra, dec)
            prior = targets.get(name)
            source_unchanged = (
                prior and prior.get("source_fingerprint") == fingerprint)
            refresh_catalogs = bool(rows) and _catalog_refresh_due(prior, now)
            retry_pipeline = (
                prior and prior.get("last_check") == "pipeline_unavailable")
            if source_unchanged and not refresh_catalogs and not retry_pipeline:
                prior["last_checked_at"] = checked_at
                prior["last_check"] = "unchanged"
                print(f"  {name}: unchanged")
            else:
                targets[name] = _refresh_target(
                    store, name, prior, fingerprint, rows, ra, dec, checked_at,
                    refresh_catalogs)
            last_check = targets[name].get("last_check")
            if last_check in {
                    "unchanged", "no_products_retained",
                    "pipeline_error_retained"}:
                counts["unchanged_count"] += 1
                # "retained": IRSA answered, but the new data was unusable,
                # so the last good snapshot is still what is published.
                state = "unchanged" if last_check == "unchanged" else "retained"
            else:
                counts["updated_count"] += 1
                state = "updated"
            counts["completed_targets"] += 1
            field_states[name] = state
            _write_refresh_status(
                store, current_target=name, current_target_index=index,
                current_target_state=state, field_states=field_states,
                **counts)
        except Exception as field_exc:
            counts["failed_count"] += 1
            counts["completed_targets"] += 1
            tb = traceback.format_exc()
            print(f"  {name}: FAILED — {type(field_exc).__name__}: {field_exc}\n{tb}",
                  flush=True)
            # Keep previous manifest entry so the last published dashboard
            # is still reachable; add a failed marker if brand new.
            if name not in targets:
                targets[name] = {
                    "last_check": "failed",
                    "last_checked_at": checked_at,
                    "error": str(field_exc),
                }
            field_states[name] = "failed"
            try:
                _write_refresh_status(
                    store, current_target=name, current_target_index=index,
                    current_target_state="failed", failed_target=name,
                    field_states=field_states, **counts)
            except Exception as status_error:
                print("Could not save field failure progress: "
                      f"{type(status_error).__name__}")
            # Do NOT re-raise here; continue processing the remaining fields.

    # Abort publication only if more than half of all fields failed.
    if counts["failed_count"] > total_targets // 2:
        raise RuntimeError(
            f"{counts['failed_count']} of {total_targets} fields failed; "
            "publication aborted to avoid overwriting a good snapshot"
        )

    tiles = []
    target_rows = []
    for name, metadata in ingest.TARGETS.items():
        dashboard = _existing_dashboard(store, targets.get(name))
        if dashboard and dashboard.get("image") is not None:
            tiles.append(portal.mosaic_tile(dashboard))
            target_rows.append({
                "name": name,
                "common": dashboard.get("common", metadata.get("common", name)),
                "ra": dashboard["ra"],
                "dec": dashboard["dec"],
            })
        else:
            entry = {"name": name, "common": metadata.get("common", name),
                     "ra": metadata["ra"], "dec": metadata["dec"]}
            target_rows.append(entry)
            tiles.append(portal.unavailable_mosaic_tile(
                entry, (dashboard or {}).get(
                    "error", "No successful snapshot is available.")))

    mosaic = portal.mosaic_payload(tiles)
    mosaic["source"] = "scheduled"
    mosaic["generated_at"] = checked_at
    mosaic_object, _ = store.write_json_immutable("snapshots/mosaic", mosaic)
    targets_object, _ = store.write_json_immutable(
        "snapshots/targets", {"targets": target_rows})
    manifest = {
        "schema_version": 1,
        "created_at": checked_at,
        "schedule": "12h",
        "region": "us-central1",
        "targets": targets,
        "mosaic": mosaic_object,
        "targets_object": targets_object,
        "previous_manifest": (
            current_pointer.get("manifest") if current_pointer else None),
    }
    pointer = store.publish(manifest, expected_generation=generation)
    store.collect_garbage(_referenced_objects(store, manifest, pointer))
    return pointer


def run_refresh(store=None):
    store = store or SnapshotStore()
    lock = store.acquire_refresh_lock()
    started_at = _utcnow().isoformat()

    # B2 FIX: heartbeat thread keeps the lock alive every 5 minutes so a
    # long-running but healthy job is never preempted by the next scheduler
    # invocation after a crash.
    _heartbeat_stop = threading.Event()
    _lock_holder = [lock]  # mutable so the heartbeat can update the handle

    def _heartbeat():
        while not _heartbeat_stop.wait(timeout=300):
            try:
                _lock_holder[0] = store.heartbeat_refresh_lock(_lock_holder[0])
            except Exception as exc:
                print(f"Lock heartbeat failed (non-fatal): {exc}", flush=True)

    _hb_thread = threading.Thread(target=_heartbeat, daemon=True,
                                  name="refresh-lock-heartbeat")
    _hb_thread.start()

    try:
        store.write_json("refresh-status/latest.json", {
            "status": "running",
            "started_at": started_at,
            "total_targets": len(ingest.TARGETS),
            "completed_targets": 0,
            "current_target": None,
            "current_target_index": 0,
            "current_target_state": "starting",
            "updated_count": 0,
            "unchanged_count": 0,
            "failed_count": 0,
        })
        try:
            pointer = _run_refresh_locked(store, started_at)
        except Exception as error:
            try:
                _write_refresh_status(store,
                    status="failed",
                    completed_at=_utcnow().isoformat(),
                    error_code=type(error).__name__)
            except Exception as status_error:
                print("Could not save refresh failure status: "
                      f"{type(status_error).__name__}")
            raise
        _write_refresh_status(
            store, status="succeeded", completed_at=_utcnow().isoformat(),
            current_target=None, current_target_state="published",
            published_at=pointer.get("published_at"))
        return pointer
    finally:
        _heartbeat_stop.set()
        store.release_refresh_lock(_lock_holder[0])


if __name__ == "__main__":
    started = time.monotonic()
    result = run_refresh()
    print(f"Published {result['manifest']} in {time.monotonic() - started:.1f}s")
