"""
live_ingest.py

OPTIONAL live mode -- NOT one of the ten blueprint files.

Instead of reading cutouts from data_cache/, this module asks IRSA for the
cutouts, keeps the FITS bytes in memory, and hands them straight to the
pipeline.  Nothing is written to disk.

    from live_ingest import live_cutouts
    cutouts = live_cutouts(ra, dec, size_deg=0.2)   # -> list of dicts with bytes

Trade-off, honestly:

    cached files     ~10 ms to load,  ~200 MB of disk, can go stale
    live streaming   seconds-to-minutes on the first call (parallel fetches),
                     0 MB of disk, fetched on demand from IRSA, dies with the network
"""

import os

import config
import io
import time
import threading
import concurrent.futures as futures

import numpy as np
import requests
from astropy.io import fits
from astropy.wcs import WCS
from astropy.time import Time

from step2_coadd import BAD_BITS
from step1_ingest import (IRSA_CUTOUT, DEFAULT_BANDS, query_sia2,
                          select_exposures, _separation_deg)

DEFAULT_WORKERS = 4
# (connect, read) per cutout attempt.  A 5 MB cutout normally takes 5-20 s;
# a stalled IRSA connection must fail long before Cloud Run's 900 s limit.
FETCH_TIMEOUT = (10, 90)
# Budget for all cutout downloads of one live fetch (incl. the all-band
# retry).  Past it no new download or retry starts.  Worst case per request:
# SIA2 listing 3x(10+90)+45 s + this budget + one in-flight attempt (100 s)
# = 865 s, inside Cloud Run's 900 s request limit.
LIVE_DEADLINE_SECONDS = float(os.environ.get("SPHEREX_LIVE_DEADLINE_SECONDS", "420"))

_SESSIONS = threading.local()


def _session():
    """One keep-alive HTTP session per download thread (no repeated TLS setup)."""
    session = getattr(_SESSIONS, "session", None)
    if session is None:
        session = requests.Session()
        _SESSIONS.session = session
    return session


def fetch_cutout_bytes(row, ra, dec, size_deg=0.2, retries=2, deadline=None):
    """
    Fetch one cutout and return the raw FITS bytes (never written to disk).

    Returns (bytes | None, meta).  The meta always describes what was asked
    for, so a failure is still traceable.
    """
    url = row.get("access_url")
    meta = {
        "obs_id": row.get("obs_id"),
        "band": row.get("energy_bandpassname"),
        "detector": str(row.get("energy_bandpassname", "")).replace("SPHEREx-", ""),
        "url": url,
        "mjd": float(row["t_min"]) if row.get("t_min") else None,
        "collection": row.get("obs_collection"),
        "cutout_size_deg": size_deg,
        "bytes": None,
    }
    if not url:
        meta["error"] = "row has no access_url"
        return None, meta

    params = {"path": url.split("/ibe/data/")[-1],
              "center": f"{ra},{dec}",
              "size": str(size_deg)}
    last = None
    for attempt in range(retries + 1):
        try:
            resp = _session().get(
                IRSA_CUTOUT, params=params,
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
                timeout=FETCH_TIMEOUT)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "fits" in ctype:
                meta["bytes"] = len(resp.content)
                meta["fetched_at"] = time.time()
                return resp.content, meta
            last = f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as e:                      # timeouts, resets
            last = f"{type(e).__name__}: {e}"
        if deadline is not None and time.monotonic() > deadline:
            last = f"{last}; no retry: the request's IRSA time budget is spent"
            break
        time.sleep(2 * (attempt + 1))
    meta["error"] = last
    return None, meta


