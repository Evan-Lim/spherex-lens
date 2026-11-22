"""
spherexlens.eval.injection  (Phase 4.2)
Strict injection-recovery.

What changed relative to the original step6 test:

* PSF     -- the real SPHEREx EPSF of the field when available (else a
             Gaussian, recorded in the result as psf_model="gaussian").
* where   -- random positions on pixels that have data in both epochs, no
             mask-matching losses, away from the edges and from real
             detections; several sources per image, >= EVAL_INJECT_MIN_SEP_PX
             apart so they can never be paired with each other.
* what    -- movers (uniform random direction, speed grid), brightening
             variables (positive change) and fading variables (negative).
* success -- a detection within EVAL_MATCH_TOLERANCE_PX (1.5 px) of the
             injected position AND of the right kind:
                 mover        -> "dipole" whose POSITIVE lobe is at the new position
                 brightening  -> "monopole" at the position
                 fading       -> "monopole_negative" at the position
             (the old test accepted any |S| > 5 in a 10x10 box).
* SNR     -- by default the PSF-matched single-epoch SNR of the injected
             flux:  flux = snr * sigma_pix / sqrt(sum P^2).  ``snr_mode="peak"``
             keeps the legacy definition (peak pixel = snr * sigma_pix) used by
             the dashboard's recovery panel.
* report  -- recall per (snr, mu, category) with counts and Wilson 95% CIs,
             per category and overall.
"""
import math

import numpy as np
from scipy.ndimage import binary_erosion, shift as nd_shift

import config
from spherexlens.eval import harness

CATEGORIES = ("mover", "brightening", "fading")
EXPECTED_KIND = {"mover": "dipole", "brightening": "monopole",
                 "fading": "monopole_negative"}


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------
def wilson(k, n, z=1.96):
    """Wilson score interval for k successes in n trials -> [lo, hi]."""
    if n <= 0:
        return [0.0, 1.0]
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------
def add_stamp(img, stamp, x, y, flux):
    """Add flux * stamp centred at sub-pixel (x, y) (stamp has unit sum)."""
    if flux == 0:
        return img
    h = stamp.shape[0] // 2
    ix, iy = int(round(x)), int(round(y))
    s = nd_shift(stamp, (y - iy, x - ix), order=1, mode="constant", cval=0.0)
    ny, nx = img.shape
    y0, y1, x0, x1 = iy - h, iy + h + 1, ix - h, ix + h + 1
    sy0, sx0 = max(0, -y0), max(0, -x0)
    sy1 = s.shape[0] - max(0, y1 - ny)
    sx1 = s.shape[1] - max(0, x1 - nx)
    if sy1 <= sy0 or sx1 <= sx0:
        return img
    img[max(0, y0):min(ny, y1), max(0, x0):min(nx, x1)] += flux * s[sy0:sy1, sx0:sx1]
    return img


def matched_filter_noise(img, stamp, valid):
    """Measured 1-sigma of the PSF-matched-filtered image on blank sky.

    Includes everything the per-pixel VARIANCE layer misses: noise correlated
    by resampling 6.2" pixels onto the 3" grid, and extra scatter (on the real
    DR1 co-adds the sky scatter is ~1.4x the formal noise).  Robust (MAD), so
    stars barely move it.
    """
    from scipy.signal import fftconvolve
    a = np.where(np.isfinite(img), img, 0.0)
    f = fftconvolve(a, stamp[::-1, ::-1], mode="same")
    v = f[valid] if valid is not None and valid.any() else f.ravel()
    v = v[np.isfinite(v)]
    return 1.4826 * float(np.median(np.abs(v - np.median(v))))


def flux_for_snr(snr, sigma_pix, stamp, mode="psf"):
    if mode == "peak":
        return snr * sigma_pix / float(stamp.max())
    return snr * sigma_pix / float(np.sqrt((stamp ** 2).sum()))


def pick_positions(valid, n, rng, min_sep, avoid=(), max_tries=2000):
    """Up to n random (x, y) on `valid` pixels, >= min_sep apart and from `avoid`."""
    ys, xs = np.nonzero(valid)
    if len(ys) == 0:
        return []
    chosen = []
    others = [(float(x), float(y)) for (y, x) in avoid]
    for _ in range(max_tries):
        if len(chosen) >= n:
            break
        i = rng.integers(len(ys))
        x, y = xs[i] + rng.uniform(-0.5, 0.5), ys[i] + rng.uniform(-0.5, 0.5)
        if any(math.hypot(x - a, y - b) < min_sep for a, b in chosen):
            continue
        if any(math.hypot(x - a, y - b) < 6.0 for a, b in others):
            continue
        chosen.append((x, y))
    return chosen


