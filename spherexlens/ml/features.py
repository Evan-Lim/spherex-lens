"""
spherexlens.ml.features  (Phase 5.1)
One fixed, documented feature vector per detection.

``extract_features(detection, ctx) -> {name: float}`` works on both flavours
of detection dict -- a raw step3 detection (pos / pos_plus / pos_minus) or a
triaged dashboard detection (position / position_minus / epochs / asteroid) --
and on both contexts: the dashboard's ``enrich_ctx`` and the eval harness's
``make_ctx``.  Every missing input gives NaN (HistGradientBoosting handles
NaN natively); nothing here raises for a missing ctx key.

FEATURE_NAMES -- the order is part of the model contract; append only.

  snr                    |S| at the main lobe (step3 "snr")
  snr_minus              |S| at the negative lobe of a dipole (NaN otherwise)
  fwhm_ratio             second-moment FWHM of the lobe in D / PSF FWHM
  ellipticity            1 - b/a of the lobe's second moments in D
  psf_chi2               reduced chi^2 of a PSF fit to the lobe(s) in D
  dipole_symmetry        |S+ + S-| / (S+ - S-)  (0 = perfectly balanced dipole)
  dipole_sep_px          lobe separation of a dipole (px on the 3" grid)
  kind_dipole            one-hot of the detection kind; an unknown future kind
  kind_monopole            leaves all three at 0
  kind_monopole_negative
  bright_star_dist_arcsec distance to the nearest Gaia star with G <= 16
  bright_star_gmag       G of that star
  masked_frac            fraction of pixels within 3 px masked / dropped by the
                         matched-mask co-add / without data
  noise_ratio            local (r = 5 px) noise / field median noise
  exposure_quality       mean score of kept exposures (ctx["exposure_quality"])
  n_pairs_seen           epoch pairs the detection was found in
  colour_cross_detector  cross-detector colour if the pipeline supplies it
  spec_ch4, spec_h2o, spec_nir_slope   step5 spectral indices if supplied
  abs_ecl_lat_deg        |ecliptic latitude| (asteroids crowd low latitudes)
  asteroid_match         1 = known asteroid there, 0 = checked, none; NaN = not checked
"""
import math

import numpy as np

import config

FEATURE_NAMES = (
    "snr", "snr_minus", "fwhm_ratio", "ellipticity", "psf_chi2",
    "dipole_symmetry", "dipole_sep_px",
    "kind_dipole", "kind_monopole", "kind_monopole_negative",
    "bright_star_dist_arcsec", "bright_star_gmag",
    "masked_frac", "noise_ratio", "exposure_quality", "n_pairs_seen",
    "colour_cross_detector", "spec_ch4", "spec_h2o", "spec_nir_slope",
    "abs_ecl_lat_deg", "asteroid_match",
)

# human-readable labels for the "reasons" shown next to an ML score:
# feature -> (text when it pushes towards real, text when towards bogus)
FEATURE_LABELS = {
    "snr": ("strong signal", "weak signal"),
    "snr_minus": ("clear negative lobe", "weak negative lobe"),
    "fwhm_ratio": ("point-like (PSF-sized)", "wrong size for a point source"),
    "ellipticity": ("round profile", "elongated profile"),
    "psf_chi2": ("good PSF fit", "poor PSF fit"),
    "dipole_symmetry": ("balanced dipole", "unbalanced dipole"),
    "dipole_sep_px": ("plausible lobe separation", "implausible lobe separation"),
    "kind_dipole": ("dipole (mover-like)", "dipole shape"),
    "kind_monopole": ("brightening point source", "monopole shape"),
    "kind_monopole_negative": ("fading point source", "negative monopole shape"),
    "bright_star_dist_arcsec": ("far from bright stars", "near a bright star"),
    "bright_star_gmag": ("nearby star is faint", "nearby star is bright"),
    "masked_frac": ("clean pixels", "masked / missing pixels nearby"),
    "noise_ratio": ("normal local noise", "noisy region"),
    "exposure_quality": ("good exposures", "poor exposures"),
    "n_pairs_seen": ("seen in several epoch pairs", "seen in few epoch pairs"),
    "colour_cross_detector": ("consistent colour", "odd colour"),
    "spec_ch4": ("spectral index (CH4)", "spectral index (CH4)"),
    "spec_h2o": ("spectral index (H2O)", "spectral index (H2O)"),
    "spec_nir_slope": ("spectral slope", "spectral slope"),
    "abs_ecl_lat_deg": ("ecliptic latitude", "ecliptic latitude"),
    "asteroid_match": ("matches a known asteroid", "no known asteroid"),
}

