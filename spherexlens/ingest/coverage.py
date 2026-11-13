"""
spherexlens.ingest.coverage
"Which archives have data here?" -- every adapter's cheap coverage() at once.

coverage_report(ra, dec, radius_deg) runs the adapters concurrently on a
shared thread pool and returns one entry per archive, in a fixed order:

    {"archive": "spherex", "available": true, "count": 18, "detail": "3 epochs, ..."}
    {"archive": "neossat", "available": false, "count": 0, "error": "..."}

A failure is an entry with available=false and the error -- never a missing
row.  An archive slower than the time box gets ``"pending": true`` and an
error saying so; its query keeps running in the background and its answer
goes into the cache, so the next request (or dashboard load) has it.

Caching: results are kept in memory for COVERAGE_CACHE_TTL_S (1 day);
failures for FAILURE_TTL_S (10 min) so a dead archive is not hammered.
Positions are keyed to ~0.4" so a re-centred field reuses the entry.  The
adapters' own HTTP cache (data_cache/archive_cache) also survives restarts.

Dashboard enricher ("coverage", order 30): payload["coverage"] for the
target position, waiting at most COVERAGE_ENRICH_BUDGET_S (3 s) in total --
it never blocks the dashboard longer than that.  Set
SPHEREX_COVERAGE_ENRICH=false to skip it entirely (the test suite does).
"""
import importlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

import config
from spherexlens.enrich import register

FAILURE_TTL_S = 600
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="coverage")
_LOCK = threading.Lock()
_CACHE = {}           # key -> (expires_monotonic, entry dict)
_INFLIGHT = {}        # key -> Future

# Owned by other subsystems; used when present in this build.
OPTIONAL_ADAPTERS = (("spherexlens.ingest.jpl", "jpl_sb_ident"),
                     ("spherexlens.ingest.ssois", "ssois"))


def default_adapters():
    from spherexlens.ingest.base import Adapter
    from spherexlens.ingest.cadc import CADCNeossat
    from spherexlens.ingest.gaia import GaiaFallback
    from spherexlens.ingest.neowise import NEOWISE
    from spherexlens.ingest.spherex import SpherexSIA
    from spherexlens.ingest.vsx import VSX
    from spherexlens.ingest.wise import AllWISE
    adapters = [SpherexSIA(), NEOWISE(), GaiaFallback(), CADCNeossat(),
                AllWISE(), VSX()]
    for module, name in OPTIONAL_ADAPTERS:
        try:
            mod = importlib.import_module(module)
        except ImportError:
            continue
        for obj in vars(mod).values():
            if (isinstance(obj, type) and issubclass(obj, Adapter)
                    and obj is not Adapter and getattr(obj, "name", None) == name):
                try:
                    adapters.append(obj())
                except Exception:
                    pass
                break
    return adapters


def _key(name, ra, dec, radius_deg, t_start, t_end):
    return (name, round(float(ra), 4), round(float(dec), 4),
            round(float(radius_deg), 4), t_start, t_end)


def _run(adapter, key, ra, dec, radius_deg, t_start, t_end):
    t0 = time.time()
    try:
        cov = adapter.coverage(ra, dec, radius_deg, t_start=t_start, t_end=t_end)
        entry, ttl = cov.to_dict(), config.COVERAGE_CACHE_TTL_S
    except Exception as e:
        entry = {"archive": adapter.name, "available": False, "count": 0,
                 "detail": "", "error": f"{type(e).__name__}: {e}"[:300]}
        ttl = FAILURE_TTL_S
    entry["seconds"] = round(time.time() - t0, 2)
    entry["checked_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _LOCK:
        _CACHE[key] = (time.monotonic() + ttl, entry)
        _INFLIGHT.pop(key, None)
    return entry


def clear_cache():
    with _LOCK:
        _CACHE.clear()


def coverage_report(ra, dec, radius_deg=None, t_start=None, t_end=None,
                    timeout_s=None, adapters=None, use_cache=True):
    """One coverage entry per archive; never raises, never waits > timeout_s."""
    radius_deg = config.COVERAGE_RADIUS_DEG if radius_deg is None else radius_deg
    timeout_s = config.COVERAGE_TIMEOUT_S if timeout_s is None else timeout_s
    adapters = default_adapters() if adapters is None else adapters
    slots, futures = [], {}
    now = time.monotonic()
    for a in adapters:
        key = _key(a.name, ra, dec, radius_deg, t_start, t_end)
        with _LOCK:
            hit = _CACHE.get(key) if use_cache else None
            if hit and hit[0] > now:
                slots.append((a.name, {**hit[1], "cached": True}))
                continue
            fut = _INFLIGHT.get(key)
            if fut is None:
                fut = _POOL.submit(_run, a, key, ra, dec, radius_deg, t_start, t_end)
                _INFLIGHT[key] = fut
        slots.append((a.name, fut))
        futures[a.name] = fut
    if futures:
        wait(list(futures.values()), timeout=max(0.0, float(timeout_s)))
    out = []
    for name, item in slots:
        if isinstance(item, dict):
            out.append(item)
        elif item.done():
            try:
                out.append(dict(item.result()))
            except Exception as e:          # _run never raises; belt and braces
                out.append({"archive": name, "available": False, "count": 0,
                            "detail": "", "error": str(e)})
        else:
            out.append({"archive": name, "available": False, "count": 0,
                        "detail": "", "pending": True,
                        "error": f"no answer within {timeout_s:g} s "
                                 "(still checking; ask again shortly)"})
    return out


def _target_position(payload, ctx):
    tp = payload.get("target_position") or {}
    if tp.get("ra") is not None and tp.get("dec") is not None:
        return tp["ra"], tp["dec"]
    mp = payload.get("map") or {}
    if mp.get("ra") is not None and mp.get("dec") is not None:
        return mp["ra"], mp["dec"]
    return (ctx or {}).get("ra"), (ctx or {}).get("dec")


@register("coverage", order=30)
def add_coverage(payload, ctx):
    if os.environ.get("SPHEREX_COVERAGE_ENRICH", "true").lower() == "false":
        return
    ra, dec = _target_position(payload, ctx)
    if ra is None or dec is None:
        return
    payload["coverage"] = coverage_report(
        float(ra), float(dec), config.COVERAGE_RADIUS_DEG,
        timeout_s=config.COVERAGE_ENRICH_BUDGET_S)