def match_detection(detections, x, y, kind, tol=None):
    """
    The strongest detection of `kind` whose main lobe is within tol px of (x, y).
    For a dipole the main lobe is the positive one (pos_plus / position).
    """
    tol = config.EVAL_MATCH_TOLERANCE_PX if tol is None else tol
    best = None
    for d in detections:
        if d.get("kind") != kind:
            continue
        p = harness.detection_position(d)
        if p is None:
            continue
        if math.hypot(p[1] - x, p[0] - y) <= tol:
            if best is None or abs(d.get("snr", 0)) > abs(best.get("snr", 0)):
                best = d
    return best


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------
def make_sources(category, snr, mu, positions, rng, dt_years, pixscale):
    out = []
    for (x, y) in positions:
        src = {"category": category, "snr": snr, "mu": mu, "x": x, "y": y}
        if category == "mover":
            ang = rng.uniform(0, 2 * np.pi)
            step = mu * dt_years / pixscale
            src.update(x2=x + step * np.cos(ang), y2=y + step * np.sin(ang),
                       angle_deg=float(np.degrees(ang)), shift_px=float(step))
        else:
            src.update(x2=x, y2=y, base=float(rng.uniform(0.0, 2.0)))
        out.append(src)
    return out


def inject_pair(img1, img2, sources, stamp, sigma1, sigma2, mode="psf", stamp2=None):
    """Copies of img1/img2 with the sources added (stamp2: epoch-2 PSF, default stamp)."""
    stamp2 = stamp if stamp2 is None else stamp2
    a, b = img1.copy(), img2.copy()
    for s in sources:
        s1 = sigma1[int(round(s["y"])), int(round(s["x"]))] if np.ndim(sigma1) else sigma1
        s1 = float(s1) if np.isfinite(s1) else float(np.nanmedian(sigma1))
        f = flux_for_snr(s["snr"], s1, stamp, mode)
        if s["category"] == "mover":
            add_stamp(a, stamp, s["x"], s["y"], f)
            add_stamp(b, stamp2, s["x2"], s["y2"], f)
        elif s["category"] == "brightening":
            add_stamp(a, stamp, s["x"], s["y"], s["base"] * f)
            add_stamp(b, stamp2, s["x"], s["y"], (s["base"] + 1.0) * f)
        else:
            add_stamp(a, stamp, s["x"], s["y"], (s["base"] + 1.0) * f)
            add_stamp(b, stamp2, s["x"], s["y"], s["base"] * f)
    return a, b


