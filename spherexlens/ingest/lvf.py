"""
Wavelength seen by a SPHEREx exposure at a sky position, from the SIA2 row alone.

SPHEREx images through linear variable filters (LVFs): the wavelength changes across each
detector, so where a target lands on the detector decides which wavelength that exposure
measures.  Two epochs that put a star on different detector positions compare different
wavelengths, and a star's spectrum then looks like a brightness change.

Each detector's wavelength map is a fixed calibration (the WCS-WAVE table in every Level-2
file, identical across files for one detector).  The tables are stored in
lvf_tables.json.  The target's detector position comes from the SIA2 footprint polygon
(``s_region``, whose four corners are the detector corners in pixel order), so no image
has to be downloaded.  Checked on 225 Baade's Window exposures against the WCS-WAVE value
at the target: median error 0.27 %, 95 % within 0.61 %, worst 0.75 % in ln(lambda).
"""
import json
import os
import re

import numpy as np

_TABLES = None
NPIX_DET = 2040


def _tables():
    global _TABLES
    if _TABLES is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lvf_tables.json")
        with open(path) as f:
            raw = json.load(f)
        _TABLES = {int(k): (np.asarray(v[0], float), np.asarray(v[1], float),
                            np.asarray(v[2], float)[..., 0]) for k, v in raw.items()}
    return _TABLES


def detector_of(row):
    """Detector number 1-6 from 'SPHEREx-D3' style bandpass names, else None."""
    m = re.search(r"D(\d)\s*$", str(row.get("energy_bandpassname") or ""))
    return int(m.group(1)) if m else None


def _polygon(s_region):
    nums = [float(t) for t in re.findall(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", s_region or "")]
    if len(nums) < 8:
        return None
    return np.array(nums[:8]).reshape(4, 2)


def detector_position(row, ra, dec):
    """(u, v) in [0, 1]^2: where (ra, dec) falls on the detector, by inverse bilinear
    interpolation of the footprint corners in a tangent plane.  None if unusable."""
    P = _polygon(row.get("s_region"))
    if P is None:
        return None
    c = P.mean(0)
    cd = np.cos(np.radians(c[1]))
    Q = np.c_[((P[:, 0] - c[0] + 180.0) % 360.0 - 180.0) * cd, P[:, 1] - c[1]]
    q = np.array([((ra - c[0] + 180.0) % 360.0 - 180.0) * cd, dec - c[1]])
    u = v = 0.5
    for _ in range(30):
        f = (1 - u) * (1 - v) * Q[0] + u * (1 - v) * Q[1] + u * v * Q[2] + (1 - u) * v * Q[3] - q
        du = -(1 - v) * Q[0] + (1 - v) * Q[1] + v * Q[2] - v * Q[3]
        dv = -(1 - u) * Q[0] - u * Q[1] + u * Q[2] + (1 - u) * Q[3]
        try:
            s = np.linalg.solve(np.c_[du, dv], f)
        except np.linalg.LinAlgError:
            return None
        u, v = u - s[0], v - s[1]
        if abs(s[0]) + abs(s[1]) < 1e-10:
            break
    if not (np.isfinite(u) and np.isfinite(v)):
        return None
    return float(u), float(v)


def wavelength_um(row, ra, dec):
    """Central wavelength (micron) this exposure sees at (ra, dec), or None."""
    det = detector_of(row)
    if det not in _tables():
        return None
    uv = detector_position(row, ra, dec)
    if uv is None:
        return None
    xs, ys, lam = _tables()[det]
    X = 1.0 + np.clip(uv[0], 0.0, 1.0) * (NPIX_DET - 1)
    Y = 1.0 + np.clip(uv[1], 0.0, 1.0) * (NPIX_DET - 1)
    # bilinear interpolation on the (Y, X) grid
    i = int(np.clip(np.searchsorted(ys, Y) - 1, 0, len(ys) - 2))
    j = int(np.clip(np.searchsorted(xs, X) - 1, 0, len(xs) - 2))
    ty = (Y - ys[i]) / (ys[i + 1] - ys[i])
    tx = (X - xs[j]) / (xs[j + 1] - xs[j])
    val = ((1 - ty) * (1 - tx) * lam[i, j] + (1 - ty) * tx * lam[i, j + 1]
           + ty * (1 - tx) * lam[i + 1, j] + ty * tx * lam[i + 1, j + 1])
    return float(val) if np.isfinite(val) and val > 0 else None


def matched_choice(pools, ra, dec, max_dlnlam):
    """
    Wavelength-matched ordering of candidate exposures for ONE detector.

    pools: [rows of epoch 1, rows of epoch 2, ...] (each already filtered / ranked by
    depth inside the footprint).  Chooses the reference wavelength that minimises the
    worst epoch's mismatch and returns (reordered pools, worst |d ln lambda|).  Within each
    epoch the rows are sorted by |d ln lambda| to that reference (closest first), so the
    caller's first usable candidate is the best match.  Rows whose wavelength cannot be
    estimated keep their order after the estimated ones.  Returns (pools, None) when fewer
    than two epochs have estimates.
    """
    lam = [[wavelength_um(r, ra, dec) for r in pool] for pool in pools]
    have = [[np.log(x) for x in L if x] for L in lam]
    if sum(1 for h in have if h) < 2:
        return pools, None
    best_ref, best_worst = None, np.inf
    for ref in sorted({x for h in have for x in h}):
        worst = max(min(abs(x - ref) for x in h) for h in have if h)
        if worst < best_worst:
            best_ref, best_worst = ref, worst
    out = []
    for pool, L in zip(pools, lam):
        keyed = [(abs(np.log(x) - best_ref) if x else np.inf, k) for k, x in enumerate(L)]
        order = [k for _, k in sorted(keyed)]
        good = [pool[k] for k in order if keyed[k][0] <= max_dlnlam]
        rest = [pool[k] for k in order if keyed[k][0] > max_dlnlam]
        out.append(good + rest)
    return out, float(best_worst)