def _check_bytes(data, ra, dec, min_margin=12, max_masked=0.5):
    """
    The same acceptance test as step1_ingest.validate_cutout(), but on bytes in
    memory: the target must sit well inside the array and the FLAGS mask must
    leave most pixels usable.
    """
    try:
        with fits.open(io.BytesIO(data)) as hdul:
            hdu = hdul["IMAGE"] if "IMAGE" in hdul else hdul[0]
            ny, nx = hdu.data.shape
            flags = hdul["FLAGS"].data if "FLAGS" in hdul else None
            wcs = WCS(hdu.header)
            pix = wcs.all_world2pix([[ra, dec]], 0, tolerance=1e-8,
                                    maxiter=100, quiet=True)[0]
            header = hdu.header
    except Exception as e:
        return False, f"unreadable ({e})", {}

    x, y = float(pix[0]), float(pix[1])
    info = {"shape": [int(ny), int(nx)],
            "obs_id": header.get("OBSID"),
            "detector": f"D{header.get('DETECTOR')}" if header.get("DETECTOR") else None,
            "date": str(header.get("DATE", ""))[:10] or None}
    if not (min_margin <= x <= nx - 1 - min_margin
            and min_margin <= y <= ny - 1 - min_margin):
        return False, f"target outside cutout ({x:.0f},{y:.0f} of {nx}x{ny})", info
    if flags is not None:
        masked = float(((flags & BAD_BITS) != 0).mean())
        info["masked_fraction"] = round(masked, 4)
        if masked > max_masked:
            return False, f"{100 * masked:.0f}% of pixels masked", info
    return True, "ok", info


def live_cutouts(ra, dec, radius_deg=0.1, collection=config.SPHEREX_COLLECTION,
                 size_deg=0.2, bands=None, epoch_mode="all", per_slot=1,
                 max_slots=None, workers=DEFAULT_WORKERS, log=print,
                 progress=None, rows=None, deadline=None):
    """
    Fetch a target's cutouts straight from IRSA into memory.

    Returns a list of dicts: {"data": <FITS bytes>, "epoch": int, "band": str,
    "obs_id": str, "url": str, "mjd": float, "shape": [ny, nx], ...}
    Slots are fetched concurrently and validated in memory.

    ``rows`` reuses an SIA2 listing fetched moments ago for the same field
    (one IRSA query per request instead of one per band set).  ``deadline``
    is a time.monotonic() value after which no new download is started.
    """
    bands = tuple(bands or DEFAULT_BANDS)

    if rows is None:
        if progress:
            progress({"stage": "irsa_query", "message": "Querying IRSA SIA2"})
        rows = query_sia2(ra, dec, radius_deg=radius_deg, collection=collection,
                          use_cache=False)
    slots, n_epochs = select_exposures(rows, ra, dec, bands=bands,
                                       epoch_mode=epoch_mode, per_slot=per_slot)
    if max_slots:
        slots = _limit_slots(slots, max_slots)
    log(f"live: {len(rows)} SIA2 rows, {n_epochs} epoch(s), {len(slots)} slot(s) queued")
    if progress:
        progress({
            "stage": "irsa_query",
            "message": f"IRSA returned {len(rows)} rows across {n_epochs} epoch(s)",
            "rows": len(rows),
            "epochs": n_epochs,
        })
        progress({
            "stage": "cutouts",
            "message": f"Fetching cutouts (0/{len(slots)})",
            "completed": 0,
            "total": len(slots),
        })

    progress_lock = threading.Lock()
    progress_counts = {"completed": 0}

    def work(item):
        epoch, candidates = item
        try:
            ordered = sorted(candidates,
                             key=lambda r: _separation_deg(ra, dec,
                                                           float(r.get("s_ra", ra)),
                                                           float(r.get("s_dec", dec))))
            for row in ordered:
                if deadline is not None and time.monotonic() > deadline:
                    message = (f"Epoch {epoch} {row.get('energy_bandpassname')} "
                               "skipped: IRSA is responding too slowly for this "
                               "request's time budget")
                    log(f"live: {message}")
                    if progress:
                        progress({"stage": "cutouts", "message": message,
                                  "outcome": "failed"})
                    return None
                data, meta = fetch_cutout_bytes(row, ra, dec, size_deg=size_deg,
                                                deadline=deadline)
                band = meta.get("band", "unknown")
                if data is None:
                    message = (f"Epoch {epoch} {band} fetch failed: "
                               f"{meta.get('error')}")
                    log(f"live: {message}")
                    if progress:
                        progress({"stage": "cutouts", "message": message,
                                  "outcome": "failed"})
                    continue
                if progress:
                    progress({"stage": "validation",
                              "message": f"Validating epoch {epoch} {band} cutout"})
                ok, reason, info = _check_bytes(data, ra, dec)
                if not ok:
                    message = f"Epoch {epoch} {band} rejected: {reason}"
                    log(f"live: {message}")
                    if progress:
                        progress({"stage": "cutouts", "message": message,
                                  "outcome": "rejected"})
                    continue
                out = dict(meta)
                out.update({k: v for k, v in info.items() if v is not None})
                out["epoch"] = epoch
                out["data"] = data
                out["bytes"] = len(data)
                log(f"live: epoch {epoch} {out.get('band')} ok "
                    f"({out['bytes'] / 1e6:.1f} MB, {out.get('shape')})")
                if progress:
                    progress({"stage": "cutouts",
                              "message": f"Epoch {epoch} {band} cutout validated",
                              "outcome": "accepted"})
                return out
            return None
        finally:
            if progress:
                with progress_lock:
                    progress_counts["completed"] += 1
                    completed = progress_counts["completed"]
                progress({
                    "stage": "cutouts",
                    "message": f"Processed cutout slots ({completed}/{len(slots)})",
                    "completed": completed,
                    "total": len(slots),
                })

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, slots))

    accepted = [r for r in results if r]
    accepted.sort(key=lambda r: (r["epoch"], str(r.get("band"))))
    log(f"live: {len(accepted)}/{len(slots)} slots fetched")
    return accepted