def run_strict_injection(img1, img2, noise1, noise2, psf=None, snr_grid=None,
                         mu_grid=None, categories=CATEGORIES, n_trials=None,
                         seed=None, dt_years=0.5, pixscale=None, valid=None,
                         avoid=(), sigma_thresh=None, per_image=None,
                         snr_mode=None, margin=None, min_sep=None,
                         zogy_fn=None, find_fn=None, progress=None,
                         on_recovered=None, on_run=None, normalisation=None):
    """
    Strict injection-recovery on a pair of co-adds.

    noise1/noise2 -- per-pixel 1-sigma maps (or scalars) of img1/img2.
    psf          -- harness.field_psf() dict (stamp + sigma) or None (Gaussian).
    on_recovered -- callback(detection, ctx_like, source) for each recovered
                    injection (the ML label builder uses it).
    on_run       -- callback(detections, D, S, sources) after every image pair.

    Returns {"grid": [...], "by_category": {...}, "overall": {...}, ...}.
    """
    snr_grid = list(snr_grid or config.EVAL_INJECT_SNR_GRID)
    mu_grid = list(mu_grid or config.EVAL_INJECT_MU_GRID)
    n_trials = int(n_trials or config.EVAL_INJECT_TRIALS)
    per_image = int(per_image or config.EVAL_INJECT_PER_IMAGE)
    pixscale = pixscale or config.PIXSCALE_ARCSEC
    sigma_thresh = sigma_thresh or config.DETECTION_SIGMA
    min_sep = min_sep or config.EVAL_INJECT_MIN_SEP_PX
    rng = np.random.default_rng(config.ML_RANDOM_SEED if seed is None else seed)
    if psf is None:
        psf = {"stamp": harness.gaussian_stamp(config.PSF_SIGMA_PX_FALLBACK),
               "sigma_px": config.PSF_SIGMA_PX_FALLBACK, "model": "gaussian"}
    stamp = psf["stamp"]
    per_epoch = list((psf.get("stamps") or {}).values())
    stamp1, stamp2 = (per_epoch[0], per_epoch[-1]) if len(per_epoch) >= 2 else (stamp, stamp)
    ny, nx = img1.shape
    if margin is None:
        margin = max(config.EVAL_EDGE_MARGIN_PX, stamp.shape[0])
    margin = min(margin, max(1, min(ny, nx) // 4))
    n1 = noise1 if np.ndim(noise1) else np.full(img1.shape, float(noise1))
    n2 = noise2 if np.ndim(noise2) else np.full(img2.shape, float(noise2))
    if valid is None:
        valid = harness.valid_mask([n1, n2])
    valid = binary_erosion(valid, iterations=3) if valid.any() else valid
    valid = valid.copy()
    valid[:margin] = valid[-margin:] = False
    valid[:, :margin] = valid[:, -margin:] = False
    s1, s2 = harness.scalar_noise(n1), harness.scalar_noise(n2)
    snr_mode = snr_mode or getattr(config, "EVAL_SNR_MODE", "psf")
    inj_n1, inj_n2, inj_mode = n1, n2, snr_mode
    if snr_mode == "empirical":
        # SNR = what a matched filter on THIS image measures: flux * sum(P^2)
        # / sigma_mf.  Expressed through flux_for_snr's "psf" formula
        # (flux = snr * sigma / sqrt(sum P^2)) with sigma = sigma_mf / sqrt(sum P^2).
        k = float(np.sqrt((stamp ** 2).sum()))
        inj_n1 = matched_filter_noise(img1, stamp1, valid) / k
        inj_n2 = matched_filter_noise(img2, stamp2, valid) / k
        inj_mode = "psf"

    cells = []
    for cat in categories:
        for snr in snr_grid:
            for mu in (mu_grid if cat == "mover" else [0]):
                cells.append((cat, snr, mu))

    grid = []
    for ci, (cat, snr, mu) in enumerate(cells):
        rec = n = 0
        while n < n_trials:
            k = min(per_image, n_trials - n)
            pos = pick_positions(valid, k, rng, min_sep, avoid)
            if not pos:
                break
            srcs = make_sources(cat, snr, mu, pos, rng, dt_years, pixscale)
            a, b = inject_pair(img1, img2, srcs, stamp1, inj_n1, inj_n2, inj_mode, stamp2)
            try:
                D, S = harness.subtract(a, b, s1, s2, psf, zogy_fn=zogy_fn,
                                        normalisation=normalisation)
                dets = (find_fn or harness.detect)(S, dt_years, sigma_thresh, pixscale)
            except Exception:
                n += len(srcs)
                continue
            if on_run is not None:
                on_run(dets, D, S, srcs)
            for s in srcs:
                m = match_detection(dets, s["x2"], s["y2"], EXPECTED_KIND[cat])
                s["recovered"] = m is not None
                if m is not None:
                    rec += 1
                    if on_recovered is not None:
                        on_recovered(m, {"D": D, "S": S, "stacks_injected": (a, b)}, s)
            n += len(srcs)
        cell = {"snr": snr, "mu": mu, "category": cat, "n": n, "n_recovered": rec,
                "recall": round(rec / n, 4) if n else None, "ci95": wilson(rec, n)}
        grid.append(cell)
        if progress:
            progress({"completed": ci + 1, "total": len(cells), "snr": float(snr),
                      "mu": float(mu), "category": cat, "recovered": rec, "trials": n})
    out = summarise(grid, psf, snr_mode, dt_years, sigma_thresh)
    out["s_normalisation"] = normalisation or config.EVAL_S_NORMALISATION
    return out


def summarise(grid, psf=None, snr_mode="psf", dt_years=None, sigma_thresh=None):
    def agg(cells):
        k = sum(c["n_recovered"] for c in cells)
        n = sum(c["n"] for c in cells)
        return {"recall": round(k / n, 4) if n else None, "n": n,
                "n_recovered": k, "ci95": wilson(k, n)}
    by_cat = {c: agg([g for g in grid if g["category"] == c])
              for c in sorted({g["category"] for g in grid})}
    overall = agg(grid)
    return {
        "recall_overall": overall["recall"], "overall": overall,
        "by_category": by_cat, "grid": grid,
        "psf_model": (psf or {}).get("model", "gaussian"),
        "psf_fwhm_px": round(float((psf or {}).get("fwhm_px", 2.3548 * config.PSF_SIGMA_PX_FALLBACK)), 3),
        "snr_definition": ("PSF-matched single-epoch SNR of the injected flux change"
                           if snr_mode == "psf" else
                           "matched-filter SNR against the measured image noise"
                           if snr_mode == "empirical" else "peak pixel / pixel noise"),
        "match": f"kind-correct detection within {config.EVAL_MATCH_TOLERANCE_PX} px",
        "dt_years": dt_years, "sigma_thresh": sigma_thresh,
    }


def merge(results):
    """Pool several fields' injection results (cell counts add up)."""
    cells = {}
    psf_models = set()
    for r in results:
        if not r:
            continue
        psf_models.add(r.get("psf_model"))
        for g in r["grid"]:
            key = (g["category"], g["snr"], g["mu"])
            c = cells.setdefault(key, {"snr": g["snr"], "mu": g["mu"],
                                       "category": g["category"], "n": 0, "n_recovered": 0})
            c["n"] += g["n"]
            c["n_recovered"] += g["n_recovered"]
    grid = []
    for c in cells.values():
        c["recall"] = round(c["n_recovered"] / c["n"], 4) if c["n"] else None
        c["ci95"] = wilson(c["n_recovered"], c["n"])
        grid.append(c)
    grid.sort(key=lambda c: (c["category"], c["snr"], c["mu"]))
    out = summarise(grid)
    out["psf_model"] = "+".join(sorted(m for m in psf_models if m)) or "gaussian"
    out["psf_fwhm_px"] = None
    out["n_fields"] = sum(1 for r in results if r)
    return out
