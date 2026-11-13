"""
Choose deep-field exposures that cover a position, spread evenly over time and
over the six detectors, and fetch them as cutouts.

A full deep stack at the North Ecliptic Pole is ~20,000 images (~80 GB as
cutouts); a light curve needs far fewer.  ``select_rows`` takes a stratified
sample: the time span is cut into equal bins and each bin contributes its
exposures round-robin across detectors, so a 200-exposure sample still spans
the whole year and every wavelength range.
"""
import os
from collections import defaultdict

import numpy as np

import step1_ingest as ingest

DEEP_COLLECTION = "spherex_dr1_deep"


def query_rows(ra, dec, radius_deg=0.05, collection=DEEP_COLLECTION):
    """SIA2 rows whose footprint contains (ra, dec), safely inside the array."""
    rows = ingest.query_sia2(ra, dec, radius_deg=radius_deg, collection=collection)
    keep = []
    for r in rows:
        poly = ingest._parse_s_region(r.get("s_region", ""))
        if not poly:
            keep.append(r)
            continue
        try:
            inside = ingest.interior_distance_deg(ra, dec, poly)
        except Exception:
            inside = None
        # >= ~3.6' inside the array, so a 0.1 deg cutout is never clipped
        if inside is None or inside > 0.06:
            keep.append(r)
    return keep


def _mjd(row):
    for key in ("t_min", "time_bounds_lower", "mjd"):
        try:
            return float(row.get(key))
        except (TypeError, ValueError):
            continue
    return None


def select_rows(rows, max_exposures=200, n_time_bins=None, seed=0):
    """Stratified sample over time bins x detector bands (deterministic)."""
    rows = [r for r in rows if _mjd(r) is not None]
    if len(rows) <= max_exposures:
        return sorted(rows, key=_mjd)
    t = np.array([_mjd(r) for r in rows])
    n_bins = n_time_bins or max(4, max_exposures // 6)
    edges = np.linspace(t.min(), t.max() + 1e-6, n_bins + 1)
    rng = np.random.default_rng(seed)
    per_bin = defaultdict(lambda: defaultdict(list))
    for r, tt in zip(rows, t):
        b = int(np.searchsorted(edges, tt, side="right") - 1)
        per_bin[b][r.get("energy_bandpassname") or r.get("band") or "?"].append(r)
    quota = max_exposures / n_bins
    chosen, carry = [], 0.0
    for b in range(n_bins):
        carry += quota
        take = int(carry)
        carry -= take
        bands = per_bin.get(b, {})
        for lst in bands.values():
            rng.shuffle(lst)
        names = sorted(bands)
        i = 0
        while take > 0 and any(bands[n] for n in names):
            name = names[i % len(names)]
            if bands[name]:
                chosen.append(bands[name].pop())
                take -= 1
            i += 1
    return sorted(chosen, key=_mjd)[:max_exposures]


def fetch(rows, ra, dec, size_deg=0.1, workers=3):
    """Download cutouts (cached on disk by step1_ingest); returns [(path, row)]."""
    from concurrent.futures import ThreadPoolExecutor

    def one(row):
        try:
            path = ingest.download_cutout(row, ra, dec, size_deg=size_deg)
        except Exception as e:                  # one bad cutout never stops the run
            print(f"    cutout failed: {type(e).__name__}: {e}")
            path = None
        return (path, row) if path and os.path.exists(path) else None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = [x for x in pool.map(one, rows) if x]
    return out
