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
                     0 MB of disk, always current, dies with the network
"""

import os
import io
import time
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
FETCH_TIMEOUT = 300


def fetch_cutout_bytes(row, ra, dec, size_deg=0.2, retries=2):
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
            resp = requests.get(IRSA_CUTOUT, params=params, timeout=FETCH_TIMEOUT)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "fits" in ctype:
                meta["bytes"] = len(resp.content)
                meta["fetched_at"] = time.time()
                return resp.content, meta
            last = f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as e:                      # timeouts, resets
            last = f"{type(e).__name__}: {e}"
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


def live_cutouts(ra, dec, radius_deg=0.1, collection="spherex_qr2",
                 size_deg=0.2, bands=None, epoch_mode="all", per_slot=1,
                 max_slots=None, workers=DEFAULT_WORKERS, log=print):
    """
    Fetch a target's cutouts straight from IRSA into memory.

    Returns a list of dicts: {"data": <FITS bytes>, "epoch": int, "band": str,
    "obs_id": str, "url": str, "mjd": float, "shape": [ny, nx], ...}
    Slots are fetched concurrently and validated in memory.
    """
    bands = tuple(bands or DEFAULT_BANDS)

    rows = query_sia2(ra, dec, radius_deg=radius_deg, collection=collection)
    slots, n_epochs = select_exposures(rows, ra, dec, bands=bands,
                                       epoch_mode=epoch_mode, per_slot=per_slot)
    if max_slots:
        slots = slots[:max_slots]
    log(f"live: {len(rows)} SIA2 rows, {n_epochs} epoch(s), {len(slots)} slot(s) queued")

    def work(item):
        epoch, candidates = item
        ordered = sorted(candidates,
                         key=lambda r: _separation_deg(ra, dec,
                                                       float(r.get("s_ra", ra)),
                                                       float(r.get("s_dec", dec))))
        for row in ordered:
            data, meta = fetch_cutout_bytes(row, ra, dec, size_deg=size_deg)
            if data is None:
                log(f"live: epoch {epoch} {meta.get('band')} failed: {meta.get('error')}")
                continue
            ok, reason, info = _check_bytes(data, ra, dec)
            if not ok:
                log(f"live: epoch {epoch} {meta.get('band')} rejected: {reason}")
                continue
            out = dict(meta)
            out.update({k: v for k, v in info.items() if v is not None})
            out["epoch"] = epoch
            out["data"] = data
            out["bytes"] = len(data)
            log(f"live: epoch {epoch} {out.get('band')} ok "
                f"({out['bytes'] / 1e6:.1f} MB, {out.get('shape')})")
            return out
        return None

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, slots))

    accepted = [r for r in results if r]
    accepted.sort(key=lambda r: (r["epoch"], str(r.get("band"))))
    log(f"live: {len(accepted)}/{len(slots)} slots fetched")
    return accepted


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

