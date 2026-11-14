"""
app.py

FastAPI backend serving the SPHEREx discovery portal.

Run: uvicorn app:app --reload --port 8000
Then open http://localhost:8000 in your browser.
"""

import os
import json
import re
import hmac
import asyncio
import contextvars
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import numpy as np
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from step2_coadd import coadd_matched, make_target_wcs
from step3_zogy import zogy, find_detections
from step4_triage import triage, PSF_FWHM_ARCSEC
from step5_spectrum import extract_spectrum_from_cutout, three_index_classifier
from step6_injection import run_injection_recovery
from live_ingest import (live_cutouts, describe_live_cutouts,
                         LIVE_DEADLINE_SECONDS)
from step1_ingest import query_sia2, source_fingerprint, target_coordinates
from catalog_tools import (propagate_catalogs, harmonise_motions, target_position, source_pm,
                           propagate, sep_arcsec, mjd_to_year, decode_vsx_type,
                           aavso_url, gaia_distance, wise_colour_class,
                           wise_reliable, mag_to_mjy, surface_sum_to_mjy,
                           CATALOG_EPOCH_MJD, GAIA_DR3_EPOCH_MJD,
                           ALLWISE_EPOCH_MJD)
from snapshot_store import SnapshotStore
import config
from version import pipeline_stamp
from spherexlens.enrich import run_enrichers
from spherexlens.imaging import (dedup_detections, spike_hit, edge_hit,
                                 bright_stars_on_grid, psf_sigma_px)
from catalog_tools import bright_mask_radius

app = FastAPI(title="SPHEREx Discovery Portal API")
# B9: Gzip responses larger than 1 KB.
app.add_middleware(GZipMiddleware, minimum_size=1024)
CORS_ORIGINS = tuple(
    origin.strip().rstrip("/")
    for origin in os.environ.get("SPHEREX_CORS_ORIGINS", "").split(",")
    if origin.strip()
)
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(CORS_ORIGINS),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["X-Snapshot-Object", "X-Snapshot-Manifest"],
    )

def _client_key(request):
    """Rate-limit key for the visitor, not for the proxy in front of us.

    On Cloud Run the TCP peer is Google's front end, and behind the Netlify
    rewrite it is a Netlify edge node, so ``request.client.host`` would put
    every visitor in one shared bucket.  Netlify sets
    ``x-nf-client-connection-ip`` to the real visitor; Cloud Run appends the
    address it accepted the connection from as the last X-Forwarded-For hop.
    The limiter only damps abuse -- the expensive paths also have hard
    concurrency guards -- so a forged header can at worst evade the limit.
    """
    # S8: with SPHEREX_PROXY_SECRET set, the Netlify header is trusted only
    # on requests carrying a valid Netlify signature (X-Nf-Sign).
    from spherexlens.http_guard import trusted_client_ip
    return trusted_client_ip(request.headers, get_remote_address(request))


# N6: per-visitor rate limiting (in-memory, per instance).
# Live pipeline endpoints are most expensive; snapshot reads are cheap.
_limiter = Limiter(key_func=_client_key, default_limits=[])
app.state.limiter = _limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _rate_limited_route(method, path, limit):
    """Register ``func`` as a rate-limited route but return it unwrapped.

    slowapi's wrapper insists on a real starlette Request, so internal callers
    -- the SSE progress streams, the Cloud Run refresh worker and the tests --
    must call the undecorated pipeline function.  Only the HTTP route counts
    against the limit.
    """
    def register(func):
        getattr(app, method)(path)(_limiter.limit(limit)(func))
        return func
    return register

CACHE = os.environ.get("SPHEREX_CACHE", "data_cache")
TARGET_NAME = re.compile(r"[A-Za-z0-9_]{1,80}\Z")

LIVE_BANDS = tuple(os.environ.get(
    "SPHEREX_LIVE_BANDS", "SPHEREx-D1,SPHEREx-D4").split(","))
FIELD_REGISTRY = {}      # name -> meta dict for fields added at runtime
FIELD_DASH_CACHE = {}    # name -> dashboard payload for those fields
FIELD_REQUEST_LIMIT = threading.BoundedSemaphore(
    int(os.environ.get("SPHEREX_FIELD_REQUEST_CONCURRENCY", "2")))
MAX_CUSTOM_FIELDS = int(os.environ.get("SPHEREX_MAX_CUSTOM_FIELDS", "8"))
# Snapshot reads default to the published Cloud snapshot.  When neither the
# bucket nor the snapshot API is reachable, the target list and mosaic fall
# back to the local data_cache so `uvicorn app:app` still works offline.
from snapshot_store import DEFAULT_GCP_PROJECT
SNAPSHOT_BUCKET = (os.environ.get("SPHEREX_SNAPSHOT_BUCKET")
                   or f"{DEFAULT_GCP_PROJECT}-spherex-snapshots")
# How far the previous_manifest chain is offered to the snapshot-history
# control.  Keep the same retention the scheduled refresh uses for garbage
# collection, so every listed manifest still has its dashboard object.
# 336 h = 14 days, i.e. 28 published versions at the 12-hour refresh cadence.
# Raise this together with the refresh job's env var to keep more history.
SNAPSHOT_RETENTION_HOURS = int(
    os.environ.get("SPHEREX_PREVIOUS_SNAPSHOT_RETENTION_HOURS", "336"))
SNAPSHOT_HISTORY_LIMIT = int(
    os.environ.get("SPHEREX_SNAPSHOT_HISTORY_LIMIT", "48"))
_SNAPSHOT_STORE = None
# Where snapshot reads come from:
#   bucket -- read the private GCS bucket directly (needs a Google login with
#             access: the project owner's laptop, Cloud Run).
#   api    -- forward snapshot reads to the deployed snapshot API, like the
#             Netlify site does (no Google login needed: teammates' laptops).
#   auto   -- (default) bucket when this computer can read it, otherwise api.
SNAPSHOT_SOURCE = os.environ.get("SPHEREX_SNAPSHOT_SOURCE", "auto").strip().lower()
SNAPSHOT_API = (os.environ.get("SPHEREX_SNAPSHOT_API")
                or "https://spherex-lens-api-m6cqoypnxa-uc.a.run.app/api").rstrip("/")


def _snapshot_api_is_self():
    """True when SPHEREX_SNAPSHOT_API points at this same Cloud Run service.

    Forwarding a snapshot read to ourselves recurses until Cloud Run's request
    timeout (the visitor sees 504 and the site says "history unavailable").
    This happens when auto mode sees a transient bucket-credential error on the
    snapshot API service and switches to "api" while its API URL is its own.
    """
    host = (urlsplit(SNAPSHOT_API).hostname or "").lower()
    service = (os.environ.get("K_SERVICE") or "").strip().lower()
    return bool(host and service) and (host == service or host.startswith(service + "-"))

_SNAPSHOT_MODE = {"value": None}
_SNAPSHOT_MODE_LOCK = threading.Lock()
_ACCESS_HINT = (
    "This computer has no Google login with access to the Cloud snapshot "
    "bucket. Leave SPHEREX_SNAPSHOT_SOURCE unset (or set it to 'api') to read "
    "through the deployed snapshot API, or run 'gcloud auth "
    "application-default login' with an account that has access.")
_PROGRESS_CALLBACK = contextvars.ContextVar("progress_callback", default=None)
# Per-request record of which immutable snapshot object a response carries.
# The middleware turns it into X-Snapshot-Object / X-Snapshot-Manifest headers
# so the browser's offline copy can verify that what it saved is exactly the
# object the snapshot index names (objects are named by their SHA-256).
_SNAPSHOT_SERVED = contextvars.ContextVar("snapshot_served", default=None)


def _note_snapshot(**fields):
    holder = _SNAPSHOT_SERVED.get()
    if holder is not None:
        holder.update({key: value for key, value in fields.items() if value})


def _same_position(ra1, dec1, ra2, dec2, tolerance_arcsec=1.0):
    """True when two positions agree to within ``tolerance_arcsec``.

    Guards reuse of an IRSA listing: it is only handed on for the position
    it was queried at.
    """
    cosd = np.cos(np.radians(dec2))
    dra = (ra1 - ra2 + 180.0) % 360.0 - 180.0      # wrap across RA 0/360
    return bool(np.hypot(dra * cosd, dec1 - dec2) * 3600.0 < tolerance_arcsec)


def _is_registered_field(target):
    try:
        from step1_ingest import TARGETS
    except Exception:
        return False
    return target in TARGETS


def _check_irsa_listing(store, entry, target):
    """Ask IRSA which SPHEREx products exist for a registered field right now.

    Returns ((ra, dec, rows), irsa_check, unchanged_dashboard).  The third
    value is the published dashboard when IRSA lists exactly the products
    the snapshot processed (same fingerprint as the 12-hour job computes);
    otherwise None and the caller runs the full fresh fetch with ``rows``.
    """
    ra, dec = target_coordinates(target)
    _emit_progress("irsa_query", "Asking IRSA which SPHEREx images exist now")
    rows = query_sia2(ra, dec, radius_deg=0.1, use_cache=False)
    fingerprint = source_fingerprint(rows, ra, dec)
    published_fingerprint = (entry or {}).get("source_fingerprint")
    irsa_check = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "irsa_rows": len(rows),
        "new_since_snapshot": (None if not published_fingerprint
                               else published_fingerprint != fingerprint),
        "reused_snapshot": False,
    }
    unchanged = None
    if (irsa_check["new_since_snapshot"] is False and store is not None
            and entry.get("dashboard") and entry.get("last_check") != "failed"):
        published = store.read_json(entry["dashboard"])
        if published:
            irsa_check.update(
                reused_snapshot=True,
                snapshot_processed_at=(entry.get("last_updated_at")
                                       or published.get("generated_at")))
            unchanged = {**published, "irsa_check": irsa_check}
            _emit_progress(
                "complete",
                f"IRSA lists the same {len(rows)} images the snapshot processed; "
                "showing that result")
    if unchanged is None:
        _emit_progress(
            "irsa_query",
            f"IRSA returned {len(rows)} images"
            + (" including new ones since the snapshot"
               if irsa_check["new_since_snapshot"] else "")
            + "; fetching cutouts")
    return (ra, dec, rows), irsa_check, unchanged
_LOGGER = logging.getLogger(__name__)


def _public_error(prefix, error):
    """Security review S7: log the real exception server-side and return a
    message that names the step and error type only -- no paths, URLs or
    library internals reach the browser."""
    _LOGGER.warning("%s: %s: %s", prefix, type(error).__name__, error)
    return f"{prefix} ({type(error).__name__}); details are in the server log."


@asynccontextmanager
async def _lifespan(application):
    # Check the snapshot connection once so a missing Google login shows up
    # as one clear message at startup instead of a 500 on every request.
    # The server still starts: the page itself and live IRSA requests do not
    # need the bucket.
    try:
        mode = await asyncio.to_thread(_snapshot_mode)
        store = await asyncio.to_thread(_snapshot_store) if mode == "bucket" else None
        if store is not None:
            # Also starts the background warm-up of all 18 fields.
            pointer, _ = await asyncio.to_thread(_published_state, store)
            _LOGGER.warning(
                "Reading Cloud snapshot gs://%s (published %s)",
                SNAPSHOT_BUCKET, (pointer or {}).get("published_at", "never"))
        elif mode == "api":
            status, _, body = await asyncio.to_thread(
                _forward_snapshot_request, "GET", "/api/refresh-status", "")
            published = None
            if status == 200:
                try:
                    published = json.loads(body).get("snapshot_published_at")
                except ValueError:
                    pass
            if published:
                _LOGGER.warning("Snapshot API reachable (snapshot published %s)",
                                published)
            else:
                _LOGGER.error("Snapshot API %s did not answer (HTTP %s); check the "
                              "internet connection", SNAPSHOT_API, status)
    except Exception as error:
        _LOGGER.error(
            "Cannot read Cloud snapshot gs://%s: %s: %s. Run "
            "'gcloud auth application-default login' once on this machine, or "
            "set GOOGLE_APPLICATION_CREDENTIALS.",
            SNAPSHOT_BUCKET, type(error).__name__, str(error)[:200])
    yield

app.router.lifespan_context = _lifespan


async def _snapshot_access_error(request, error):
    if _running_on_cloud_run():
        _LOGGER.error("Bucket access error on %s: %s", request.url.path,
                      type(error).__name__)
        return JSONResponse(status_code=503, content={
            "error": "The server could not read this part of the Cloud snapshot.",
            "hint": "Other pages still work; this is reported to the operators."})
    if SNAPSHOT_SOURCE == "auto":
        _use_snapshot_api(type(error).__name__)
        hint = "Switched to reading through the deployed snapshot API; reload the page."
    else:
        hint = _ACCESS_HINT
    return JSONResponse(status_code=503, content={
        "error": "This computer cannot read the Cloud snapshot bucket.",
        "detail": hint, "hint": hint})


try:
    from google.auth.exceptions import DefaultCredentialsError, RefreshError
    app.add_exception_handler(DefaultCredentialsError, _snapshot_access_error)
    app.add_exception_handler(RefreshError, _snapshot_access_error)
except ImportError:
    pass
try:
    from google.api_core.exceptions import Forbidden, Unauthorized
    app.add_exception_handler(Forbidden, _snapshot_access_error)
    app.add_exception_handler(Unauthorized, _snapshot_access_error)
except ImportError:
    pass

# B9: Brief in-process cache for the current pointer + manifest.  Each
# snapshot read was making two sequential GCS calls (current.json → manifest).
# 10 seconds is enough to collapse a burst of concurrent requests while
# staying fresh for status polling.
_MANIFEST_CACHE: dict = {"t": 0.0, "pointer": None, "manifest": None}


# Catalogue lookups for custom fields run here, alongside the IRSA listing
# and cutout downloads of the same request (they only meet at triage).
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
_CATALOG_POOL = _ThreadPoolExecutor(max_workers=4, thread_name_prefix="catalogs")
_CATALOG_LABELS = {"gaia": "Gaia DR3", "wise": "AllWISE", "allwise": "AllWISE",
                   "vsx": "VSX", "twomass": "2MASS", "2mass": "2MASS",
                   "panstarrs": "Pan-STARRS", "ps1": "Pan-STARRS",
                   "unwise": "unWISE", "simbad": "SIMBAD", "skymapper": "SkyMapper"}


def _catalog_label(key):
    return _CATALOG_LABELS.get(key) or str(key).replace("_", " ").title()


def _start_catalog_lookup(ra, dec):
    """Start Gaia, AllWISE and VSX lookups in the background; returns a future."""
    from step1_ingest import download_catalogs_with_status
    callback = _PROGRESS_CALLBACK.get()

    def report(key, count, state):
        if not callback:
            return
        label = _catalog_label(key)
        text = {"ok": f"{count} sources", "none": "no sources in this field",
                "unavailable": "unavailable (archive did not answer)"}[state]
        callback({"stage": "catalogs", "message": f"Catalogue {label}: {text}",
                  "catalog": key, "count": count, "catalog_state": state})

    return _CATALOG_POOL.submit(download_catalogs_with_status,
                                float(ra), float(dec), progress=report)


def _resolve_catalog_lookup(meta):
    """Wait for a background catalogue lookup (if any) and store its result."""
    future = meta.pop("_catalogs_future", None)
    if future is None:
        return
    if not future.done():
        _emit_progress("catalogs", "Images ready; waiting for the catalogue lookups")
    try:
        meta["catalogs"], meta["catalog_status"] = future.result()
    except Exception as exc:
        print(f"field catalogues failed: {type(exc).__name__}: {exc}", flush=True)
        meta["catalogs"] = {}
        meta["catalog_status"] = {key: "unavailable" for key in _CATALOG_LABELS}
    _emit_progress("catalogs", "Catalogue lookup complete")


def _emit_progress(stage, message, **details):
    callback = _PROGRESS_CALLBACK.get()
    if callback:
        callback({"stage": stage, "message": message, **details})


def _sse_event(event, payload):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)
    return f"event: {event}\ndata: {data}\n\n"