def _limit_slots(slots, max_slots):
    """Bound live work while prioritizing matched bands across epochs."""
    if max_slots < 1:
        return []
    if len(slots) <= max_slots:
        return slots

    by_epoch = {}
    for slot in slots:
        epoch, candidates = slot
        band = candidates[0].get("energy_bandpassname") if candidates else None
        by_epoch.setdefault(epoch, {})[band] = slot

    epochs = list(by_epoch)
    selected = []
    selected_ids = set()

    def add(slot):
        identity = id(slot)
        if identity not in selected_ids and len(selected) < max_slots:
            selected.append(slot)
            selected_ids.add(identity)

    if len(epochs) >= 2:
        first, last = epochs[0], epochs[-1]
        common_bands = [
            band for band in by_epoch[first]
            if band in by_epoch[last] and band is not None
        ]
        for band in common_bands:
            if len(selected) + 2 > max_slots:
                break
            add(by_epoch[first][band])
            add(by_epoch[last][band])

        # If there is no common band, preserve representation from both ends
        # so the caller can report the actual coverage problem.
        if not selected and max_slots >= 2:
            for epoch in (first, last):
                for slot in by_epoch[epoch].values():
                    add(slot)
                    break

    # Fill remaining capacity by rotating through epochs instead of consuming
    # it all from the first epoch.
    while len(selected) < max_slots:
        changed = False
        for epoch in epochs:
            for slot in by_epoch[epoch].values():
                if id(slot) not in selected_ids:
                    add(slot)
                    changed = True
                    break
        if not changed:
            break
    return selected


def describe_live_cutouts(cutouts):
    """Provenance rows for cutouts fetched in live mode (no local paths)."""
    out = []
    for c in cutouts:
        entry = {k: c.get(k) for k in
                 ("epoch", "band", "detector", "obs_id", "mjd", "url",
                  "collection", "cutout_size_deg", "bytes", "shape")}
        if entry.get("mjd") and not entry.get("date"):
            try:
                entry["date"] = Time(float(entry["mjd"]), format="mjd").iso[:10]
            except Exception:
                pass
        entry["file"] = f"live:{entry.get('obs_id')}"
        entry["live"] = True
        out.append(entry)
    return out