NAN = float("nan")
_OBLIQUITY = math.radians(23.4392911)


def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return NAN
    return v if math.isfinite(v) else NAN


def _yx(p):
    if p is None:
        return None
    try:
        return float(p[0]), float(p[1])
    except Exception:
        return None


def _positions(d):
    plus = _yx(d.get("position") or d.get("pos_plus") or d.get("pos"))
    minus = _yx(d.get("position_minus") or d.get("pos_minus"))
    if plus is None and minus is not None:
        plus, minus = minus, None
    return plus, minus


def _cache(ctx):
    c = ctx.get("_ml_cache") if isinstance(ctx, dict) else None
    if c is None and isinstance(ctx, dict):
        c = ctx["_ml_cache"] = {}
    return c if c is not None else {}


def psf_info(ctx):
    """(sigma_px, fwhm_px) of the PSF the pipeline used."""
    psf = ctx.get("psf")
    if isinstance(psf, dict) and psf.get("sigma_px"):
        return float(psf["sigma_px"]), float(psf.get("fwhm_px") or 2.3548 * psf["sigma_px"])
    psfs = ctx.get("psfs")
    try:
        arrays = list(psfs.values()) if isinstance(psfs, dict) else list(psfs or [])
        arrays = [np.asarray(a, dtype=float) for a in arrays
                  if np.ndim(a) == 2 and np.asarray(a).shape[0] == np.asarray(a).shape[1]]
        if arrays:
            from spherexlens.eval.harness import psf_moments
            sig = float(np.mean([psf_moments(a)[0] for a in arrays]))
            return sig, 2.3548 * sig
    except Exception:
        pass
    s = config.PSF_SIGMA_PX_FALLBACK
    return s, 2.3548 * s


def pair_maps(d, ctx):
    """(D, S) of the epoch pair the detection came from."""
    D, S = ctx.get("D"), ctx.get("S")
    ep = d.get("epochs")
    order = ctx.get("epoch_order") or []
    stacks = ctx.get("stacks") or {}
    if ep is None or len(order) < 2 or not stacks:
        return D, S
    ea, eb = ep[0], ep[-1]
    if (ea, eb) == (order[0], order[-1]) or ea not in stacks or eb not in stacks:
        return D, S
    cache = _cache(ctx).setdefault("pairs", {})
    if (ea, eb) not in cache:
        try:
            from spherexlens.eval import harness
            noises = ctx.get("noises") or {}
            cache[(ea, eb)] = harness.subtract(stacks[ea], stacks[eb], noises[ea],
                                               noises[eb], ctx.get("psf"))
        except Exception:
            cache[(ea, eb)] = (D, S)
    return cache[(ea, eb)]


def _robust_sigma(D, ctx):
    cache = _cache(ctx).setdefault("sigma_D", {})
    k = id(D)
    if k not in cache:
        v = np.asarray(D, dtype=float)
        v = v[np.isfinite(v) & (v != 0)]
        if v.size < 10:
            cache[k] = NAN
        else:
            med = np.median(v)
            cache[k] = float(1.4826 * np.median(np.abs(v - med))) or NAN
    return cache[k]


def _window(arr, y, x, r):
    ny, nx = arr.shape
    yi, xi = int(round(y)), int(round(x))
    y0, y1, x0, x1 = yi - r, yi + r + 1, xi - r, xi + r + 1
    if y0 < 0 or x0 < 0 or y1 > ny or x1 > nx:
        return None, None, None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    return arr[y0:y1, x0:x1], yy - y, xx - x


def lobe_shape(D, y, x, sign, r=4):
    """(fwhm_px, ellipticity) from the second moments of sign*D near (y, x)."""
    if D is None:
        return NAN, NAN
    w, dy, dx = _window(np.asarray(D, dtype=float), y, x, r)
    if w is None:
        return NAN, NAN
    w = np.clip(np.nan_to_num(sign * w), 0, None)
    tot = w.sum()
    if tot <= 0:
        return NAN, NAN
    cy, cx = (w * dy).sum() / tot, (w * dx).sum() / tot
    iyy = (w * (dy - cy) ** 2).sum() / tot
    ixx = (w * (dx - cx) ** 2).sum() / tot
    ixy = (w * (dx - cx) * (dy - cy)).sum() / tot
    tr, det = ixx + iyy, ixx * iyy - ixy * ixy
    disc = math.sqrt(max(tr * tr / 4 - det, 0.0))
    l1, l2 = tr / 2 + disc, max(tr / 2 - disc, 1e-9)
    fwhm = 2.3548 * math.sqrt(max(tr / 2, 1e-9))
    return fwhm, 1.0 - math.sqrt(l2 / max(l1, 1e-9))