RESULT_ENCODING = "gzip-base64"
# Progress streams are not gzipped by the middleware (event streams must not
# be buffered), so a 2-3 MB dashboard crossed the ocean uncompressed. A client
# that asks with ?result=gzip gets the final result gzip-compressed and
# base64-wrapped inside the stream; it unpacks to exactly the same JSON.
_RESULT_PARAM = Query("", pattern="^(|gzip)$")


def _pack_result(payload):
    import base64
    import gzip
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return {
        "encoding": RESULT_ENCODING,
        "data_packed": base64.b64encode(gzip.compress(raw, compresslevel=6)).decode("ascii"),
        "raw_bytes": len(raw),
    }


def _progress_stream(operation, initial_message, compress=False):
    async def events():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        started = time.monotonic()

        def publish(payload):
            payload = dict(payload)
            payload["elapsed_seconds"] = round(time.monotonic() - started, 1)
            try:
                asyncio.run_coroutine_threadsafe(
                    queue.put(("progress", payload)), loop).result()
            except RuntimeError:
                _LOGGER.debug("Progress stream closed before pipeline completion")

        token = _PROGRESS_CALLBACK.set(publish)

        async def execute():
            try:
                result = await asyncio.to_thread(operation)
                if isinstance(result, JSONResponse):
                    status = result.status_code
                    try:
                        payload = json.loads(result.body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        payload = {"error": "The pipeline returned an invalid response."}
                else:
                    status = 200
                    payload = result
            except HTTPException as error:
                status = error.status_code
                payload = {"error": str(error.detail)}
            except Exception:
                _LOGGER.exception("Live progress request failed unexpectedly")
                status = 500
                payload = {"error": "The live pipeline failed unexpectedly."}
            await queue.put(("result", {"status": status, "data": payload}))

        task = asyncio.create_task(execute())
        try:
            yield _sse_event("progress", {
                "stage": "starting",
                "message": initial_message,
                "elapsed_seconds": 0.0,
            })
            while True:
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event == "result":
                    payload["elapsed_seconds"] = round(time.monotonic() - started, 1)
                    if compress:
                        data = payload.pop("data")
                        payload.update(await asyncio.to_thread(_pack_result, data))
                    yield _sse_event(event, payload)
                    break
                yield _sse_event(event, payload)
            await task
        finally:
            _PROGRESS_CALLBACK.reset(token)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _is_access_error(error):
    """True for 'this computer may not read the bucket' (not network trouble)."""
    try:
        from google.auth import exceptions as auth_errors
        if isinstance(error, (auth_errors.DefaultCredentialsError,
                              auth_errors.RefreshError)):
            return True
    except ImportError:
        pass
    try:
        from google.api_core import exceptions as api_errors
        if isinstance(error, (api_errors.Forbidden, api_errors.Unauthorized)):
            return True
    except ImportError:
        pass
    return False


def _snapshot_reads_enabled():
    return bool(SNAPSHOT_BUCKET) and os.environ.get(
        "SPHEREX_SNAPSHOT_READS", "true").lower() not in {"0", "false", "no"}


def _running_on_cloud_run():
    """Cloud Run sets K_SERVICE.  There the service account *is* the bucket
    reader and SNAPSHOT_API is this very service, so switching to "api" mode
    would make the server forward every request to itself (an infinite loop
    that took the site down on 2026-11-14).  A permission error must then
    fail that one request, never change the mode."""
    return bool(os.environ.get("K_SERVICE"))


# _snapshot_api_is_self() is defined once, near SNAPSHOT_API above (it detects
# "the snapshot API is this Cloud Run service" from the URL and K_SERVICE).


def _use_snapshot_api(reason):
    if _running_on_cloud_run() or _snapshot_api_is_self():
        _LOGGER.error(
            "Bucket access error (%s) on the server itself; NOT switching to "
            "the snapshot API (it would forward to this service). Check the "
            "service account's role on gs://%s.", reason, SNAPSHOT_BUCKET)
        return
    with _SNAPSHOT_MODE_LOCK:
        if _SNAPSHOT_MODE["value"] == "api":
            return
        _SNAPSHOT_MODE["value"] = "api"
    _LOGGER.warning(
        "No access to gs://%s from this computer (%s). Reading the Cloud "
        "snapshot through %s instead; fresh IRSA fetches still run here.",
        SNAPSHOT_BUCKET, reason, SNAPSHOT_API)


def _snapshot_mode():
    """'bucket', 'api' or 'off' (snapshot reads disabled, as in the tests)."""
    global _SNAPSHOT_STORE
    # Never forward to ourselves: a self-loop is a guaranteed 504.  An explicit
    # api/auto setting on the snapshot service itself must read its own bucket.
    if _snapshot_api_is_self():
        with _SNAPSHOT_MODE_LOCK:
            if _SNAPSHOT_MODE["value"] != "bucket":
                _LOGGER.warning(
                    "SPHEREX_SNAPSHOT_API points at this Cloud Run service (%s); "
                    "reading the bucket directly instead of forwarding to itself.",
                    os.environ.get("K_SERVICE"))
            _SNAPSHOT_MODE["value"] = "bucket"
        return "bucket"
    if not _snapshot_reads_enabled():
        return "off"
    if SNAPSHOT_SOURCE == "api":
        return "api"
    if SNAPSHOT_SOURCE == "bucket" or _SNAPSHOT_STORE is not None:
        return _SNAPSHOT_MODE["value"] or "bucket"
    if _SNAPSHOT_MODE["value"]:
        return _SNAPSHOT_MODE["value"]
    # auto: one cheap read decides whether this computer may use the bucket.
    try:
        store = SnapshotStore(SNAPSHOT_BUCKET)
        store.read_current()
    except Exception as error:
        if _is_access_error(error) and not _running_on_cloud_run():
            _use_snapshot_api(type(error).__name__)
            return "api"
        return "bucket"           # e.g. no network: decide on a later request
    with _SNAPSHOT_MODE_LOCK:
        _SNAPSHOT_STORE = _SNAPSHOT_STORE or store
        _SNAPSHOT_MODE["value"] = _SNAPSHOT_MODE["value"] or "bucket"
    return _SNAPSHOT_MODE["value"]


def _snapshot_store():
    """The bucket store, or None when snapshot reads go through the API (or are off)."""
    global _SNAPSHOT_STORE
    if _snapshot_mode() != "bucket":
        return None
    if _SNAPSHOT_STORE is None:
        try:
            _SNAPSHOT_STORE = SnapshotStore(SNAPSHOT_BUCKET)
        except Exception as error:
            if _is_access_error(error):
                raise HTTPException(status_code=503, detail=_ACCESS_HINT) from error
            raise
    return _SNAPSHOT_STORE


def _published_manifest(store):
    _, manifest = _published_state(store)
    return manifest


def _published_state(store):
    # B9: serve from the 10-second RAM cache to avoid two GCS round-trips
    # on every snapshot read request.
    now = time.monotonic()
    if (now - _MANIFEST_CACHE["t"] < 10.0
            and _MANIFEST_CACHE["pointer"] is not None):
        pointer = _MANIFEST_CACHE["pointer"]
        manifest = _MANIFEST_CACHE["manifest"]
        if manifest:
            return pointer, manifest
    pointer, _ = store.read_current()
    if not pointer:
        raise HTTPException(
            status_code=503,
            detail="No published snapshot is available yet.")
    manifest = store.read_manifest(pointer)
    if not manifest:
        raise HTTPException(
            status_code=503,
            detail="The published snapshot manifest is unavailable.")
    _MANIFEST_CACHE.update({"t": now, "pointer": pointer, "manifest": manifest})
    _warm_snapshot_cache(store, pointer, manifest)
    return pointer, manifest


_WARMED_MANIFESTS = set()
_WARM_LOCK = threading.Lock()


def _warm_snapshot_cache(store, pointer, manifest):
    """Read every object of a newly seen snapshot into memory, in the background.

    The objects are immutable, so this only moves the first GCS read off the
    visitor's click; it never changes what is served.
    """
    if os.environ.get("SPHEREX_SNAPSHOT_WARM", "true").lower() in {"0", "false", "no"}:
        return
    manifest_name = (pointer or {}).get("manifest")
    with _WARM_LOCK:
        if not manifest_name or manifest_name in _WARMED_MANIFESTS:
            return
        _WARMED_MANIFESTS.add(manifest_name)
    names = [manifest.get("mosaic"), manifest.get("targets_object")]
    for entry in (manifest.get("targets") or {}).values():
        names += [entry.get(key) for key in
                  ("dashboard", "recovery", "neowise_object", "meta_object")]

    def read(name):
        try:
            store.read_bytes(name)
            return True
        except Exception as error:          # a later request retries the read
            _LOGGER.debug("Snapshot warm-up skipped %s: %s", name, error)
            return False

    def warm():
        import concurrent.futures
        started = time.monotonic()
        # Dashboards first: they are what a visitor clicks.
        ordered = sorted(filter(None, names),
                         key=lambda name: not name.startswith("snapshots/"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            loaded = sum(pool.map(read, ordered))
        _LOGGER.warning("Snapshot cache warmed: %d objects in %.1f s",
                        loaded, time.monotonic() - started)

    threading.Thread(target=warm, name="snapshot-warm", daemon=True).start()


def _optional_published_manifest(store):
    # B9: same cache path for optional reads (live requests).
    now = time.monotonic()
    if (now - _MANIFEST_CACHE["t"] < 10.0
            and _MANIFEST_CACHE["pointer"] is not None):
        return _MANIFEST_CACHE["manifest"]
    pointer, _ = store.read_current()
    manifest = store.read_manifest(pointer) if pointer else None
    if pointer:
        _MANIFEST_CACHE.update({"t": now, "pointer": pointer, "manifest": manifest})
        if manifest:
            _warm_snapshot_cache(store, pointer, manifest)
    return manifest


def _published_json(store, object_name, description):
    payload = store.read_json(object_name) if object_name else None
    if payload is None:
        raise HTTPException(
            status_code=503,
            detail=f"The published {description} is unavailable.")
    return payload


def _published_object_response(store, object_name, description, manifest=None):
    """A published object returned unchanged, labelled with its object name."""
    payload = _published_json(store, object_name, description)
    _note_snapshot(object=object_name, manifest=manifest)
    return payload


def _snapshot_chain(store, pointer, manifest, limit=None):
    """Published manifests, newest first, following ``previous_manifest``.

    Each item is ``(manifest_object, manifest, published_at)``.  The current
    pointer carries the real publication time; older manifests only retain the
    ``created_at`` they were written with.  The chain is bounded by the same
    retention window the refresh job uses for garbage collection, so a listed
    snapshot still has its dashboard object.
    """
    entries = []
    seen = set()
    manifest_object = pointer.get("manifest") if pointer else None
    current = manifest
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=SNAPSHOT_RETENTION_HOURS))
    limit = limit or SNAPSHOT_HISTORY_LIMIT
    while current and manifest_object and manifest_object not in seen:
        if len(entries) >= limit:
            break
        created_at = current.get("created_at")
        if entries:                       # the current manifest is always shown
            try:
                if created_at and datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")) < cutoff:
                    break
            except (TypeError, ValueError):
                pass                      # an unparseable time is kept, not lost
        seen.add(manifest_object)
        entry_published_at = created_at
        if not entries and pointer:
            entry_published_at = pointer.get("published_at") or created_at
        entries.append((manifest_object, current, entry_published_at))
        manifest_object = current.get("previous_manifest")
        current = (store.read_json(manifest_object)
                   if manifest_object else None)
    return entries


def _snapshot_history_payload(store, target):
    """Every retained published dashboard for ``target``, newest first."""
    pointer, manifest = _published_state(store)
    snapshots = []
    for manifest_object, snap_manifest, published_at in _snapshot_chain(
            store, pointer, manifest):
        entry = (snap_manifest.get("targets") or {}).get(target) or {}
        dashboard_object = entry.get("dashboard")
        if not dashboard_object:
            continue
        snapshots.append({
            "manifest": manifest_object,
            "dashboard": dashboard_object,
            "recovery": entry.get("recovery"),
            "published_at": published_at,
            "created_at": snap_manifest.get("created_at") or published_at,
            "last_check": entry.get("last_check"),
            "last_updated_at": entry.get("last_updated_at"),
            "latest": manifest_object == pointer.get("manifest"),
        })
    _note_snapshot(manifest=pointer.get("manifest"))
    for index, snapshot in enumerate(reversed(snapshots)):
        snapshot["order"] = index             # 0 = oldest (playback order)
    return {
        "schema_version": 1,
        "target": target,
        "latest": pointer.get("manifest"),
        "published_at": pointer.get("published_at"),
        "retention_hours": SNAPSHOT_RETENTION_HOURS,
        "snapshots": snapshots,
    }


_SNAPSHOT_GET_PATHS = {
    "/api/targets", "/api/status", "/api/refresh-status", "/api/snapshot-index",
    "/api/snapshot-history", "/api/dashboard", "/api/recovery", "/api/mosaic",
}
_FORWARDED_HEADERS = ("X-Snapshot-Object", "X-Snapshot-Manifest", "Retry-After")
_SNAPSHOT_API_SESSION = requests.Session()


def _forward_snapshot_request(method, path, query, token=None):
    """One snapshot request to the deployed API: (status, headers, body)."""
    url = SNAPSHOT_API + path[len("/api"):] + (f"?{query}" if query else "")
    headers = {"X-Refresh-Token": token} if token else {}
    try:
        response = _SNAPSHOT_API_SESSION.request(
            method, url, headers=headers, timeout=(10, 60), allow_redirects=False)
    except requests.RequestException as error:
        body = json.dumps({
            "error": "Cannot reach the Cloud snapshot API.",
            "detail": f"{type(error).__name__} while contacting {SNAPSHOT_API}",
            "hint": "Check the internet connection, then reload.",
        })
        return 503, {"Content-Type": "application/json"}, body.encode()
    out = {"Content-Type": response.headers.get("Content-Type", "application/json")}
    out.update({h: response.headers[h] for h in _FORWARDED_HEADERS if h in response.headers})
    return response.status_code, out, response.content


def _should_forward(request):
    path = request.url.path
    if request.method == "POST":
        return path == "/api/refresh"
    if request.method != "GET" or path not in _SNAPSHOT_GET_PATHS:
        return False
    if request.query_params.get("source", "").lower() == "live":
        return False                     # fresh IRSA work runs on this computer
    target = request.query_params.get("target")
    return not (path in {"/api/dashboard", "/api/recovery"} and target in FIELD_REGISTRY)


@app.middleware("http")
async def disable_live_response_caching(request: Request, call_next):
    served = {}
    token = _SNAPSHOT_SERVED.set(served)
    try:
        response = await call_next(request)
    finally:
        _SNAPSHOT_SERVED.reset(token)
    if served.get("object"):
        response.headers["X-Snapshot-Object"] = served["object"]
    if served.get("manifest"):
        response.headers["X-Snapshot-Manifest"] = served["manifest"]
    is_live_pipeline = (
        request.url.path in {
            "/api/dashboard", "/api/recovery",
            "/api/dashboard/stream", "/api/recovery/stream",
            "/api/field/stream",
        }
        and request.query_params.get("source", "").lower() == "live"
    ) or request.url.path in {
        "/api/dashboard/stream", "/api/recovery/stream", "/api/field/stream",
        "/api/mosaic/tile/stream",
    }
    is_live_mosaic = (
        request.url.path == "/api/mosaic"
        and request.query_params.get("source", "").lower() == "live"
    )
    is_snapshot_read = (
        bool(SNAPSHOT_BUCKET)
        and request.url.path in {
            "/api/targets", "/api/status", "/api/refresh-status",
            "/api/snapshot-index", "/api/snapshot-history",
            "/api/dashboard", "/api/recovery", "/api/mosaic"
        }
        and request.query_params.get("source", "").lower() != "live"
    )
    if (is_live_pipeline or is_live_mosaic or is_snapshot_read
            or request.url.path in {
                "/api/field", "/api/field/stream", "/api/refresh",
                "/api/refresh-status",
            }):
        response.headers["Cache-Control"] = "no-store, no-cache, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.middleware("http")
async def forward_snapshot_reads(request: Request, call_next):
    """Without bucket access, serve snapshot reads from the deployed API."""
    if not _should_forward(request):
        return await call_next(request)
    if await asyncio.to_thread(_snapshot_mode) != "api":
        return await call_next(request)
    status, headers, body = await asyncio.to_thread(
        _forward_snapshot_request, request.method, request.url.path,
        request.url.query, request.headers.get("X-Refresh-Token"))
    headers["Cache-Control"] = "no-store, no-cache, max-age=0"
    return Response(content=body, status_code=status, headers=headers)


def load_target(target):
    if not isinstance(target, str) or not TARGET_NAME.fullmatch(target):
        return None
    if target in FIELD_REGISTRY:
        return FIELD_REGISTRY[target]
    meta_path = os.path.join(CACHE, f"{target}_meta.json")
    if not os.path.exists(meta_path):
        return None
    with open(meta_path) as f:
        meta = json.load(f)
    meta["paths"] = [_normalize_data_path(path) for path in meta.get("paths", [])]
    for source in meta.get("sources", []):
        if isinstance(source, dict) and isinstance(source.get("path"), str):
            source["path"] = _normalize_data_path(source["path"])
    return meta


def _normalize_data_path(path):
    return (os.path.normpath(path.replace("\\", os.sep))
            if isinstance(path, str) else path)


# ---------------------------------------------------------------------------
# Provenance, statistics and map overlays
# ---------------------------------------------------------------------------
def significance_histogram(S, bins=32):
    """
    Histogram of the significance map, in sigma.

    A healthy difference image is a narrow noise peak around zero plus a long
    tail of real sources; the shape is the quickest way to see whether the
    subtraction worked.
    """
    values = np.asarray(S, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"bins": [], "counts": [], "edges": []}
    thr = float(config.DETECTION_SIGMA)
    # both tails reach past the detection threshold, so fading (negative)
    # detections are visible too, not clipped into the edge bin
    lo, hi = -(thr + 2.0), max(thr + 2.0, float(np.percentile(values, 99.9)))
    counts, edges = np.histogram(np.clip(values, lo, hi), bins=bins,
                                 range=(lo, hi))
    return {
        "edges": [round(float(e), 2) for e in edges],
        "counts": [int(c) for c in counts],
        "n_pixels": int(values.size),
        "threshold": thr,          # the UI highlights |S| beyond this
    }


ALL_BANDS = tuple(f"SPHEREx-D{i}" for i in range(1, 7))


class CoverageError(RuntimeError):
    """The release has no like-for-like epoch pair here (not a network fault)."""


def shared_band_epochs(cutouts):
    """How many epochs contain a band that at least one other epoch also has."""
    by_band = {}
    for c in cutouts:
        by_band.setdefault(c.get("band"), set()).add(c.get("epoch"))
    epochs = set()
    for eps in by_band.values():
        if len(eps) >= 2:
            epochs |= eps
    return len(epochs)


def epoch_coverage_error(epochs, bands):
    """Explain why these exposures cannot form a like-for-like epoch pair."""
    if len(epochs) != len(bands):
        return "exposure epoch and detector metadata do not align"
    epoch_groups = {}
    for epoch, band in zip(epochs, bands):
        epoch_groups.setdefault(epoch, set()).add(band)
    if len(epoch_groups) < 2:
        return (f"only {len(epoch_groups)} observing epoch(s) are available; "
                "at least two are required")
    common_bands = set.intersection(*epoch_groups.values())
    if not common_bands:
        available = {str(epoch): sorted(values)
                     for epoch, values in epoch_groups.items()}
        return (f"no detector band is observed in every epoch "
                f"({available}); comparing different bands would measure colour, "
                "not change")
    return None


def _resolve_epochs(epochs, n_sources):
    """Use recorded epochs; infer two halves only for legacy metadata with none."""
    if epochs is not None and len(epochs) == n_sources and all(e is not None for e in epochs):
        return list(epochs)
    half = n_sources // 2
    return ([1] * max(1, half) + [2] * max(1, n_sources - half))[:n_sources]


def live_exposures(target, meta, size_deg=0.2, epoch_mode="firstlast",
                  max_slots=0, bands=LIVE_BANDS, progress=None, rows=None):
    """
    Fetch the current SIA2 listing and cutouts from IRSA into memory (no cache).

    Returns (cutouts, provenance, False).  The third value is retained for API
    compatibility; live requests are never served from a local RAM cache.
    """
    rejected = []
    rejected_lock = threading.Lock()

    def report_progress(event):
        if event.get("outcome") == "rejected":
            with rejected_lock:
                rejected.append(event["message"])
        if progress:
            progress(event)

    ra, dec = meta["meta"]["ra"], meta["meta"]["dec"]
    deadline = time.monotonic() + LIVE_DEADLINE_SECONDS
    # One fresh IRSA listing per request: reuse the caller's (it just asked
    # IRSA for this position) and share it with the all-detector retry below.
    if rows is None:
        if progress:
            progress({"stage": "irsa_query", "message": "Querying IRSA SIA2"})
        rows = query_sia2(ra, dec, radius_deg=0.1, use_cache=False)

    def fetch(band_set):
        return live_cutouts(ra, dec,
                            size_deg=size_deg, bands=band_set,
                            epoch_mode=epoch_mode,
                            max_slots=max_slots or None,
                            log=lambda msg: print(msg, flush=True),
                            progress=report_progress,
                            rows=rows, deadline=deadline)

    cutouts = fetch(bands)
    if shared_band_epochs(cutouts) < 2 and tuple(bands) != tuple(ALL_BANDS):
        # The default pair (D1, D4) may simply not be observed in some epoch
        # (Sgr A*: epoch 2 has neither) -- try every detector before giving up.
        print(f"live: {target}: bands {list(bands)} do not cover two epochs; "
              "retrying with all six detectors", flush=True)
        if progress:
            progress({
                "stage": "irsa_query",
                "message": "Selected detectors do not span two epochs; retrying all six",
            })
        cutouts = fetch(ALL_BANDS)
    if shared_band_epochs(cutouts) < 2:
        per_epoch = {}
        for c in cutouts:
            per_epoch.setdefault(c.get("epoch"), []).append(c.get("band"))
        rejection_detail = ""
        if rejected:
            reasons = "; ".join(dict.fromkeys(rejected[-4:]))
            rejection_detail = f" IRSA cutout validation rejected: {reasons}."
        raise CoverageError(
            "no usable detector band spans two epochs, so there is nothing "
            f"like-for-like to subtract (usable bands per epoch: {per_epoch or 'none'})."
            f"{rejection_detail} "
            "This is a coverage limit of the SPHEREx release, not a network error.")
    provenance = describe_live_cutouts(cutouts)
    return cutouts, provenance, False


def exposure_provenance(meta, target_wcs, npix=180):
    """
    One row per downloaded exposure: where the file came from, which detector
    and epoch it belongs to, and the wavelength at the target position.

    Uses the `sources` block written by step1_ingest.py (archive URL, obs_id,
    start time) and falls back to the FITS headers for older caches.
    """
    from astropy.io import fits
    from astropy.time import Time
    from step5_spectrum import wavelength_at

    ra, dec = meta["meta"]["ra"], meta["meta"]["dec"]
    listed = {s.get("path"): s for s in meta.get("sources", []) if s}
    rows = []

    for path, epoch in zip(meta["paths"], meta.get("epochs", [None] * len(meta["paths"]))):
        info = dict(listed.get(path, {}))
        info.setdefault("path", path)
        info.setdefault("file", os.path.basename(path))
        info.setdefault("epoch", epoch)
        info.setdefault("collection", meta.get("collection", config.SPHEREX_COLLECTION))

        try:
            with fits.open(path) as hdul:
                hdu = hdul["IMAGE"] if "IMAGE" in hdul else hdul[0]
                header = hdu.header
                info.setdefault("obs_id", header.get("OBSID"))
                detector = header.get("DETECTOR")
                if detector is not None and not info.get("detector"):
                    info["detector"] = f"D{detector}"
                info.setdefault("exptime_s", header.get("EXPTIME") or header.get("TSAMP"))
                info.setdefault("bunit", header.get("BUNIT", "MJy/sr").replace(" ", ""))
                shape = hdu.data.shape
                info["shape"] = list(shape)
                if not info.get("bytes"):
                    info["bytes"] = os.path.getsize(path)
            lam, band = wavelength_at(path, ra, dec)
            info["wavelength_um"] = lam
            info["bandwidth_um"] = band
        except Exception as exc:                      # keep the row, note the problem
            info["error"] = f"{type(exc).__name__}: {exc}"

        if info.get("mjd") and not info.get("date"):
            try:
                info["date"] = Time(float(info["mjd"]), format="mjd").iso[:10]
            except Exception:
                pass
        rows.append(info)

    return rows


def source_magnitude(src):
    """Best available magnitude for a catalogue row, whatever catalogue it is.

    The three built-in catalogues use gmag/w1, but a field may also carry an
    extra catalogue (2MASS, Pan-STARRS, ...) with other column names.  Without
    this fallback those rows would have no magnitude and would be cut from the
    map overlay before the brighter built-in entries.
    """
    for key in ("gmag", "w1", "w2", "mag", "rmag", "imag", "zmag",
                "jmag", "hmag", "kmag", "w1mpro", "phot_g_mean_mag"):
        value = (src or {}).get(key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(value) and value < 90:
            return value
    return None


def catalog_overlay(meta, target_wcs, npix=180):
    """
    Catalog positions inside the field, converted to target-plane pixels so the
    frontend can draw them on top of the map.
    """
    out = []
    cats = meta.get("catalogs", {})
    for cat_name, sources in cats.items():
        for src in sources or []:
            s_ra, s_dec = src.get("ra"), src.get("dec")
            if s_ra is None or s_dec is None:
                continue
            try:
                x, y = target_wcs.world_to_pixel_values(s_ra, s_dec)
            except Exception:
                continue
            x, y = float(x), float(y)
            if not (0 <= x < npix and 0 <= y < npix):
                continue
            out.append({
                "cat": cat_name,
                "x": round(x, 1),
                "y": round(y, 1),
                "ra": s_ra,
                "dec": s_dec,
                "mag": source_magnitude(src),
                "label": src.get("name") or src.get("type"),
            })
    # Cap the drawn markers, but per catalogue: a plain sort-by-name-then-cut
    # let ~600 in-field Gaia stars ("g" < "v" < "w") crowd out every VSX
    # variable and AllWISE source. VSX is small and the most interesting, so
    # it is never cut; the rest keep their brightest entries. Catalogues that
    # are not built in get a generous default so an added catalogue is drawn.
    caps = {"vsx": None, "gaia": 500, "wise": 300}
    out.sort(key=lambda r: (r["cat"], r["mag"] if r["mag"] is not None else 99))
    kept, seen = [], {}
    for r in out:
        n = seen.get(r["cat"], 0)
        cap = caps.get(r["cat"], 1000)
        if cap is None or n < cap:
            kept.append(r)
            seen[r["cat"]] = n + 1
    return kept


def build_field_objects(meta, detections, target_wcs, npix=180,
                        match_arcsec=30.0, dedup_arcsec=2.0):
    """
    Merge the whole catalogue and our detections into one object list.

    Each row is one physical object in the 9' field and carries a verdict:

    * ``KNOWN``     -- the object is in at least one catalogue (Gaia DR3, VSX,
                       AllWISE).  ``detected`` tells you whether SPHEREx also
                       saw it change; ``snr`` is that significance.
    * ``CANDIDATE`` -- the object is *not* in any catalogue but SPHEREx did
                       detect a change there: the follow-up list.

    Catalogue entries appearing in several catalogues (a Gaia star that is also
    a VSX variable) are merged into one row whose ``catalogs`` list names all of
    them.
    """
    objects = []

    def to_pixels(ra, dec, require_inside=False):
        try:
            x, y = target_wcs.world_to_pixel_values(ra, dec)
        except Exception:
            return None
        x, y = float(x), float(y)
        if require_inside and not (0 <= x < npix and 0 <= y < npix):
            return None
        return x, y

    # Spatial hash for the twin search: cells one dedup radius wide, so a twin
    # can only sit in the same or a neighbouring cell. The old linear scan
    # was O(N^2) -- ~10^9 comparisons for a 20k Gaia + 20k AllWISE field.
    cell = dedup_arcsec / 3600.0
    grid = {}

    def cell_of(ra, dec):
        return int(np.floor(ra / cell)), int(np.floor(dec / cell))

    def find_twin(ra, dec):
        # Nearest row inside the dedup box. (The old scan took the first row
        # in list order, so in crowded fields like Orion a VSX variable within
        # 2" of two Gaia stars could attach to the farther one.)
        ci, cj = cell_of(ra, dec)
        best, best_d2 = None, None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for obj in grid.get((ci + di, cj + dj), ()):
                    dra, ddec = obj["ra"] - ra, obj["dec"] - dec
                    if abs(dra) < cell and abs(ddec) < cell:
                        d2 = (dra * np.cos(np.radians(dec))) ** 2 + ddec ** 2
                        if best_d2 is None or d2 < best_d2:
                            best, best_d2 = obj, d2
        return best

    # --- catalogue entries ------------------------------------------------
    for cat_name, sources in (meta.get("catalogs") or {}).items():
        for src in sources or []:
            s_ra, s_dec = src.get("ra"), src.get("dec")
            if s_ra is None or s_dec is None:
                continue
            pix = to_pixels(s_ra, s_dec)
            if pix is None:
                continue
            x, y = pix
            in_field = (0 <= x < npix and 0 <= y < npix)

            # merge with an existing row for the same star
            twin = find_twin(s_ra, s_dec)
            extras = object_extras(cat_name, src)
            if twin is not None:
                if cat_name not in twin["catalogs"]:
                    twin["catalogs"].append(cat_name)
                for k, v in extras.items():
                    if twin.get(k) is None:
                        twin[k] = v
                if cat_name == "vsx" and src.get("type"):
                    twin["type"] = src.get("type")
                if twin.get("mag") is None:
                    twin["mag"] = source_magnitude(src)
                if src.get("name"):
                    twin["name"] = twin.get("name") or src.get("name")
                continue

            objects.append({
                "ra": s_ra,
                "dec": s_dec,
                "x": round(x, 1),
                "y": round(y, 1),
                "mag": source_magnitude(src),
                "in_field": in_field,
                "catalogs": [cat_name],
                "name": src.get("name"),
                "type": src.get("type"),
                "verdict": "KNOWN",
                "detected": False,
                "snr": None,
                "sep_arcsec": None,
                "kind": None,
                "pair": None,
                "category": None,
                **extras,
            })
            grid.setdefault(cell_of(s_ra, s_dec), []).append(objects[-1])

    # --- detections: attach to a catalogue row, or add as CANDIDATE -------
    def _sep(a_ra, a_dec, b_ra, b_dec):
        cosd = np.cos(np.radians(a_dec))
        return float(np.hypot((a_ra - b_ra) * cosd, a_dec - b_dec) * 3600.0)

    for det in detections:
        d_ra, d_dec = det.get("ra"), det.get("dec")
        if d_ra is None or d_dec is None:
            continue
        best = None
        best_sep = match_arcsec
        # Triage already decided whether a catalogue entry explains this
        # detection; a CANDIDATE must not be re-attached to a row here.
        if det.get("verdict") != "CANDIDATE":
            for obj in objects:
                s = _sep(d_ra, d_dec, obj["ra"], obj["dec"])
                if s < best_sep:
                    best, best_sep = obj, s

        if best is not None:
            if (best.get("snr") or 0) < det["snr"]:
                best["snr"] = round(float(det["snr"]), 2)
                best["sep_arcsec"] = round(best_sep, 2)
                best["kind"] = det.get("kind")
                best["pair"] = det.get("pair")
                best["category"] = det.get("category")
            best["detected"] = True
            continue

        pix = to_pixels(d_ra, d_dec, require_inside=True)
        if pix is None:
            continue
        objects.append({
            "ra": d_ra,
            "dec": d_dec,
            "x": round(pix[0], 1),
            "y": round(pix[1], 1),
            "mag": None,
            "in_field": True,
            "catalogs": [],
            "name": None,
            "type": None,
            "verdict": "CANDIDATE",
            "detected": True,
            "snr": round(float(det["snr"]), 2),
            "sep_arcsec": 0.0,
            "kind": det.get("kind"),
            "pair": det.get("pair"),
            "category": det.get("category", "CANDIDATE"),
        })

    objects.sort(key=lambda o: (not o["detected"],
                                -(o.get("snr") or 0),
                                o.get("mag") if o.get("mag") is not None else 99))
    return objects


def object_extras(cat, src):
    """Per-catalogue facts shown on hover / in the catalogue table."""
    out = {}
    if src.get("moved_arcsec"):
        out["moved_arcsec"] = src["moved_arcsec"]
    if cat == "vsx":
        label, family = decode_vsx_type(src.get("type"))
        out.update({"vsx_label": label, "vsx_family": family,
                    "period_d": src.get("period_d"),
                    "aavso_url": aavso_url(src.get("oid"))})
    elif cat == "gaia":
        dist = gaia_distance(src)
        if dist:
            out.update({"dist_pc": dist["distance_pc"], "nearby": dist["nearby"]})
        if src.get("var_flag") == "VARIABLE":
            out["gaia_variable"] = True
    elif cat == "wise":
        colour, cls = wise_colour_class(src.get("w1"), src.get("w2"))
        if colour is not None:
            out.update({"w1_w2": colour, "colour_class": cls})
        if not wise_reliable(src):
            out["wise_artefact"] = True
    return {k: v for k, v in out.items() if v is not None}


# ---------------------------------------------------------------------------
# Epochs, target motion and catalogue science (see catalog_tools.py)
# ---------------------------------------------------------------------------
EXTENDED_TYPES = ("Galaxy", "Nebula", "Supernova", "Cluster", "Galactic")


def target_motion_block(target, meta):
    """
    The target's ra/dec/pm/pos_epoch. Caches written before the moving
    targets got motions fall back to step1_ingest.TARGETS, so the dashboard
    follows the star without a re-ingest.
    """
    block = dict(meta["meta"])
    if block.get("pm_ra") or block.get("pm_dec"):
        return block
    try:
        from step1_ingest import TARGETS
        ref = TARGETS.get(target, {})
    except Exception:
        ref = {}
    if ref.get("pm_ra") or ref.get("pm_dec"):
        block.update({k: ref[k] for k in ("ra", "dec", "pm_ra", "pm_dec",
                                          "pos_epoch") if k in ref})
    return block


def exposure_bands(meta, sources, live_cutouts_list=None):
    """Detector band of every exposure ('SPHEREx-D4'), for matched masking."""
    import re
    if live_cutouts_list is not None:
        return [c.get("band") or f"exp{i}" for i, c in enumerate(live_cutouts_list)]
    by_path = {s.get("path"): s.get("band") for s in meta.get("sources", []) if s}
    out = []
    for i, p in enumerate(sources):
        band = by_path.get(p)
        if not band:
            m = re.search(r"D(\d)_spx", os.path.basename(str(p)))
            band = f"SPHEREx-D{m.group(1)}" if m else _detector_band_from_fits(p)
        out.append(band or f"exp{i}")
    return out


def _detector_band_from_fits(path):
    """Read DETECTOR from a cutout when the metadata/filename does not say.

    Synthetic demo cutouts (make_demo_data.py) are named demo_<target>_00.fits,
    so the filename carries no detector.  Their IMAGE header does, and without
    this the shared-band check saw four invented bands (exp0..exp3) and
    refused to subtract two epochs that really do share D3/D4.
    """
    try:
        from astropy.io import fits
        with fits.open(path, memmap=False) as hdul:
            for hdu in hdul:
                detector = hdu.header.get("DETECTOR") or hdu.header.get("DETECT")
                if detector in (None, ""):
                    continue
                detector = str(detector).strip()
                return f"SPHEREx-D{detector}" if detector.isdigit() else detector
    except Exception:
        return None
    return None


def exposure_mjds(meta, paths, live_cutouts_list=None):
    """MJD of every exposure, aligned with the list the pipeline runs on."""
    if live_cutouts_list is not None:
        return [c.get("mjd") for c in live_cutouts_list]
    by_path = {s.get("path"): s.get("mjd") for s in meta.get("sources", []) if s}
    return [by_path.get(p) for p in paths]


def aperture_flux(img, x, y, sigma, r=2.0, r_in=4.0, r_out=7.0):
    """Background-subtracted aperture sum on a target-plane map, with error."""
    ny, nx = img.shape
    x0, x1 = int(max(0, x - r_out - 1)), int(min(nx, x + r_out + 2))
    y0, y1 = int(max(0, y - r_out - 1)), int(min(ny, y + r_out + 2))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None, None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.hypot(xx - x, yy - y)
    cut = img[y0:y1, x0:x1]
    ap, ann = (d <= r) & np.isfinite(cut), (d > r_in) & (d <= r_out) & np.isfinite(cut)
    if ap.sum() == 0 or ann.sum() < 5:
        return None, None
    flux = float(np.sum(cut[ap]) - np.median(cut[ann]) * ap.sum())
    return flux, float(sigma * np.sqrt(ap.sum()))


MASK_RADIUS_PX = 2          # 6" on the 3" grid: the detection core


def masked_near(hole_map, py, px, radius=MASK_RADIUS_PX):
    """Max number of exposures masked at any pixel within `radius` of (py, px)."""
    if hole_map is None:
        return 0
    ny, nx = hole_map.shape
    y0, y1 = max(0, int(py) - radius), min(ny, int(py) + radius + 1)
    x0, x1 = max(0, int(px) - radius), min(nx, int(px) + radius + 1)
    if y1 <= y0 or x1 <= x0:
        return 0
    return int(hole_map[y0:y1, x0:x1].max())


def delta_mag(img_a, img_b, sigma_a, sigma_b, x, y):
    """V3: SPHEREx brightness change of a monopole between two epochs."""
    fa, ea = aperture_flux(img_a, x, y, sigma_a)
    fb, eb = aperture_flux(img_b, x, y, sigma_b)
    if None in (fa, fb, ea, eb) or fa <= 3 * ea or fb <= 3 * eb:
        return None
    return round(-2.5 * np.log10(fb / fa), 3)


def astrometry_check(img, wcs, gaia, sigma, npix=180, max_stars=150):
    """
    G7: centroid unsaturated, isolated Gaia stars (already propagated to the
    epoch of `img`) and report the median offset of the SPHEREx frame.
    """
    if not gaia or not sigma:
        return None
    usable = [s for s in gaia if 10.0 <= (s.get("gmag") or 99) <= 16.0]
    if len(usable) < 3:
        return None
    usable = sorted(usable, key=lambda s: s["gmag"])[:max_stars * 3]
    all_ra = np.array([s["ra"] for s in gaia])
    all_dec = np.array([s["dec"] for s in gaia])
    all_g = np.array([s.get("gmag") or 99 for s in gaia])

    offsets = []
    for s in usable:
        try:
            x, y = wcs.world_to_pixel_values(s["ra"], s["dec"])
        except Exception:
            continue
        x, y = float(x), float(y)
        if not (5 <= x < npix - 5 and 5 <= y < npix - 5):
            continue
        # isolation: no Gaia star within 2 mag of it inside 12"
        cosd = np.cos(np.radians(s["dec"]))
        near = np.hypot((all_ra - s["ra"]) * cosd, all_dec - s["dec"]) * 3600 < 12
        if np.sum(near & (all_g < s["gmag"] + 2)) > 1:
            continue
        ix, iy = int(round(x)), int(round(y))
        box = img[iy - 3:iy + 4, ix - 3:ix + 4]
        if box.shape != (7, 7) or not np.all(np.isfinite(box)):
            continue
        border = np.concatenate([box[0], box[-1], box[1:-1, 0], box[1:-1, -1]])
        sub = box - np.median(border)
        if sub.max() < 10 * sigma:
            continue
        py, px = np.unravel_index(np.argmax(sub), sub.shape)
        if abs(py - 3) > 1 or abs(px - 3) > 1:
            continue
        w = np.clip(sub, 0, None)
        gy, gx = np.mgrid[-3:4, -3:4]
        cx, cy = ix + float(np.sum(w * gx) / w.sum()), iy + float(np.sum(w * gy) / w.sum())
        ra_c, dec_c = wcs.pixel_to_world_values(cx, cy)
        offsets.append(((float(ra_c) - s["ra"]) * cosd * 3600,
                        (float(dec_c) - s["dec"]) * 3600))
        if len(offsets) >= max_stars:
            break
    if len(offsets) < 3:
        return {"n_stars": len(offsets),
                "note": "too few isolated, unsaturated Gaia stars (G 10-16)"}
    d = np.array(offsets)
    total = np.hypot(d[:, 0] - np.median(d[:, 0]), d[:, 1] - np.median(d[:, 1]))
    return {"n_stars": len(offsets),
            "median_dra_arcsec": round(float(np.median(d[:, 0])), 2),
            "median_ddec_arcsec": round(float(np.median(d[:, 1])), 2),
            "scatter_arcsec": round(float(1.4826 * np.median(total)), 2),
            "note": "SPHEREx centroid minus Gaia DR3 at the SPHEREx epoch"}


def expected_movers(catalogs, mjd_a, mjd_b, wcs, detections, npix=180,
                    block=None, min_arcsec=3.0, limit=25):
    """
    G2: catalogued stars that *should* have moved >= one map pixel between the
    first and last epoch, and whether a detection was found at either end.
    """
    if mjd_a is None or mjd_b is None or mjd_b <= mjd_a:
        return []
    dt = (mjd_b - mjd_a) / 365.25
    det_xy = []
    for d in detections:
        for key in ("pos_plus", "pos_minus", "pos"):
            if d.get(key):
                det_xy.append((d[key][1], d[key][0], d))

    def found_near(points):
        best = None
        for (x, y) in points:
            for dx, dy, d in det_xy:
                if np.hypot(dx - x, dy - y) * 3.0 <= 15.0:
                    if best is None or d["snr"] > best["snr"]:
                        best = d
        return best

    rows = []

    def consider(cat, src, pm, name=None, is_target=False):
        disp = float(np.hypot(*pm) * dt / 1e3)
        if disp < min_arcsec:
            return
        ref = CATALOG_EPOCH_MJD.get(cat, mjd_a)
        if is_target:
            a, b = target_position(block, mjd_a), target_position(block, mjd_b)
        else:
            a = propagate(src["ra"], src["dec"], pm[0], pm[1], (mjd_a - ref) / 365.25)
            b = propagate(src["ra"], src["dec"], pm[0], pm[1], (mjd_b - ref) / 365.25)
        try:
            xa, ya = (float(v) for v in wcs.world_to_pixel_values(*a))
            xb, yb = (float(v) for v in wcs.world_to_pixel_values(*b))
        except Exception:
            return
        if not (0 <= xb < npix and 0 <= yb < npix):
            return
        hit = found_near([(xa, ya), (xb, yb)])
        rows.append({
            "cat": cat, "name": name, "is_target": is_target,
            "ra": round(b[0], 6), "dec": round(b[1], 6),
            "x_a": round(xa, 1), "y_a": round(ya, 1),
            "x": round(xb, 1), "y": round(yb, 1),
            "pm_masyr": round(float(np.hypot(*pm)), 1),
            "displacement_arcsec": round(disp, 2),
            "resolvable": disp >= PSF_FWHM_ARCSEC,
            "mag": source_magnitude(src) if src else None,
            "detected": hit is not None,
            "det_kind": hit.get("kind") if hit else None,
            "det_snr": round(float(hit["snr"]), 1) if hit else None})

    if block and (block.get("pm_ra") or block.get("pm_dec")):
        consider("target", {}, (block.get("pm_ra") or 0, block.get("pm_dec") or 0),
                 name=block.get("common"), is_target=True)
    pm_floor = min_arcsec / dt * 1e3
    for cat, sources in (catalogs or {}).items():
        for s in sources or []:
            if cat == "gaia" and (s.get("gmag") or 99) > 19:
                continue
            if cat == "wise" and ((s.get("w1") or 99) > 15.5 or s.get("pm_from") == "gaia"):
                continue                     # Gaia twins are already listed
            pm = source_pm(cat, s)
            if pm and np.hypot(*pm) >= pm_floor:
                consider(cat, s, pm)
    # the target is usually also in Gaia/AllWISE: keep the target row only
    if any(r["is_target"] for r in rows):
        t = next(r for r in rows if r["is_target"])
        rows = [r for r in rows if r["is_target"]
                or sep_arcsec(r["ra"], r["dec"], t["ra"], t["dec"]) > 6.0]
    rows.sort(key=lambda r: -r["displacement_arcsec"])
    return rows[:limit]


def target_sed(meta, block, spectrum_points, mjds):
    """
    W6 SED (Gaia + AllWISE + SPHEREx) and W5 2010 comparison for the target.

    spectrum_points: [(wavelength_um, aperture_sum MJy/sr, pix_sr)].
    """
    cats = meta.get("catalogs") or {}
    extended = str(block.get("type", "")).startswith(EXTENDED_TYPES)
    out = {"points": [], "spherex": [], "counterparts": {}, "extended": extended}

    def nearest(cat, epoch_mjd, radius):
        ra, dec = target_position(block, epoch_mjd)
        best, best_sep = None, radius
        for s in cats.get(cat) or []:
            if cat == "wise" and not wise_reliable(s):
                continue
            sep = sep_arcsec(ra, dec, s["ra"], s["dec"])
            if sep < best_sep:
                best, best_sep = s, sep
        return best, best_sep

    if not extended:
        g, gsep = nearest("gaia", GAIA_DR3_EPOCH_MJD, 3.0)
        if g:
            out["counterparts"]["gaia"] = {"source_id": g.get("source_id"),
                                           "sep_arcsec": round(gsep, 2)}
            for band, key in (("BP", "bpmag"), ("G", "gmag"), ("RP", "rpmag")):
                conv = mag_to_mjy(band, g.get(key))
                if conv:
                    out["points"].append({"band": f"Gaia {band}", "lam_um": conv[0],
                                          "mjy": round(conv[1], 4), "mag": g.get(key)})
        w, wsep = nearest("wise", ALLWISE_EPOCH_MJD, 4.0)
        if w:
            out["counterparts"]["wise"] = {"designation": w.get("designation"),
                                           "sep_arcsec": round(wsep, 2)}
            for band, key in (("W1", "w1"), ("W2", "w2"), ("W3", "w3"), ("W4", "w4")):
                conv = mag_to_mjy(band, w.get(key))
                if conv:
                    out["points"].append({"band": band, "lam_um": conv[0],
                                          "mjy": round(conv[1], 4), "mag": w.get(key)})

    for lam, flux, pix_sr in spectrum_points:
        mjy = surface_sum_to_mjy(flux, pix_sr)
        if lam is not None and mjy is not None:
            out["spherex"].append({"lam_um": round(float(lam), 4),
                                   "mjy": round(mjy, 4)})

    # W5: SPHEREx now vs AllWISE 2010, in the W1 and W2 windows
    comparison = []
    years = [mjd_to_year(m) for m in mjds if m]
    for band, lo, hi in (("W1", 3.0, 3.9), ("W2", 4.1, 5.0)):
        wise_pt = next((p for p in out["points"] if p["band"] == band), None)
        sph = [p["mjy"] for p in out["spherex"] if lo <= p["lam_um"] <= hi and p["mjy"] > 0]
        if wise_pt and sph:
            ratio = float(np.median(sph)) / wise_pt["mjy"]
            comparison.append({
                "band": band, "spherex_mjy": round(float(np.median(sph)), 3),
                "wise_mjy": wise_pt["mjy"], "ratio": round(ratio, 3),
                "delta_mag": round(-2.5 * np.log10(ratio), 3),
                "n_spherex": len(sph),
                "years_between": round(float(np.mean(years)) - mjd_to_year(ALLWISE_EPOCH_MJD), 1)
                if years else None})
    out["wise_comparison"] = comparison
    out["note"] = ("SPHEREx points are 3-pixel-radius aperture sums: crowded "
                   "fields and extended targets inflate them, so treat a ratio "
                   "within ~30% of 1 as 'no change'.")
    return out


def load_neowise(target):
    store = _snapshot_store()
    if store is not None:
        manifest = _optional_published_manifest(store)
        entry = (manifest or {}).get("targets", {}).get(target)
        if entry and entry.get("neowise_object"):
            return _published_json(
                store, entry["neowise_object"], f"NEOWISE history for {target}")
    path = os.path.join(CACHE, f"{target}_neowise.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def neowise_at(neowise, ra, dec, radius_arcsec=5.0):
    """The step7 summary for the detection position nearest (ra, dec)."""
    best, best_sep = None, radius_arcsec
    for p in (neowise or {}).get("positions", []):
        if p.get("kind") != "detection":
            continue
        s = sep_arcsec(ra, dec, p["ra"], p["dec"])
        if s < best_sep:
            best, best_sep = p, s
    if not best:
        return None
    out = dict(best.get("summary") or {})
    out["n_exposures"] = best.get("n_exposures")
    return out


def target_statistics(meta, detections, sigma1, sigma2, npix=180,
                      pixscale_arcsec=3.0, dt_years=0.5, n_exposures=None):
    """The numbers shown in the dashboard's stat cards."""
    depths = [s.get("mjd") for s in meta.get("sources", []) if s.get("mjd")]
    baseline_days = round(max(depths) - min(depths), 1) if len(depths) > 1 else None
    snrs = [d["snr"] for d in detections] or [0.0]
    n_known = sum(1 for d in detections if d.get("verdict") == "KNOWN")
    fov_armin = npix * pixscale_arcsec / 60.0

    # A frame that is almost entirely flagged (a saturated naked-eye star, for
    # instance) leaves no valid pixels to weight, so the inverse-variance noise
    # collapses to a meaningless number. Report that honestly as "no estimate"
    # instead of printing 1e+06 as if it were a measurement.
    def usable(sig):
        try:
            sig = float(sig)
        except (TypeError, ValueError):
            return None
        return sig if np.isfinite(sig) and 1e-4 < sig < 1e3 else None

    s1, s2 = usable(sigma1), usable(sigma2)
    usable_noise = [x for x in (s1, s2) if x is not None]
    depth = round(5.0 * float(np.mean(usable_noise)), 4) if usable_noise else None

    return {
        "n_exposures": n_exposures if n_exposures is not None else len(meta["paths"]),
        "n_epochs": len(set(meta.get("epochs", []))) or 2,
        "baseline_days": baseline_days,
        "baseline_months": round(baseline_days / 30.44, 1) if baseline_days else None,
        "dt_years": dt_years,
        "pixscale_arcsec": pixscale_arcsec,
        "npix": npix,
        "fov_arcmin": round(fov_armin, 2),
        "sky_area_deg2": round((fov_armin / 60.0) ** 2, 5),
        "sigma1": s1,
        "sigma2": s2,
        "noise_note": (None if usable_noise else
                       "noise estimate degenerate: the frame is almost entirely "
                       "flagged (saturated star), so no valid pixels are left to weight"),
        "depth_5sigma": depth,
        "n_detections": len(detections),
        "n_known": n_known,
        "n_candidate": len(detections) - n_known,
        "n_dipoles": sum(1 for d in detections if d.get("kind") == "dipole"),
        "n_monopoles": sum(1 for d in detections if d.get("kind") == "monopole"),
        "max_snr": round(float(max(snrs)), 2),
        "mean_snr": round(float(np.mean(snrs)), 2) if detections else 0.0,
        "n_gaia": len(meta.get("catalogs", {}).get("gaia", []) or []),
        "n_wise": len(meta.get("catalogs", {}).get("wise", []) or []),
        "n_vsx": len(meta.get("catalogs", {}).get("vsx", []) or []),
    }


@_rate_limited_route("get", "/api/targets", "60/minute")
def list_targets(request: Request = None):
    """Return the target presets: the published snapshot, else the local cache."""
    store = _snapshot_store()
    if store is not None:
        try:
            manifest = _published_manifest(store)
        except HTTPException:
            manifest = None            # no published snapshot yet: try the cache
        if manifest and manifest.get("targets_object"):
            return _published_object_response(
                store, manifest.get("targets_object"), "target list")

    # Local development: `python step1_ingest.py` writes data_cache/*_meta.json
    # and `uvicorn app:app` must be able to list them without a GCS bucket.
    local = _local_target_entries()
    if local:
        return {"targets": local}

    # Cloud-only mode with neither a snapshot nor a local cache.
    raise HTTPException(
        status_code=503,
        detail="No published snapshot and no local data cache is available. "
               "Set SPHEREX_SNAPSHOT_BUCKET to the GCS bucket name, or run "
               "`python step1_ingest.py` to fill data_cache/."
    )


def _local_target_entries():
    """Targets described by *_meta.json in SPHEREX_CACHE, for local runs."""
    try:
        names = sorted(name[:-len("_meta.json")]
                       for name in os.listdir(CACHE)
                       if name.endswith("_meta.json"))
    except OSError:
        return []
    entries = []
    for name in names:
        meta = load_target(name)
        if not meta:
            continue
        block = meta.get("meta") or {}
        entries.append({
            "name": name,
            "common": block.get("common") or name,
            "ra": block.get("ra"),
            "dec": block.get("dec"),
            "type": block.get("type"),
            "n_epochs": meta.get("n_epochs"),
            "n_exposures": len(meta.get("paths") or []),
        })
    return entries


@app.get("/api/status")
def get_status(target: str = Query("WISE_0855", max_length=80, pattern=r"^[A-Za-z0-9_]+$")):
    store = _snapshot_store()
    if store is None:
        return {
            "target": target,
            "snapshot_published_at": None,
            "field_last_checked_at": None,
            "field_last_processed_at": None,
            "catalogs_last_checked_at": None,
            "neowise_last_updated_at": None,
            "last_check": None,
            "refresh_attempt": {"status": "unavailable"},
        }

    pointer, manifest = _published_state(store)
    entry = (manifest or {}).get("targets", {}).get(target)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"No published snapshot exists for target '{target}'.")
    attempt = store.read_json("refresh-status/latest.json")
    if attempt:
        attempt = {
            key: attempt.get(key)
            for key in (
                "status", "started_at", "completed_at", "published_at",
                "error_code", "total_targets", "completed_targets",
                "current_target", "current_target_index",
                "current_target_state", "updated_count", "unchanged_count",
                "failed_count", "failed_target",
            )
        }
    else:
        attempt = {"status": "unknown"}
    _note_snapshot(manifest=pointer.get("manifest"))
    neowise_last_updated_at = entry.get("neowise_checked_at")
    if not neowise_last_updated_at and entry.get("neowise_object"):
        neowise = store.read_json(entry["neowise_object"])
        neowise_last_updated_at = (neowise or {}).get("generated_utc")
    return {
        "target": target,
        "snapshot_published_at": pointer.get("published_at"),
        "field_last_checked_at": entry.get("last_checked_at"),
        "field_last_processed_at": entry.get("last_updated_at"),
        "catalogs_last_checked_at": entry.get("catalogs_checked_at"),
        "neowise_last_updated_at": neowise_last_updated_at,
        "last_check": entry.get("last_check"),
        "refresh_attempt": attempt,
    }


_REFRESH_STATUS_FIELDS = (
    "status", "started_at", "completed_at", "published_at", "error_code",
    "total_targets", "completed_targets", "current_target",
    "current_target_index", "current_target_state", "updated_count",
    "unchanged_count", "failed_count", "failed_target",
)


_FIELD_REFRESH_STATES = frozenset(
    {"queued", "checking", "updated", "unchanged", "retained", "failed"})


def _safe_field_states(value):
    """Keep only well-formed per-field refresh states (name -> known state)."""
    if not isinstance(value, dict):
        return {}
    return {
        name: state
        for name, state in list(value.items())[:64]
        if isinstance(name, str) and TARGET_NAME.fullmatch(name)
        and state in _FIELD_REFRESH_STATES
    }


def _safe_refresh_status(value):
    value = value if isinstance(value, dict) else {}
    safe = {key: value.get(key) for key in _REFRESH_STATUS_FIELDS}
    if safe["status"] is None:
        safe["status"] = "unknown"
    safe["field_states"] = _safe_field_states(value.get("field_states"))
    return safe


def _next_scheduled_refresh(now=None):
    """Next Cloud Scheduler run, as an ISO-8601 UTC string, or None.

    Terraform schedules ``0 */12 * * *`` in Asia/Kuala_Lumpur (UTC+8, no
    DST), i.e. 04:00 and 16:00 UTC.  Terraform passes the same hours here via
    SPHEREX_REFRESH_SCHEDULE_UTC_HOURS so the two cannot drift silently.
    """
    from datetime import datetime, timedelta, timezone
    raw = os.environ.get("SPHEREX_REFRESH_SCHEDULE_UTC_HOURS", "4,16")
    try:
        hours = sorted({int(part) for part in raw.split(",") if part.strip()})
    except ValueError:
        return None
    hours = [hour for hour in hours if 0 <= hour <= 23]
    if not hours:
        return None
    now = now or datetime.now(timezone.utc)
    for day in (0, 1):
        base = (now + timedelta(days=day)).replace(
            minute=0, second=0, microsecond=0)
        for hour in hours:
            candidate = base.replace(hour=hour)
            if candidate > now:
                return candidate.isoformat()
    return None


def _refresh_proxy_url():
    value = os.environ.get("SPHEREX_REFRESH_API_URL", "").strip().rstrip("/")
    if not value:
        return None
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise HTTPException(
            status_code=503,
            detail="Cloud refresh proxy must be a trusted HTTPS API URL.")
    return value


def _proxy_refresh_request(endpoint, method, token=None):
    base = _refresh_proxy_url()
    if not base:
        return None
    headers = {"X-Refresh-Token": token} if token else {}
    try:
        response = requests.request(
            method,
            f"{base}/{endpoint}",
            headers=headers,
            timeout=(5, 25),
            allow_redirects=False,
        )
    except requests.RequestException as error:
        _LOGGER.error("Cloud refresh proxy failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=502,
            detail="The configured Cloud refresh API could not be reached.") from error
    try:
        payload = response.json()
    except ValueError as error:
        raise HTTPException(
            status_code=502,
            detail="The configured Cloud refresh API returned invalid data.") from error
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail="The configured Cloud refresh API returned invalid data.")
    if endpoint == "refresh-status" and response.ok:
        payload = {
            **_safe_refresh_status(payload),
            "manual_refresh_available": bool(
                payload.get("manual_refresh_available")),
            "snapshot_published_at": payload.get("snapshot_published_at"),
            "next_scheduled_at": payload.get("next_scheduled_at"),
        }
    return JSONResponse(status_code=response.status_code, content=payload)


@_rate_limited_route("get", "/api/snapshot-history", "60/minute")
def get_snapshot_history(target: str = Query("WISE_0855", max_length=80, pattern=r"^[A-Za-z0-9_]+$"),
                         request: Request = None):
    """Retained published snapshots for one field, newest first.

    The dashboards are immutable objects, so the browser can keep a historical
    frame it has already viewed and replay it without another network read.
    """
    if not isinstance(target, str) or not TARGET_NAME.fullmatch(target):
        raise HTTPException(status_code=400, detail="Invalid target name.")
    store = _snapshot_store()
    if store is None:
        raise HTTPException(status_code=503,
                            detail="Snapshot history is not available.")
    return _snapshot_history_payload(store, target)


@_rate_limited_route("get", "/api/snapshot-index", "60/minute")
def get_snapshot_index(request: Request = None):
    """Names of the immutable objects behind every snapshot response.

    About 2 KB. The browser's offline copy compares these names with the ones
    it saved and downloads only the objects that changed; each download is
    checked against its X-Snapshot-Object header before it is used.
    """
    store = _snapshot_store()
    if store is None:
        raise HTTPException(status_code=503,
                            detail="Snapshot store is not available.")
    pointer, manifest = _published_state(store)
    _note_snapshot(manifest=pointer.get("manifest"))
    fields = {
        name: {"dashboard": entry.get("dashboard"),
               "recovery": entry.get("recovery")}
        for name, entry in sorted((manifest.get("targets") or {}).items())
    }
    return {
        "manifest": pointer.get("manifest"),
        "published_at": pointer.get("published_at"),
        "targets_object": manifest.get("targets_object"),
        "mosaic": manifest.get("mosaic"),
        "fields": fields,
    }


@app.get("/api/refresh-status")
def get_refresh_status():
    proxied = _proxy_refresh_request("refresh-status", "GET")
    if proxied is not None:
        return proxied
    store = _snapshot_store()
    if store is None:
        return {
            "status": "unavailable",
            "manual_refresh_available": False,
            "snapshot_published_at": None,
            "next_scheduled_at": None,
            "field_states": {},
        }
    pointer, _ = store.read_current()
    status = _safe_refresh_status(
        store.read_json("refresh-status/latest.json"))
    return {
        **status,
        "manual_refresh_available": bool(
            os.environ.get("SPHEREX_REFRESH_OPERATOR_TOKEN")
            and os.environ.get("SPHEREX_REFRESH_PROJECT")
            and os.environ.get("SPHEREX_REFRESH_REGION")
            and os.environ.get("SPHEREX_REFRESH_JOB")),
        "snapshot_published_at": (pointer or {}).get("published_at"),
        "next_scheduled_at": _next_scheduled_refresh(),
    }


def _invoke_cloud_refresh_job():
    project = os.environ["SPHEREX_REFRESH_PROJECT"]
    region = os.environ["SPHEREX_REFRESH_REGION"]
    job = os.environ["SPHEREX_REFRESH_JOB"]
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    url = (f"https://run.googleapis.com/v2/projects/{project}/locations/"
           f"{region}/jobs/{job}:run")
    response = AuthorizedSession(credentials).post(url, json={}, timeout=20)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(
            f"Cloud Run Jobs API returned HTTP {response.status_code}")
    try:
        return response.json().get("name")
    except (ValueError, AttributeError):
        return None


@_rate_limited_route("post", "/api/refresh", "3/minute")
def trigger_cloud_refresh(request: Request):
    supplied_token = request.headers.get("x-refresh-token", "")
    proxied = _proxy_refresh_request("refresh", "POST", supplied_token)
    if proxied is not None:
        return proxied
    store = _snapshot_store()
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Cloud snapshot refresh is not configured for this API.")
    expected_token = os.environ.get("SPHEREX_REFRESH_OPERATOR_TOKEN", "")
    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="Operator-authorized cloud refresh is not configured.")
    if (not supplied_token or len(supplied_token) > 1024
            or not hmac.compare_digest(supplied_token, expected_token)):
        raise HTTPException(status_code=401, detail="Invalid operator token.")

    try:
        if store.refresh_lock_is_active():
            raise HTTPException(
                status_code=409,
                detail="A cloud refresh is already running.")
    except HTTPException:
        raise
    except Exception as error:
        _LOGGER.error("Could not verify cloud refresh lock: %s",
                      type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="Could not verify whether a cloud refresh is running.") from error
    required = ("SPHEREX_REFRESH_PROJECT", "SPHEREX_REFRESH_REGION",
                "SPHEREX_REFRESH_JOB")
    if any(not os.environ.get(name) for name in required):
        raise HTTPException(
            status_code=503,
            detail="Cloud Run refresh job configuration is incomplete.")
    accepted_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        operation = _invoke_cloud_refresh_job()
    except Exception as error:
        _LOGGER.error("Cloud refresh trigger failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=502,
            detail="Cloud Run did not accept the refresh request.") from error
    return {
        "status": "accepted",
        "accepted_at": accepted_at,
        "operation": operation,
    }


