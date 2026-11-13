"""
Health endpoints.

    GET /api/health/live   process is up; no network, no storage (uptime check)
    GET /api/health        pipeline stamp, published-snapshot age and cheap
                           archive probes (cached ~5 min)

``ok`` is false (HTTP 503) only when this instance cannot serve dashboards
(the snapshot store is configured but unreadable or has nothing published).
An upstream archive being down is reported under ``archives`` and leaves
``ok`` true: dashboards are still served from the snapshot.
"""
import concurrent.futures
import threading
import time
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import config
from spherexlens.http_guard import RateLimiter
from spherexlens.logging_setup import logged_route
from version import pipeline_stamp

router = APIRouter()

_STARTED = time.monotonic()
HEALTH_LIMIT = RateLimiter(30, 60.0)

# Cheap, fixed URLs (never user-controlled).  Any HTTP answer below 500
# counts as "up": a 400 for a parameter-less query still proves the service
# is answering.
ARCHIVE_PROBES = {
    "irsa_sia": "https://irsa.ipac.caltech.edu/SIA?COLLECTION=spherex_dr1&POS=circle+0+0+0.001&RESPONSEFORMAT=VOTABLE&MAXREC=1",
    "irsa_tap": "https://irsa.ipac.caltech.edu/TAP/availability",
    "jpl_ssd": "https://ssd-api.jpl.nasa.gov/sbdb.api?sstr=433",
    "cadc": "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/availability",
    "mast": "https://mast.stsci.edu/api/v0/invoke",
    "vizier": "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/availability",
}

_PROBE_CACHE = {"t": 0.0, "value": None}
_PROBE_LOCK = threading.Lock()


def _probe_one(name, url, timeout):
    started = time.monotonic()
    try:
        response = requests.get(url, timeout=timeout, stream=True,
                                allow_redirects=True,
                                headers={"User-Agent": "spherex-lens-health/1"})
        status = response.status_code
        response.close()
        up = status < 500
        return name, {"status": "up" if up else "down", "http": status,
                      "latency_ms": round((time.monotonic() - started) * 1000)}
    except requests.RequestException as error:
        return name, {"status": "down", "error": type(error).__name__,
                      "latency_ms": round((time.monotonic() - started) * 1000)}


def probe_archives(force=False):
    """{name: {"status": "up"|"down", ...}} cached for HEALTH_PROBE_TTL_S."""
    now = time.monotonic()
    with _PROBE_LOCK:
        cached = _PROBE_CACHE["value"]
        if (not force and cached is not None
                and now - _PROBE_CACHE["t"] < config.Production.HEALTH_PROBE_TTL_S):
            return cached
        timeout = config.Production.HEALTH_PROBE_TIMEOUT_S
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(ARCHIVE_PROBES)) as pool:
            results = dict(pool.map(lambda item: _probe_one(item[0], item[1], timeout),
                                    ARCHIVE_PROBES.items()))
        checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for value in results.values():
            value["checked_at"] = checked
        _PROBE_CACHE.update(t=now, value=results)
        return results


def snapshot_health():
    """(ok_to_serve, info) for the published Cloud snapshot."""
    import app
    try:
        mode = app._snapshot_mode()
    except Exception as error:
        return False, {"status": "error", "error": type(error).__name__}
    if mode == "off":
        return True, {"status": "disabled",
                      "detail": "snapshot reads are off; live/local dashboards only"}
    if mode == "api":
        return True, {"status": "forwarded", "detail": "reads go to the deployed snapshot API"}
    try:
        store = app._snapshot_store()
        pointer, manifest = app._published_state(store)
    except Exception as error:
        detail = getattr(error, "detail", None)
        return False, {"status": "unavailable",
                       "error": detail if isinstance(detail, str) else type(error).__name__}
    published = (pointer or {}).get("published_at")
    age_h = None
    try:
        published_dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        age_h = round((datetime.now(timezone.utc) - published_dt).total_seconds() / 3600, 2)
    except (TypeError, ValueError):
        pass
    stale = age_h is None or age_h > config.Production.SNAPSHOT_MAX_AGE_H
    return True, {
        "status": "stale" if stale else "ok",
        "published_at": published,
        "age_hours": age_h,
        "max_age_hours": config.Production.SNAPSHOT_MAX_AGE_H,
        "n_fields": len((manifest or {}).get("targets") or {}),
        "manifest": (pointer or {}).get("manifest"),
    }


@router.get("/api/health/live")
def health_live(request: Request = None):
    """Liveness: answers without touching the network or storage.

    ``proxy_signature`` (S8) tells an operator whether *this* request carried a
    valid Netlify signature: "not_configured" (no SPHEREX_PROXY_SECRET on this
    service), "valid", or "missing_or_invalid".  Call it through
    https://<site>/live-api/health/live to check the Netlify side.
    """
    from spherexlens.http_guard import netlify_signature_ok
    sig = netlify_signature_ok(request.headers) if request is not None else None
    return {"ok": True, "pipeline": pipeline_stamp(),
            "uptime_s": round(time.monotonic() - _STARTED, 1),
            "proxy_signature": ("not_configured" if sig is None
                                else "valid" if sig else "missing_or_invalid")}


@router.get("/api/health")
@logged_route("/api/health")
def health(request: Request):
    HEALTH_LIMIT.check(request)
    ok, snapshot = snapshot_health()
    archives = probe_archives()
    payload = {
        "ok": ok,
        "pipeline": pipeline_stamp(),
        "snapshot": snapshot,
        "archives": archives,
        "archives_down": sorted(k for k, v in archives.items() if v.get("status") != "up"),
        "uptime_s": round(time.monotonic() - _STARTED, 1),
    }
    return JSONResponse(payload, status_code=200 if ok else 503,
                        headers={"Cache-Control": "no-store"})