def psf_chi2(D, y, x, sigma_psf, sigma_d, r=None):
    """Reduced chi^2 of amplitude * Gaussian(PSF) fitted to D around (y, x)."""
    r = r or config.ML_FEATURE_RADIUS_PX
    if D is None or not math.isfinite(sigma_d) or sigma_d <= 0:
        return NAN
    w, dy, dx = _window(np.asarray(D, dtype=float), y, x, r)
    if w is None or not np.isfinite(w).all():
        return NAN
    g = np.exp(-(dx ** 2 + dy ** 2) / (2 * sigma_psf ** 2))
    amp = float((w * g).sum() / (g * g).sum())
    res = (w - amp * g) / sigma_d
    return float((res ** 2).sum() / max(w.size - 1, 1))


def _gaia_pixels(ctx):
    c = _cache(ctx)
    if "gaia_px" not in c:
        c["gaia_px"] = None
        cats = ctx.get("raw_catalogs") or {}
        wcs = ctx.get("wcs")
        rows = [s for s in (cats.get("gaia") or [])
                if s.get("gmag") is not None and s["gmag"] <= config.ML_BRIGHT_STAR_GMAG
                and s.get("ra") is not None]
        if rows and wcs is not None:
            try:
                x, y = wcs.world_to_pixel_values(np.array([s["ra"] for s in rows]),
                                                 np.array([s["dec"] for s in rows]))
                c["gaia_px"] = (np.asarray(x, float), np.asarray(y, float),
                                np.array([s["gmag"] for s in rows], float))
            except Exception:
                pass
    return c["gaia_px"]


def masked_fraction(ctx, y, x, ep=None, r=None):
    r = r or config.ML_FEATURE_RADIUS_PX
    exc = ctx.get("excluded")
    nmaps = ctx.get("noise_maps") or {}
    keys = [k for k in (ep or ctx.get("epoch_order") or []) if k in nmaps]
    shape = (exc.shape if exc is not None else
             (nmaps[keys[0]].shape if keys else None))
    if shape is None:
        return NAN
    ny, nx = shape
    yi, xi = int(round(y)), int(round(x))
    yy, xx = np.mgrid[yi - r:yi + r + 1, xi - r:xi + r + 1]
    inside = (yy >= 0) & (yy < ny) & (xx >= 0) & (xx < nx)
    bad = ~inside
    yc, xc = np.clip(yy, 0, ny - 1), np.clip(xx, 0, nx - 1)
    if exc is not None:
        bad |= np.asarray(exc)[yc, xc] > 0
    for k in keys:
        nm = np.asarray(nmaps[k])[yc, xc]
        bad |= ~np.isfinite(nm) | (nm >= 1e3)
    return float(bad.mean())


def noise_ratio(ctx, y, x, ep=None, r=5):
    nmaps = ctx.get("noise_maps") or {}
    noises = ctx.get("noises") or {}
    for k in (ep or ctx.get("epoch_order") or []):
        if k in nmaps and noises.get(k):
            w, _, _ = _window(np.asarray(nmaps[k], dtype=float), y, x, r)
            if w is None:
                return NAN
            w = w[np.isfinite(w) & (w < 1e3)]
            if not w.size:
                return NAN
            return float(np.median(w) / noises[k])
    return NAN


def exposure_quality(ctx):
    q = ctx.get("exposure_quality")
    if not q:
        return NAN
    try:
        rows = q.values() if isinstance(q, dict) else q
        scores = [float(r.get("score")) for r in rows
                  if isinstance(r, dict) and r.get("score") is not None and r.get("kept", True)]
        return float(np.mean(scores)) if scores else NAN
    except Exception:
        return NAN


def ecliptic_latitude_deg(ra, dec):
    a, d = math.radians(ra), math.radians(dec)
    s = math.sin(d) * math.cos(_OBLIQUITY) - math.cos(d) * math.sin(_OBLIQUITY) * math.sin(a)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def _sky(d, ctx, y, x):
    ra, dec = _f(d.get("ra")), _f(d.get("dec"))
    if math.isfinite(ra) and math.isfinite(dec):
        return ra, dec
    wcs = ctx.get("wcs")
    if wcs is not None:
        try:
            r, de = wcs.pixel_to_world_values(float(x), float(y))
            return float(r), float(de)
        except Exception:
            pass
    return _f(ctx.get("ra")), _f(ctx.get("dec"))