@_rate_limited_route("get", "/api/dashboard", "30/minute")
def get_dashboard(request: Request = None,
                  target: str = Query("WISE_0855", max_length=80, pattern=r"^[A-Za-z0-9_]+$"),
                  source: str = Query("cache"),
                  snapshot: str = Query("", max_length=96),
                  live_slots: int = Query(0, ge=0, le=12),
                  live_size: float = Query(0.2, ge=0.05, le=0.5),
                  live_epochs: str = Query("firstlast", pattern="^(firstlast|all)$")):
    """
    Run the full pipeline on the specified target and return everything
    the frontend needs to render the dashboard.

    ``snapshot=<manifest object>`` returns one retained historical version of
    the published dashboard instead of the current one.  The response carries
    the same X-Snapshot-Object / X-Snapshot-Manifest labels as every other
    snapshot read, so the offline copy can verify and reuse it.
    """
    store = _snapshot_store()
    entry = None
    if not isinstance(snapshot, str):
        snapshot = ""          # internal callers get the FastAPI Query default
    if snapshot and str(source).lower() == "live":
        raise HTTPException(
            status_code=400,
            detail="A historical snapshot cannot also be a fresh IRSA request.")
    if snapshot and store is None:
        raise HTTPException(
            status_code=503, detail="Snapshot history is not available.")
    if snapshot and store is not None:
        pointer, current_manifest = _published_state(store)
        for manifest_object, snap_manifest, published_at in _snapshot_chain(
                store, pointer, current_manifest):
            if manifest_object != snapshot:
                continue
            snap_entry = (snap_manifest.get("targets") or {}).get(target) or {}
            if not snap_entry.get("dashboard"):
                raise HTTPException(
                    status_code=404,
                    detail=f"No dashboard for '{target}' in that snapshot.")
            return _published_object_response(
                store, snap_entry.get("dashboard"),
                f"dashboard for {target} from {published_at}",
                manifest=manifest_object)
        raise HTTPException(
            status_code=404,
            detail="That snapshot is not retained any more; reload the "
                   "snapshot history list.")
    if store is not None:
        live_request = str(source).lower() == "live"
        manifest = (_optional_published_manifest(store) if live_request
                    else _published_manifest(store))
        entry = (manifest or {}).get("targets", {}).get(target)
        if not live_request:
            if not entry:
                raise HTTPException(
                    status_code=404,
                    detail=f"No published snapshot exists for target '{target}'.")
            return _published_object_response(
                store, entry.get("dashboard"), f"dashboard for {target}")
        if entry:
            if entry.get("meta_object"):
                meta = _published_json(
                    store, entry.get("meta_object"), f"metadata for {target}")
            else:
                # B7 FIX: no-products / error-only entries have no meta_object.
                # Fall back to the TARGETS registry so a live retry can still
                # run the pipeline instead of returning 503/404.
                meta = _meta_from_targets_registry(target)
        else:
            meta = load_target(target)
    else:
        meta = load_target(target)
    if meta is None and str(source).lower() == "live":
        # A fresh IRSA fetch needs only the field position, so it must not
        # depend on the snapshot being reachable.
        meta = _meta_from_targets_registry(target)
    if meta is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No published or registered field '{target}'."}
        )

    ra = meta["meta"]["ra"]
    dec = meta["meta"]["dec"]

    custom = bool(meta.get("custom"))
    live_mode = (str(source).lower() == "live") or custom
    if (custom and target in FIELD_DASH_CACHE
            and str(source).lower() not in {"refresh", "live"}):
        return FIELD_DASH_CACHE[target]
    live_provenance = None
    live_cached = False
    irsa_check = None
    listing_rows = None
    if live_mode and not custom and _is_registered_field(target):
        # Ask IRSA what exists *now*.  When it lists exactly the products the
        # published snapshot processed, reprocessing would reproduce that
        # result, so return it (labelled) instead of re-downloading cutouts.
        try:
            listing, irsa_check, unchanged = _check_irsa_listing(
                store, entry, target)
        except Exception as e:
            return JSONResponse(
                status_code=502,
                content={"error": _public_error("IRSA listing query failed", e),
                         "hint": "IRSA may be busy; retry in a minute."})
        if unchanged is not None:
            return unchanged
        if _same_position(listing[0], listing[1], ra, dec):
            listing_rows = listing[2]
    if live_mode and listing_rows is None:
        listing_rows = meta.pop("_listing_rows", None)
    if live_mode:
        try:
            cutouts, live_provenance, live_cached = live_exposures(
                target, meta, size_deg=live_size,
                epoch_mode=live_epochs, max_slots=live_slots,
                progress=_PROGRESS_CALLBACK.get(), rows=listing_rows)
        except Exception as e:
            return JSONResponse(
                status_code=422 if isinstance(e, CoverageError) else 502,
                content={"error": (str(e) if isinstance(e, CoverageError)
                                   else _public_error("Live fetch from IRSA failed", e)),
                         "hint": "retry, or call without ?source=live to use data_cache/"}
            )
        paths = []
        sources = [c["data"] for c in cutouts]
        epochs = [c["epoch"] for c in cutouts]
    else:
        # FITS files are gitignored, so a clone can hold a target's
        # _meta.json without its cutouts: keep only the files that exist.
        present = [i for i, p in enumerate(meta["paths"]) if os.path.exists(p)]
        paths = [meta["paths"][i] for i in present]
        sources = list(paths)
        all_epochs = meta.get("epochs")
        epochs = ([all_epochs[i] for i in present]
                  if all_epochs is not None and len(all_epochs) == len(meta["paths"])
                  else None)
        if len(paths) < 2:
            return JSONResponse(
                status_code=404,
                content={"error": f"'{target}': {len(paths)} of {len(meta['paths'])} "
                                  f"cutout FITS files are on disk. Run "
                                  f"`python step1_ingest.py --targets {target}` "
                                  f"or use source=live."})

    if len(sources) < 2:
        return JSONResponse(
            status_code=500,
            content={"error": f"Only {len(sources)} usable cutout(s) for '{target}'. "
                              f"Need at least 2 for subtraction."}
        )

    epochs = _resolve_epochs(epochs, len(sources))
    bands = exposure_bands(meta, sources, cutouts if live_mode else None)
    coverage_error = epoch_coverage_error(epochs, bands)
    if coverage_error:
        return JSONResponse(
            status_code=422,
            content={"error": f"'{target}': {coverage_error}.",
                     "hint": "Use a release with a shared detector band in at least two "
                             "epochs, or select another field."}
        )

    npix = 180
    wcs = make_target_wcs(ra, dec, npix=npix, pixscale_arcsec=3.0)

    # Epoch dates and the target's own motion: catalogues are propagated to
    # each epoch (G1) and the target spectrum follows the star (movers).
    mjds = exposure_mjds(meta, paths, cutouts if live_mode else None)
    block = target_motion_block(target, meta)
    neowise = load_neowise(target)

    # -- epochs -----------------------------------------------------------
    # Every epoch in the cache is co-added separately, and ZOGY is run on each
    # consecutive pair.  The deepest baseline (first vs last) becomes the map
    # shown by default; the extra pairs contribute detections only.
    order = sorted(set(epochs))
    files_by_epoch = {e: [s for s, pe in zip(sources, epochs) if pe == e]
                      for e in order}
    epoch_mjd = {}
    for e in order:
        vals = [m for m, pe in zip(mjds, epochs) if pe == e and m]
        epoch_mjd[e] = float(np.mean(vals)) if vals else None

    _resolve_catalog_lookup(meta)
    raw_catalogs = meta.get("catalogs") or {}
    _cats_cache = {}

    def catalogs_at(e):
        """Catalogues with every movable row moved to epoch e (G1, W4)."""
        if e not in _cats_cache:
            _cats_cache[e] = propagate_catalogs(raw_catalogs, epoch_mjd.get(e))
        return _cats_cache[e]

    def baseline_years(ea, eb):
        a, b = epoch_mjd.get(ea), epoch_mjd.get(eb)
        return (b - a) / 365.25 if a and b and b > a else None

    try:
        # Matched masks: a band only counts at a pixel where it is unmasked
        # in every epoch, so no epoch can lose a band the others keep.
        _emit_progress("coadd", f"Co-adding {len(order)} observing epochs")
        groups = {e: [(s, b) for s, b, pe in zip(sources, bands, epochs) if pe == e]
                  for e in order}
        # v2: ZODI-subtracted, quality-filtered, with each epoch's measured PSF
        stacks, noise_maps, excluded, n_bands, coadd_extra = coadd_matched(
            groups, wcs, npix=npix, return_psf=True)
        psfs = coadd_extra["psfs"]
        psf_info = coadd_extra["psf_info"]
        exposure_quality = coadd_extra["exposure_quality"]
        edge_map = coadd_extra["edge_map"]
        noises = {e: float(np.nanmedian(np.where(noise_maps[e] < 1e3, noise_maps[e], np.nan)))
                  for e in order}
        _emit_progress("coadd", f"Co-add complete for {len(order)} epochs")
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": _public_error("Co-addition failed", e)}
        )

    epoch1_files = files_by_epoch[order[0]]
    epoch2_files = files_by_epoch[order[-1]]
    img1, img2 = stacks[order[0]], stacks[order[-1]]
    sigma1, sigma2 = noises[order[0]], noises[order[-1]]

    try:
        _emit_progress("subtraction", "Running ZOGY subtraction")
        D, S = zogy(img1, img2, sigma1, sigma2,
                    psf1=psfs.get(order[0]), psf2=psfs.get(order[-1]))
        _emit_progress("subtraction", "Initial ZOGY subtraction complete")
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": _public_error("ZOGY subtraction failed", e)}
        )

    # consecutive pairs (E1->E2, E2->E3, ...) plus the deep pair E1->En
    pair_list = [(a, b) for a, b in zip(order, order[1:])]
    if len(order) > 2 and (order[0], order[-1]) not in pair_list:
        pair_list.append((order[0], order[-1]))
    if not pair_list:
        pair_list = [(order[0], order[0])]

    raw_detections = []
    pair_summary = []
    pair_count = sum(1 for ea, eb in pair_list if ea != eb)
    completed_pairs = 0
    for ea, eb in pair_list:
        if ea == eb:
            continue
        try:
            if (ea, eb) == (order[0], order[-1]):
                d_img, s_img = D, S
            else:
                d_img, s_img = zogy(stacks[ea], stacks[eb], noises[ea],
                                    noises[eb], psf1=psfs.get(ea), psf2=psfs.get(eb))
            found = find_detections(s_img, pix_scale_arcsec=3.0,
                                    dt_years=baseline_years(ea, eb) or 0.5,
                                    sigma_thresh=config.DETECTION_SIGMA)
        except Exception:
            found = []
        pair_summary.append({
            "pair": f"E{ea}\u2192E{eb}",
            "n_exposures_a": len(files_by_epoch[ea]),
            "n_exposures_b": len(files_by_epoch[eb]),
            "n_detections": len(found),
            "sigma_a": noises[ea],
            "sigma_b": noises[eb],
        })
        for d in found:
            d["pair"] = f"E{ea}\u2192E{eb}"
            d["epochs"] = (ea, eb)
            raw_detections.append(d)
        completed_pairs += 1
        _emit_progress(
            "subtraction",
            f"Processed epoch-pair subtraction ({completed_pairs}/{pair_count})",
            completed=completed_pairs,
            total=pair_count,
        )

    # merge the same object found in several pairs, keeping the strongest
    # (KD-tree, config.MERGE_RADIUS_PX; records n_pairs_seen)
    detections = dedup_detections(raw_detections)

    # artefact masks: footprint edges and bright-star detector-axis crosses
    spike_angles = coadd_extra.get("axis_angles_deg") or []
    _bright_cache = {}

    def bright_at(e):
        if e not in _bright_cache:
            _bright_cache[e] = bright_stars_on_grid(catalogs_at(e), wcs, npix,
                                                    bright_mask_radius)
        return _bright_cache[e]

    triaged = []
    triage_detections = detections[:config.MAX_TRIAGED_DETECTIONS]
    _emit_progress("triage", f"Classifying {len(triage_detections)} detections")
    for index, d in enumerate(triage_detections, start=1):
        ea, eb = d.get("epochs", (order[0], order[-1]))
        b_yr = baseline_years(ea, eb)
        context = {"baseline_years": b_yr}
        # How many bands the mask matching dropped at this detection
        lost = max((masked_near(excluded, *d[k]) for k in ("pos_plus", "pos_minus", "pos")
                    if d.get(k)), default=0)
        if lost:
            context["mask"] = {"bands_excluded": lost, "n_bands": n_bands}
        mask_hits = lost >= n_bands
        lobes = [d.get(k) for k in ("pos_plus", "pos_minus", "pos") if d.get(k)]
        n_edge = edge_hit(edge_map, lobes)
        if n_edge:
            context["edge"] = {"n_exposures": n_edge, "edge_px": config.EDGE_MASK_PX}
        spike = spike_hit(lobes, bright_at(eb), spike_angles)
        if spike:
            context["spike"] = spike
        fw = [psf_info.get(e, {}).get("grid_fwhm_arcsec") for e in (ea, eb)]
        fw = [f for f in fw if f]
        if fw:
            context["psf_fwhm_arcsec"] = max(fw)
        if d.get("kind") == "dipole":
            (y1, x1), (y2, x2) = d["pos_plus"], d["pos_minus"]
            d["separation_arcsec"] = float(np.hypot(x1 - x2, y1 - y2) * 3.0)
            if b_yr:
                # step3 assumed dt = 0.5 yr; use the real epoch spacing
                d["proper_motion_arcsec_yr"] = d["separation_arcsec"] / b_yr
        elif ea in stacks and eb in stacks and not mask_hits:
            py, px = d["pos"]
            context["delta_mag"] = delta_mag(stacks[ea], stacks[eb], noises[ea],
                                             noises[eb], px, py)
        if neowise:
            py, px = d.get("pos_plus") or d.get("pos")
            d_ra, d_dec = wcs.pixel_to_world_values(float(px), float(py))
            context["neowise"] = neowise_at(neowise, float(d_ra), float(d_dec))
        t = triage(d, ra, dec, npix, 3.0, catalogs_at(eb), wcs=wcs, context=context)
        t["kind"] = d["kind"]
        t["snr"] = d["snr"]
        if d.get("sign") is not None:
            t["sign"] = d["sign"]
        t["n_pairs_seen"] = d.get("n_pairs_seen", 1)
        t["pairs_seen"] = d.get("pairs_seen") or [d.get("pair")]
        t["pair"] = d.get("pair")
        t["position"] = d.get("pos_plus") or d.get("pos")
        if d.get("pos_minus"):
            t["position_minus"] = d["pos_minus"]
        if "proper_motion_arcsec_yr" in d:
            t["proper_motion_arcsec_yr"] = d["proper_motion_arcsec_yr"]
        if context.get("delta_mag") is not None:
            t["delta_mag"] = context["delta_mag"]
        # v2: epochs and a stable id for every detection, so enrichers
        # (asteroids, links, ML) and votes can refer to it.  t["ra"]/t["dec"]
        # are already set by triage (WCS, with a flat-sky fallback).
        _py, _px = t["position"]
        t["mjd_a"], t["mjd_b"] = epoch_mjd.get(ea), epoch_mjd.get(eb)
        t["epochs"] = [ea, eb]
        t["detection_id"] = f"{target}:{t.get('pair')}:{_py},{_px}"
        d["category"] = t["category"]
        triaged.append(t)
        _emit_progress(
            "triage",
            f"Classified detections ({index}/{len(triage_detections)})",
            completed=index,
            total=len(triage_detections),
        )

    # Spectrum: follow the target to each exposure's own epoch, so a fast
    # mover is measured where it actually is, not at its catalogue position.
    _emit_progress("spectrum", "Extracting the target spectrum")
    def target_at(i):
        return target_position(block, mjds[i] if i < len(mjds) else None)

    spectrum = extract_spectrum_from_cutout(sources[0], *target_at(0))
    points = []
    if meta.get("spectrum_fits") and os.path.exists(meta["spectrum_fits"]):
        # A cached 102-band SPECTRUM table wins over the synthetic ramp below.
        from astropy.io import fits

        with fits.open(meta["spectrum_fits"]) as hdul:
            table = hdul["SPECTRUM"].data
            wavelengths = np.asarray(table["WAVELENGTH"], dtype=float).tolist()
            flux = np.asarray(table["FLUX"], dtype=float).tolist()
        classification = three_index_classifier(np.array(wavelengths), np.array(flux))
    else:
        # Real SPHEREx cutouts: build the observed spectrum from the aperture
        # photometry of every cached exposure, using each file's own WCS-WAVE
        # wavelength calibration.
        points = []
        for i, path in enumerate(sources):
            spec = extract_spectrum_from_cutout(path, *target_at(i))
            if spec.get("wavelength_um") is not None:
                points.append((spec["wavelength_um"], spec["flux"], spec.get("pix_sr")))
        points.sort(key=lambda p: p[0])
        wavelengths = [float(p[0]) for p in points]
        flux = [float(p[1]) for p in points]
        # The three-index classifier needs a sampled spectrum, not a handful
        # of exposures, so it is only reported when enough channels exist.
        classification = (three_index_classifier(np.array(wavelengths),
                                                 np.array(flux))
                          if len(points) >= 30 else None)

    def clean_array(arr):
        arr = np.round(arr, 2)
        return np.where(np.isfinite(arr), arr, 0).tolist()

    # ---- field-level catalogue science --------------------------------------
    last_cats = catalogs_at(order[-1])            # positions as of the map epoch
    movers = expected_movers(harmonise_motions(raw_catalogs), epoch_mjd.get(order[0]),
                             epoch_mjd.get(order[-1]), wcs, detections,
                             npix=npix, block=block)
    astrometry = astrometry_check(img1, wcs, catalogs_at(order[0]).get("gaia"),
                                  sigma1, npix=npix)
    sed = target_sed(meta, block, points, mjds)
    t_ra, t_dec = target_position(block, epoch_mjd.get(order[-1]))
    try:
        tx, ty = (float(v) for v in wcs.world_to_pixel_values(t_ra, t_dec))
    except Exception:
        tx = ty = None
    categories = {}
    for t in triaged:
        categories[t["category"]] = categories.get(t["category"], 0) + 1
    neowise_target = next((p for p in (neowise or {}).get("positions", [])
                           if p.get("kind") == "target"), None)

    stats = target_statistics(meta, triaged, sigma1, sigma2, npix=npix,
                              n_exposures=len(sources))
    stats.update({
        "categories": categories,
        "n_priority": sum(1 for t in triaged if t["priority"] <= 2),
        "n_expected_movers": len(movers),
        "n_movers_detected": sum(1 for m in movers if m["detected"]),
        "astrometry": astrometry,
    })

    payload = {
        "pipeline": {**pipeline_stamp(), "model_version": None},
        "target": target,
        "common": meta["meta"].get("common", target),
        "ra": ra,
        "dec": dec,
        "image": clean_array(img1),
        "zogy_image": clean_array(D),
        "zogy_snr": clean_array(S),
        "sigma1": sigma1,
        "sigma2": sigma2,
        "detections": triaged,
        "spectrum": {
            "wavelengths_um": wavelengths,
            "flux": flux,
            "classification": classification,
            "aperture_flux": spectrum["flux"],
            "aperture_wavelength_um": spectrum["wavelength_um"],
        },
        "n_epoch1": len(epoch1_files),
        "n_epoch2": len(epoch2_files),
        "map": {
            "npix": npix,
            "pixscale_arcsec": 3.0,
            "ra": ra,
            "dec": dec,
            "fov_arcmin": round(npix * 3.0 / 60.0, 2),
        },
        "provenance": (live_provenance if live_mode
                       else exposure_provenance(meta, wcs, npix=npix)),
        "source": "live" if live_mode else "cache",
        "live_cached": live_cached,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stats": stats,
        "catalog_overlay": catalog_overlay({**meta, "catalogs": last_cats}, wcs, npix=npix),
        "field_objects": build_field_objects({**meta, "catalogs": last_cats},
                                             triaged, wcs, npix=npix),
        "histogram": significance_histogram(S),
        "epoch_pairs": pair_summary,
        "catalog_counts": {k: len(v or []) for k, v in raw_catalogs.items()},
        "catalog_status": meta.get("catalog_status"),
        "epochs": [{"epoch": e, "mjd": epoch_mjd.get(e),
                    "year": round(mjd_to_year(epoch_mjd[e]), 3) if epoch_mjd.get(e) else None,
                    "psf": {k: (psf_info.get(e) or {}).get(k)
                            for k in ("source", "fwhm_arcsec", "grid_fwhm_arcsec")}}
                   for e in order],
        "exposure_quality": exposure_quality,
        "target_position": {"ra": t_ra, "dec": t_dec, "x": tx, "y": ty,
                            "moving": bool(block.get("pm_ra") or block.get("pm_dec")),
                            "pm_ra": block.get("pm_ra"), "pm_dec": block.get("pm_dec")},
        "expected_movers": movers,
        "sed": sed,
        "irsa_check": irsa_check,
        "neowise": {"available": neowise is not None,
                    "generated_utc": (neowise or {}).get("generated_utc"),
                    "target": neowise_target},
    }

    # ---- v2 enrichers (spherexlens/enrich.py) -------------------------------
    # Asteroid check, links, coverage, ML score, evidence.  Each adds fields
    # documented in spherexlens/CONTRACT.md; a failing one is reported in
    # payload["enrichment"] and never breaks the dashboard.
    _emit_progress("enrich", "Running asteroid check, ML scoring and evidence")
    enrich_ctx = {
        "target": target, "meta": meta, "wcs": wcs, "npix": npix,
        "pixscale_arcsec": 3.0, "ra": ra, "dec": dec,
        "stacks": stacks, "noises": noises, "noise_maps": noise_maps,
        "excluded": excluded, "n_bands": n_bands,
        "epoch_order": order, "epoch_mjd": epoch_mjd,
        "sources": sources, "bands": bands, "epochs": epochs, "mjds": mjds,
        "raw_catalogs": raw_catalogs, "catalogs_at": catalogs_at,
        "neowise": neowise, "block": block, "S": S, "D": D,
        "raw_detections": detections, "live": live_mode,
        "psfs": psfs, "psf_info": psf_info, "exposure_quality": exposure_quality,
        "edge_map": edge_map,
    }
    run_enrichers(payload, enrich_ctx)
    _emit_progress("complete", "Dashboard processing complete")
    return payload


@_rate_limited_route("get", "/api/dashboard/stream", "10/minute")
async def stream_dashboard(request: Request = None,
                           target: str = Query("WISE_0855", max_length=80),
                           live_slots: int = Query(0, ge=0, le=12),
                           live_size: float = Query(0.2, ge=0.05, le=0.5),
                           live_epochs: str = Query(
                               "firstlast", pattern="^(firstlast|all)$"),
                           result: str = _RESULT_PARAM):
    return _progress_stream(
        lambda: get_dashboard(
            target=target,
            source="live",
            live_slots=live_slots,
            live_size=live_size,
            live_epochs=live_epochs,
        ),
        f"Starting a fresh IRSA run for {target}",
        compress=result == "gzip",
    )


def _run_recovery_pipeline(target, ra, dec, sources, epochs, bands, source="live"):
    """Shared pipeline body for all recovery code paths."""
    epochs = _resolve_epochs(epochs, len(sources))
    coverage_error = epoch_coverage_error(epochs, bands)
    if coverage_error:
        return JSONResponse(
            status_code=422,
            content={"error": f"'{target}': recovery unavailable because "
                              f"{coverage_error}."})

    order = sorted(set(epochs))
    npix = 180
    wcs = make_target_wcs(ra, dec, npix=npix)
    groups = {e: [(s, b) for s, b, pe in zip(sources, bands, epochs) if pe == e]
              for e in (order[0], order[-1])}
    _emit_progress("coadd", "Co-adding matched recovery epochs")
    stacks, noise_maps, _, _, coadd_extra = coadd_matched(groups, wcs, npix=npix,
                                                          return_psf=True)
    _emit_progress("coadd", "Matched recovery epochs are ready")
    # step6 injects and subtracts Gaussians: give them the measured co-add PSF
    # width (largest of the two epochs) instead of the 1.4 px default
    rec_fwhm = [(coadd_extra["psf_info"].get(e) or {}).get("grid_fwhm_arcsec")
                for e in (order[0], order[-1])]
    rec_fwhm = [f for f in rec_fwhm if f]
    rec_sigma = psf_sigma_px(max(rec_fwhm)) if rec_fwhm else 1.4
    img1, img2 = stacks[order[0]], stacks[order[-1]]
    noise1, noise2 = noise_maps[order[0]], noise_maps[order[-1]]
    noise1 = np.where(noise1 < 1e3, noise1, np.nan)
    noise2 = np.where(noise2 < 1e3, noise2, np.nan)

    _emit_progress("recovery", "Running injection-recovery trials")

    def report_recovery_progress(event):
        _emit_progress(
            "recovery",
            f"Recovery grid {event['completed']}/{event['total']} cells "
            f"(SNR {event['snr']}, motion {event['mu']} arcsec/year)",
            completed=event["completed"],
            total=event["total"],
        )

    results = run_injection_recovery(
        img1, img2,
        float(np.nanmedian(noise1)), float(np.nanmedian(noise2)),
        psf_sigma_px=rec_sigma,
        snr_grid=[3, 5, 7, 10, 15, 20],
        mu_grid=[1, 2, 5, 10],
        n_trials=10,
        progress=(report_recovery_progress
                  if _PROGRESS_CALLBACK.get() else None),
    )

    grid = []
    for (snr, mu), frac in sorted(results.items()):
        grid.append({"snr": snr, "mu": mu, "recovery": float(frac)})

    return {
        "target": target,
        "grid": grid,
        "psf_sigma_px": round(float(rec_sigma), 3),
        "source": "live" if str(source).lower() == "live" else "cache",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _meta_from_targets_registry(target):
    """
    B7: Build a minimal meta dict from the TARGETS registry for targets whose
    manifest entry has no meta_object (no-products or error-only entries).
    Returns None when the target is not in TARGETS.
    """
    try:
        from step1_ingest import TARGETS
        t = TARGETS.get(target)
    except Exception:
        t = None
    if not t:
        return None
    return {
        "target": target,
        "meta": {
            "ra": float(t["ra"]),
            "dec": float(t["dec"]),
            "common": t.get("common", target),
            "type": t.get("type", ""),
            "pm_ra": t.get("pm_ra"),
            "pm_dec": t.get("pm_dec"),
            "pos_epoch": t.get("pos_epoch"),
        },
        "paths": [],
        "epochs": [],
        "catalogs": {},
        "collection": config.SPHEREX_COLLECTION,
    }


@_rate_limited_route("get", "/api/recovery", "30/minute")
def get_recovery(request: Request = None,
                 target: str = Query("WISE_0855", max_length=80, pattern=r"^[A-Za-z0-9_]+$"), source: str = Query("cache")):
    """
    Run injection-recovery on the specified target and return the grid.

    Uses the first and last epoch with matched masks, like the dashboard.
    Runtime fields and source=live fetch current cutouts from IRSA for each
    request; live data is not reused from the dashboard or a process cache.
    """
    # B6 FIX: custom fields live only in FIELD_REGISTRY (in-process); they
    # are never in the GCS manifest, so checking the manifest first returns
    # 404.  Short-circuit to the live pipeline for any registered custom field
    # regardless of what the caller put in ?source=.
    if target in FIELD_REGISTRY:
        meta = FIELD_REGISTRY[target]
        if meta is None:
            return JSONResponse(status_code=404,
                                content={"error": f"Custom field '{target}' not found."})
        ra = meta["meta"]["ra"]
        dec = meta["meta"]["dec"]
        try:
            cutouts, _, _ = live_exposures(
                target, meta, progress=_PROGRESS_CALLBACK.get())
        except Exception as e:
            return JSONResponse(
                status_code=422 if isinstance(e, CoverageError) else 502,
                content={"error": (str(e) if isinstance(e, CoverageError)
                                   else _public_error("Live fetch from IRSA failed", e))})
        sources = [c["data"] for c in cutouts]
        epochs = [c["epoch"] for c in cutouts]
        bands = exposure_bands(meta, sources, cutouts)
        return _run_recovery_pipeline(
            target, ra, dec, sources, epochs, bands, source="live")

    store = _snapshot_store()
    if store is not None:
        live_request = str(source).lower() == "live"
        manifest = (_optional_published_manifest(store) if live_request
                    else _published_manifest(store))
        entry = (manifest or {}).get("targets", {}).get(target)
        if not live_request:
            if not entry:
                raise HTTPException(
                    status_code=404,
                    detail=f"No published recovery grid exists for target '{target}'.")
            return _published_object_response(
                store, entry.get("recovery"), f"recovery grid for {target}")
        if entry:
            if entry.get("meta_object"):
                meta = _published_json(
                    store, entry.get("meta_object"), f"metadata for {target}")
            else:
                # B7 FIX: same as get_dashboard — fall back to TARGETS registry.
                meta = _meta_from_targets_registry(target)
        else:
            meta = load_target(target)
    else:
        meta = load_target(target)
    if meta is None and str(source).lower() == "live":
        meta = _meta_from_targets_registry(target)
    if meta is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No published or registered field '{target}'."}
        )

    ra = meta["meta"]["ra"]
    dec = meta["meta"]["dec"]
    if str(source).lower() == "live" or meta.get("custom"):
        try:
            cutouts, _, _ = live_exposures(
                target, meta, progress=_PROGRESS_CALLBACK.get())
        except Exception as e:
            return JSONResponse(status_code=422 if isinstance(e, CoverageError) else 502,
                                content={"error": (str(e) if isinstance(e, CoverageError)
                                                   else _public_error("Live fetch from IRSA failed", e))})
        sources = [c["data"] for c in cutouts]
        epochs = [c["epoch"] for c in cutouts]
        bands = exposure_bands(meta, sources, cutouts)
    else:
        present = [i for i, p in enumerate(meta["paths"]) if os.path.exists(p)]
        sources = [meta["paths"][i] for i in present]
        all_ep = meta.get("epochs")
        epochs = ([all_ep[i] for i in present]
                  if all_ep is not None and len(all_ep) == len(meta["paths"])
                  else None)
        bands = exposure_bands(meta, sources)

    return _run_recovery_pipeline(target, ra, dec, sources, epochs, bands, source=source)