def _s_at(S, p):
    if S is None or p is None:
        return NAN
    y, x = int(round(p[0])), int(round(p[1]))
    if 0 <= y < S.shape[0] and 0 <= x < S.shape[1]:
        return float(S[y, x])
    return NAN


def extract_features(detection, ctx=None):
    """Feature dict in FEATURE_NAMES order (floats, NaN when unknown)."""
    d, ctx = detection or {}, ctx or {}
    f = dict.fromkeys(FEATURE_NAMES, NAN)
    kind = d.get("kind")
    plus, minus = _positions(d)
    f["kind_dipole"] = 1.0 if kind == "dipole" else 0.0
    f["kind_monopole"] = 1.0 if kind == "monopole" else 0.0
    f["kind_monopole_negative"] = 1.0 if kind == "monopole_negative" else 0.0
    f["snr"] = abs(_f(d.get("snr")))
    f["n_pairs_seen"] = _f(d.get("n_pairs_seen"))
    f["exposure_quality"] = exposure_quality(ctx)
    f["colour_cross_detector"] = _f(d.get("colour_cross_detector"))
    spec = d.get("spectral_indices") or {}
    if isinstance(spec, dict):
        f["spec_ch4"], f["spec_h2o"], f["spec_nir_slope"] = (
            _f(spec.get("ch4")), _f(spec.get("h2o")), _f(spec.get("nir_slope")))
    ast = d.get("asteroid")
    if isinstance(ast, dict):
        f["asteroid_match"] = {"match": 1.0, "no_match": 0.0}.get(ast.get("status"), NAN)
    if plus is None:
        return f
    y, x = plus
    try:
        D, S = pair_maps(d, ctx)
    except Exception:
        D, S = ctx.get("D"), ctx.get("S")
    D = None if D is None else np.asarray(D)
    S = None if S is None else np.asarray(S)
    sign = -1.0 if kind == "monopole_negative" else 1.0
    sig_psf, fwhm_psf = psf_info(ctx)
    fwhm, ell = lobe_shape(D, y, x, sign)
    f["fwhm_ratio"] = fwhm / fwhm_psf if math.isfinite(fwhm) else NAN
    f["ellipticity"] = ell
    sd = _robust_sigma(D, ctx) if D is not None else NAN
    chis = [psf_chi2(D, y, x, sig_psf, sd)]
    if kind == "dipole" and minus is not None:
        chis.append(psf_chi2(D, minus[0], minus[1], sig_psf, sd))
        sp, sm = _s_at(S, plus), _s_at(S, minus)
        f["snr_minus"] = abs(sm)
        fc = _f(d.get("flux_consistency"))
        if not math.isfinite(fc) and math.isfinite(sp) and math.isfinite(sm):
            fc = abs(sp + sm) / (sp - sm + 1e-9)
        f["dipole_symmetry"] = fc
        f["dipole_sep_px"] = math.hypot(plus[0] - minus[0], plus[1] - minus[1])
    chis = [c for c in chis if math.isfinite(c)]
    f["psf_chi2"] = float(np.mean(chis)) if chis else NAN
    if not math.isfinite(f["snr"]):
        f["snr"] = abs(_s_at(S, plus))
    gp = _gaia_pixels(ctx)
    if gp is not None:
        gx, gy, gm = gp
        dist = np.hypot(gx - x, gy - y)
        i = int(np.argmin(dist))
        f["bright_star_dist_arcsec"] = float(dist[i] * (ctx.get("pixscale_arcsec") or
                                                        config.PIXSCALE_ARCSEC))
        f["bright_star_gmag"] = float(gm[i])
    ep = d.get("epochs")
    ep = [ep[0], ep[-1]] if ep is not None and len(ep) else None
    f["masked_frac"] = masked_fraction(ctx, y, x, ep)
    f["noise_ratio"] = noise_ratio(ctx, y, x, ep)
    ra, dec = _sky(d, ctx, y, x)
    if math.isfinite(ra) and math.isfinite(dec):
        f["abs_ecl_lat_deg"] = abs(ecliptic_latitude_deg(ra, dec))
    return f


def to_vector(features):
    return np.array([_f(features.get(n)) for n in FEATURE_NAMES], dtype=float)


def to_json(features):
    """NaN -> None so the dict survives strict JSON encoders."""
    out = {}
    for n in FEATURE_NAMES:
        v = _f(features.get(n))
        out[n] = round(v, 5) if math.isfinite(v) else None
    return out