@_rate_limited_route("get", "/api/recovery/stream", "10/minute")
async def stream_recovery(request: Request = None,
                          target: str = Query("WISE_0855", max_length=80),
                          source: str = Query(
                              "live", pattern="^(cache|live)$"),
                          result: str = _RESULT_PARAM):
    return _progress_stream(
        lambda: get_recovery(target=target, source=source),
        f"Starting {'fresh IRSA retrieval' if source == 'live' else 'cached-cutout'} "
        f"recovery for {target}",
        compress=result == "gzip",
    )


MOSAIC_CACHE = {"t": 0.0, "payload": None}
MOSAIC_TTL = int(os.environ.get("SPHEREX_MOSAIC_TTL", "900"))


def _thumbnail(arr, size=48):
    """Block-average a 180x180 map down to a small square, rounded to 2 dp."""
    a = np.asarray(arr, dtype=float)
    a = np.where(np.isfinite(a), a, 0.0)
    ny, nx = a.shape
    fy, fx = ny // size, nx // size
    if fy < 1 or fx < 1:
        return a[:size, :size].round(2).tolist()
    trimmed = a[:fy * size, :fx * size]
    blocks = trimmed.reshape(size, fy, size, fx).mean(axis=(1, 3))
    return np.round(blocks, 2).tolist()


_FINGERPRINT_PATTERN = "^[0-9a-f]{0,64}$"     # step1_ingest.source_fingerprint
_DISPLAY_NAME_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>&\"'`\\]")


def field_input_error(ra, dec, name="", size=0.2, slots=0):
    """Why an "Open any field" request is invalid, or None (security review S1)."""
    try:
        ra, dec, size = float(ra), float(dec), float(size)
    except (TypeError, ValueError):
        return "RA, Dec and size must be numbers."
    if (not (np.isfinite(ra) and np.isfinite(dec))
            or not 0 <= ra <= 360 or not -90 <= dec <= 90):
        return ("RA must be between 0 and 360 degrees and Dec between "
                "-90 and 90 degrees.")
    if not np.isfinite(size) or not 0.05 <= size <= config.Production.MAX_FIELD_SIZE_DEG:
        return (f"size must be between 0.05 and "
                f"{config.Production.MAX_FIELD_SIZE_DEG:g} degrees.")
    if (isinstance(slots, bool) or not isinstance(slots, int)
            or not 0 <= slots <= config.Production.MAX_LIVE_SLOTS):
        return f"slots must be between 0 and {config.Production.MAX_LIVE_SLOTS}."
    if not isinstance(name, str) or len(name) > config.Production.FIELD_NAME_MAX_LEN:
        return (f"name must be at most {config.Production.FIELD_NAME_MAX_LEN} "
                "characters.")
    return None


def _display_name(name):
    """A field label safe to echo back: no control or HTML characters."""
    return _DISPLAY_NAME_UNSAFE.sub("", str(name or "")).strip()[:80]


@_rate_limited_route("get", "/api/field", "5/minute")
def add_field(request: Request = None,
              ra: float = Query(...), dec: float = Query(...),
              name: str = Query("", max_length=80),
              size: float = Query(0.2, ge=0.05, le=0.5),
              slots: int = Query(0, ge=0, le=12),
              epochs: str = Query("firstlast", pattern="^(firstlast|all)$"),
              catalogs: int = Query(1, ge=0, le=1),
              known: str = Query("", max_length=128, pattern=_FINGERPRINT_PATTERN)):
    error = field_input_error(ra, dec, name, size, slots)
    if error is None and epochs not in ("firstlast", "all"):
        error = "epochs must be 'firstlast' or 'all'."
    if error is None and not re.fullmatch(_FINGERPRINT_PATTERN, str(known or "")):
        error = "known must be a source fingerprint (hex)."
    if error:
        return JSONResponse(status_code=422, content={"error": error})
    if not FIELD_REQUEST_LIMIT.acquire(blocking=False):
        return JSONResponse(
            status_code=429,
            content={"error": "The field loader is busy. Wait for an active request "
                              "to finish, then try again."})
    try:
        return _register_field(ra, dec, name, size, slots, epochs, catalogs,
                               known_fingerprint=known)
    finally:
        FIELD_REQUEST_LIMIT.release()


@_rate_limited_route("get", "/api/field/stream", "5/minute")
async def stream_field(request: Request = None,
                       ra: float = Query(...), dec: float = Query(...),
                       name: str = Query("", max_length=80),
                       size: float = Query(0.2, ge=0.05, le=0.5),
                       slots: int = Query(0, ge=0, le=12),
                       epochs: str = Query("firstlast", pattern="^(firstlast|all)$"),
                       catalogs: int = Query(1, ge=0, le=1),
                       known: str = Query("", max_length=128,
                                          pattern=_FINGERPRINT_PATTERN),
                       result: str = _RESULT_PARAM):
    error = field_input_error(ra, dec, name, size, slots)
    if error:
        return JSONResponse(status_code=422, content={"error": error})
    return _progress_stream(
        lambda: add_field(
            ra=ra, dec=dec, name=name, size=size, slots=slots,
            epochs=epochs, catalogs=catalogs, known=known,
        ),
        "Starting a fresh IRSA request for the custom field",
        compress=result == "gzip",
    )


def _manifest_target_entry(target):
    """
    B5: Return a minimal meta-like dict if 'target' exists in the published
    snapshot manifest or the TARGETS registry, so name-collision checks work
    even when no local files are present (cloud-only deployments).
    """
    # Check TARGETS registry first (cheapest).
    try:
        from step1_ingest import TARGETS
        if target in TARGETS:
            t = TARGETS[target]
            return {"meta": {"ra": float(t["ra"]), "dec": float(t["dec"])}}
    except Exception:
        pass
    # Check GCS manifest.
    store = _snapshot_store()
    if store is None:
        return None
    manifest = _optional_published_manifest(store)
    entry = (manifest or {}).get("targets", {}).get(target)
    if not entry:
        return None
    # Build a minimal meta-like object from whatever RA/dec we can recover.
    if entry.get("meta_object"):
        try:
            meta = store.read_json(entry["meta_object"])
            if meta:
                return meta
        except Exception:
            pass
    return None


def _register_field(ra, dec, name, size, slots, epochs, catalogs,
                    known_fingerprint=""):
    """
    Load a field that is not in data_cache/ -- any position on the sky.

    The cutouts are fetched from IRSA into RAM (nothing is written to disk),
    the full pipeline runs, and its dashboard is returned in this response so
    clients do not depend on process-local state for a later request.
    """
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or (
        f"Field_{ra:.2f}_{dec:.2f}".replace("-", "m").replace(".", "p"))
    # B5 FIX: check against the GCS manifest and the TARGETS registry too, not
    # only local disk / FIELD_REGISTRY.  In cloud-only mode local disk is
    # empty so collisions with registered snapshot fields were silently missed.
    existing = load_target(safe)
    if existing is None:
        existing = _manifest_target_entry(safe)
    safe = safe[:80]
    existing_meta = (existing or {}).get("meta") or {}
    if (existing and
            (float(existing_meta.get("ra", float("nan"))) != ra
             or float(existing_meta.get("dec", float("nan"))) != dec)):
        base = safe[:55]
        suffix = f"_{ra:.4f}_{dec:.4f}".replace("-", "m").replace(".", "p")
        safe = candidate = f"{base}{suffix}"
        # Security review S3: the old loop re-tried the same 72-character
        # name forever once it was taken.  Numbered, bounded suffixes instead.
        for attempt in range(1, 21):
            if load_target(safe) is None and _manifest_target_entry(safe) is None:
                break
            safe = f"{candidate[:74]}_{attempt}"
        else:
            return JSONResponse(
                status_code=409,
                content={"error": "Could not find a free name for this field; "
                                  "choose another name."})
    meta = {
        "target": safe,
        "meta": {"ra": float(ra), "dec": float(dec),
                 "common": _display_name(name) or safe,
                 "type": "Custom field"},
        "paths": [], "epochs": [], "catalogs": {},
        "collection": config.SPHEREX_COLLECTION, "cutout_size_deg": size,
        "custom": True,
    }
    from datetime import datetime, timezone
    checked_at = datetime.now(timezone.utc).isoformat()
    if known_fingerprint:
        # The browser holds a saved result for these coordinates: ask IRSA
        # only for its listing first. The same images mean reprocessing
        # would reproduce the saved result, so nothing else is fetched.
        _emit_progress("irsa_query", "Checking IRSA for new images at these coordinates")
    else:
        _emit_progress("irsa_query", "Querying IRSA SIA2")
    listing_future = _CATALOG_POOL.submit(
        query_sia2, float(ra), float(dec), radius_deg=0.1, use_cache=False)
    if known_fingerprint:
        try:
            rows = listing_future.result()
        except Exception as e:
            _LOGGER.warning("IRSA listing failed for a custom field: %s: %s",
                            type(e).__name__, str(e)[:300])
            return JSONResponse(
                status_code=502,
                content={"error": f"IRSA listing query failed ({type(e).__name__}).",
                         "hint": "IRSA may be busy; retry in a minute."})
        fingerprint = source_fingerprint(rows, float(ra), float(dec))
        if fingerprint == known_fingerprint:
            return {
                "target": safe, "ra": float(ra), "dec": float(dec),
                "unchanged": True,
                "irsa_check": {"checked_at": checked_at, "irsa_rows": len(rows),
                               "source_fingerprint": fingerprint,
                               "reused_saved_result": True},
                "note": "IRSA lists the same images as the saved result; "
                        "nothing was reprocessed",
            }
        _emit_progress("irsa_query",
                       f"IRSA lists {len(rows)} images, including new ones; processing fresh")
    if catalogs:
        # Without catalogues every detection in a custom field would be a
        # CANDIDATE. Gaia + AllWISE (IRSA TAP) and VSX (VizieR) are looked up
        # in parallel with each other and with the IRSA listing and cutout
        # downloads; the dashboard waits for them only at triage. Each
        # catalogue reports its own status, so "unavailable" is never shown
        # as "no catalogue stars".
        _emit_progress("catalogs",
                       "Querying Gaia, AllWISE and VSX while IRSA is queried")
        meta["_catalogs_future"] = _start_catalog_lookup(ra, dec)
    try:
        listing_rows = listing_future.result()
    except Exception as e:
        meta.pop("_catalogs_future", None)
        _LOGGER.warning("IRSA listing failed for a custom field: %s: %s",
                        type(e).__name__, str(e)[:300])
        return JSONResponse(
            status_code=502,
            content={"error": f"IRSA listing query failed ({type(e).__name__}).",
                     "hint": "IRSA may be busy; retry in a minute."})
    meta["_listing_rows"] = listing_rows
    _emit_progress("irsa_query", f"IRSA returned {len(listing_rows)} images for this position")
    previous_meta = FIELD_REGISTRY.get(safe)
    FIELD_REGISTRY[safe] = meta

    payload = get_dashboard(target=safe, source="live", live_slots=slots,
                            live_size=size, live_epochs=epochs)
    meta.pop("_catalogs_future", None)       # an early failure never waited for it
    meta.pop("_listing_rows", None)
    if not isinstance(payload, dict):
        if previous_meta is None:
            FIELD_REGISTRY.pop(safe, None)
        else:
            FIELD_REGISTRY[safe] = previous_meta
        return payload

    FIELD_DASH_CACHE[safe] = payload
    while len(FIELD_REGISTRY) > max(1, MAX_CUSTOM_FIELDS):
        oldest = next(key for key in FIELD_REGISTRY if key != safe)
        FIELD_REGISTRY.pop(oldest)
        FIELD_DASH_CACHE.pop(oldest, None)
    MOSAIC_CACHE["payload"] = None          # force the all-sky tiles to refresh
    return {
        "target": safe,
        "common": payload.get("common"),
        "ra": payload.get("ra"),
        "dec": payload.get("dec"),
        "n_detections": payload.get("stats", {}).get("n_detections"),
        "max_snr": payload.get("stats", {}).get("max_snr"),
        "n_exposures": payload.get("stats", {}).get("n_exposures"),
        "n_epochs": payload.get("stats", {}).get("n_epochs"),
        "dashboard": payload,
        "irsa_check": {"checked_at": checked_at, "irsa_rows": len(listing_rows),
                       "source_fingerprint": source_fingerprint(
                           listing_rows, float(ra), float(dec)),
                       "reused_saved_result": False},
        "note": "fetched from IRSA for this request; not added to scheduled snapshots",
    }


def mosaic_tile(dashboard, thumb=48):
    """Build one available all-sky tile from a dashboard response."""
    stats = dashboard.get("stats", {})
    return {
        "target": dashboard["target"],
        "common": dashboard.get("common", dashboard["target"]),
        "ra": dashboard["ra"],
        "dec": dashboard["dec"],
        "fov_arcmin": dashboard["map"]["fov_arcmin"],
        "thumb": _thumbnail(dashboard["zogy_snr"], size=thumb),
        "thumb_npix": thumb,
        "available": True,
        "reason": None,
        "n_detections": stats.get("n_detections", 0),
        "n_known": stats.get("n_known", 0),
        "n_candidate": stats.get("n_candidate", 0),
        "max_snr": stats.get("max_snr"),
        "baseline_days": stats.get("baseline_days"),
        "sigma1": stats.get("sigma1"),
        "sigma2": stats.get("sigma2"),
        "n_exposures": stats.get("n_exposures"),
        "n_epochs": stats.get("n_epochs"),
        "n_gaia": stats.get("n_gaia"),
        "n_vsx": stats.get("n_vsx"),
        "source": dashboard.get("source", "cache"),
    }


def unavailable_mosaic_tile(entry, reason, thumb=48):
    """Keep a target locatable even when it has no comparable epoch data."""
    return {
        "target": entry["name"],
        "common": entry.get("common", entry["name"]),
        "ra": entry["ra"],
        "dec": entry["dec"],
        "fov_arcmin": 9.0,
        "thumb": [],
        "thumb_npix": thumb,
        "available": False,
        "reason": reason,
        "n_detections": None,
        "n_known": None,
        "n_candidate": None,
        "max_snr": None,
        "baseline_days": None,
        "n_exposures": None,
        "n_epochs": None,
        "source": "cache",
    }


def mosaic_payload(tiles):
    available = [tile for tile in tiles if tile.get("available")]
    return {
        "tiles": tiles,
        "n_fields": len(tiles),
        "n_available": len(available),
        "n_unavailable": len(tiles) - len(available),
        "sky_area_deg2": round(sum(
            (tile["fov_arcmin"] / 60.0) ** 2 for tile in available), 6),
    }


def live_mosaic_tile(target, thumb=48):
    """Fetch one registered field fresh from IRSA and return its all-sky tile.

    The fresh all-sky view calls this once per field, in sequence: a single
    request for all 18 would run ~12 minutes with no partial result.
    """
    registry = _meta_from_targets_registry(target)
    if registry is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"'{target}' is not a registered field."})
    entry = {"name": target, "common": registry["meta"]["common"],
             "ra": registry["meta"]["ra"], "dec": registry["meta"]["dec"]}
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    started = time.monotonic()
    result = get_dashboard(target=target, source="live", live_slots=0,
                           live_size=0.2, live_epochs="firstlast")
    payload = result if isinstance(result, dict) else None
    if payload is None or payload.get("error") or payload.get("zogy_snr") is None:
        if payload is None:
            try:
                payload = json.loads(result.body.decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
        tile = unavailable_mosaic_tile(
            entry, payload.get("error") or "The fresh IRSA run returned no map.",
            thumb=thumb)
    else:
        tile = mosaic_tile(payload, thumb=thumb)
    tile["source"] = "live"
    tile["generated_at"] = checked_at
    tile["irsa_check"] = (payload or {}).get("irsa_check")
    tile["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return tile


@_rate_limited_route("get", "/api/mosaic/tile/stream", "60/minute")
async def stream_mosaic_tile(request: Request = None,
                             target: str = Query(..., max_length=80),
                             result: str = _RESULT_PARAM):
    return _progress_stream(
        lambda: live_mosaic_tile(target),
        f"Fetching {target} fresh from IRSA for the all-sky view",
        compress=result == "gzip",
    )


@_rate_limited_route("get", "/api/mosaic", "60/minute")
def get_mosaic(request: Request = None,
               thumb: int = Query(48, ge=16, le=96),
               refresh: int = Query(0, ge=0, le=1),
               source: str = Query("cache")):
    """
    One small tile per cached target, for the all-sky view.

    Each tile carries the field centre, size, a down-sampled significance map,
    the detection split and the headline statistics -- enough to draw an
    all-sky chart and to decide which field is worth opening.
    """
    store = _snapshot_store()
    if store is not None and str(source).lower() != "live":
        try:
            manifest = _published_manifest(store)
        except HTTPException:
            manifest = None            # no published snapshot yet: try the cache
        if manifest and manifest.get("mosaic"):
            return _published_object_response(
                store, manifest.get("mosaic"), "all-sky mosaic")

    if source.lower() == "live":
        return JSONResponse(
            status_code=501,
            content={
                "error": "Fresh all-sky IRSA mosaics are not implemented. "
                         "This endpoint only serves cache-derived tiles.",
                "hint": "Select one field to fetch its cutouts from IRSA; "
                        "fresh all-sky cutouts and processing cannot complete "
                        "within 200 ms."
            })

    # N3: no snapshot store.  Local development still gets one locatable tile
    # per cached field (no thumbnail: rendering 18 pipelines in one request
    # would be slower than opening the field), so All-sky is usable and every
    # tile can be clicked.
    local = _local_target_entries()
    if local:
        reason = ("All-sky thumbnails come from the published Cloud snapshot; "
                  "click to open this field from the local cache.")
        return mosaic_payload([unavailable_mosaic_tile(entry, reason)
                               for entry in local])

    raise HTTPException(
        status_code=503,
        detail="No published snapshot and no local data cache is available. "
               "Set SPHEREX_SNAPSHOT_BUCKET to the GCS bucket name, or run "
               "`python step1_ingest.py` to fill data_cache/."
    )


# v2 subsystems add their endpoints as routers in spherexlens/routes/; they
# must be included before the catch-all static mount below.
from spherexlens.routes import all_routers  # noqa: E402

for _router in all_routers():
    app.include_router(_router)

app.mount("/", StaticFiles(directory="static", html=True), name="static")
