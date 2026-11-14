"""
Spectrum-normalised light curves for SPHEREx deep fields.

For each source we have (t_i, lambda_i, f_i, sigma_i): every visit measures the
star at whatever wavelength the linear variable filter puts on it.  Model:

    f_i = S(lambda_i) * r(t_i)

S is the star's mean spectrum, r the variability (1 for a constant star).
S is estimated non-parametrically -- a weighted median in wavelength bins,
interpolated in log-lambda -- and r_i = f_i / S(lambda_i).  A constant star
gives r ~ 1 with scatter set by the errors; a variable star gives structured
r(t), which we test with a chi-square against r = 1 and search for periods
with a Lomb-Scargle periodogram (astropy).

Iterating once (refit S on f / r_smoothed) guards against a variable star's
variability leaking into its spectrum when wavelength and time are correlated
(the LVF scan order makes them partially correlated at the poles).

Motion: each visit also gives a flux-weighted centroid; a weighted linear fit
of (ra*cos dec, dec) against time gives the proper motion and its error.
"""
import math

import numpy as np
from scipy import stats

MIN_POINTS = 12
# SPHEREx pixels (6.2") are large compared with the PSF (~7"), so the flux in
# a small aperture changes with where the star lands inside a pixel.  The
# VARIANCE layer does not include that, so a fractional floor is added in
# quadrature.  Measured on the NEP deep field (2026-11-13): without it every
# star -- including Gaia-constant ones -- had chi2/dof 2-28.
SYSTEMATIC_FRAC = 0.03
ENSEMBLE_CHI2_FACTOR = 3.0   # variable = chi2/dof > 3x the field's median star
N_LAMBDA_BINS = 24
VAR_P_VALUE = 1e-3          # chi-square p below this -> variable
VAR_MIN_AMPLITUDE = 0.05    # and robust amplitude above 5 %
LS_FAP_MAX = 1e-3           # periodogram false-alarm probability for a period
MIN_PERIOD_D, MAX_PERIOD_FRACTION = 0.5, 0.5   # up to half the time baseline


def _binned_spectrum(lam, f, w, n_bins=N_LAMBDA_BINS):
    loglam = np.log(lam)
    edges = np.quantile(loglam, np.linspace(0, 1, n_bins + 1))
    edges[-1] += 1e-9
    centres, values = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (loglam >= a) & (loglam < b)
        if sel.sum() < 2:
            continue
        order = np.argsort(f[sel])
        cw = np.cumsum(w[sel][order])
        median = f[sel][order][np.searchsorted(cw, cw[-1] / 2)]
        centres.append(np.median(loglam[sel]))
        values.append(median)
    return np.array(centres), np.array(values)


def spectrum_model(lam, f, err, r=None):
    """S(lambda) evaluated at every point (and the bin nodes)."""
    w = 1.0 / np.maximum(err, 1e-12) ** 2
    target = f if r is None else f / np.where(r > 0, r, 1.0)
    nodes_x, nodes_y = _binned_spectrum(lam, target, w)
    if len(nodes_x) < 2:
        level = np.median(target)
        return np.full_like(f, level), nodes_x, nodes_y
    return np.interp(np.log(lam), nodes_x, nodes_y), nodes_x, nodes_y


def _smooth_in_time(t, r, width_days):
    out = np.empty_like(r)
    for i, ti in enumerate(t):
        sel = np.abs(t - ti) <= width_days / 2
        out[i] = np.median(r[sel])
    return out


def analyse_source(points):
    """points: list of {"mjd","wavelength_um","flux_mjy","err_mjy","flagged",
    "centroid_ra","centroid_dec"} -> summary dict (or None if too few)."""
    pts = [p for p in points
           if not p.get("flagged") and p.get("mjd") and p.get("wavelength_um")
           and p.get("err_mjy") and p["err_mjy"] > 0 and np.isfinite(p["flux_mjy"])]
    if len(pts) < MIN_POINTS:
        return {"n_points": len(pts), "status": "too_few_points"}
    t = np.array([p["mjd"] for p in pts], float)
    lam = np.array([p["wavelength_um"] for p in pts], float)
    f = np.array([p["flux_mjy"] for p in pts], float)
    e = np.array([p["err_mjy"] for p in pts], float)
    e = np.sqrt(e ** 2 + (SYSTEMATIC_FRAC * np.abs(f)) ** 2)
    order = np.argsort(t)
    t, lam, f, e = t[order], lam[order], f[order], e[order]
    pts = [pts[i] for i in order]

    S, _, _ = spectrum_model(lam, f, e)
    good = S > 3 * np.median(e)            # need signal to form a ratio
    if good.sum() < MIN_POINTS:
        return {"n_points": int(good.sum()), "status": "too_faint"}
    t, lam, f, e, S = t[good], lam[good], f[good], e[good], S[good]
    pts = [p for p, g in zip(pts, good) if g]
    r = f / S
    # one refinement: remove slow variability before refitting the spectrum
    baseline = t.max() - t.min()
    r_smooth = _smooth_in_time(t, r, max(baseline / 10, 5.0))
    S, nodes_x, nodes_y = spectrum_model(lam, f, e, r=r_smooth)
    r = f / S
    re = e / S

    chi2 = float(np.sum(((r - 1.0) / re) ** 2))
    dof = len(r) - len(nodes_x)
    p_const = float(stats.chi2.sf(chi2, max(dof, 1)))
    # robust peak-to-peak: 10th-90th percentile spread of the ratio, with the
    # noise contribution removed in quadrature (a constant star -> ~0)
    p10, p90 = np.percentile(r, [10, 90])
    noise_spread = 2 * 1.2816 * float(np.median(re))     # 10-90 % of N(0, re)
    amplitude = float(math.sqrt(max((p90 - p10) ** 2 - noise_spread ** 2, 0.0)))
    variable = bool(p_const < VAR_P_VALUE and amplitude > VAR_MIN_AMPLITUDE)

    period = None
    if baseline > 2 * MIN_PERIOD_D and len(r) >= 20:
        from astropy.timeseries import LombScargle
        ls = LombScargle(t, r, re)
        fmin = 1.0 / (baseline * MAX_PERIOD_FRACTION) if baseline > 0 else 0.01
        freq, power = ls.autopower(minimum_frequency=fmin,
                                   maximum_frequency=1.0 / MIN_PERIOD_D,
                                   samples_per_peak=10)
        if len(power):
            k = int(np.argmax(power))
            fap = float(ls.false_alarm_probability(power[k], minimum_frequency=fmin,
                                                   maximum_frequency=1.0 / MIN_PERIOD_D))
            period = {"period_days": float(1.0 / freq[k]), "power": float(power[k]),
                      "fap": fap, "significant": bool(fap < LS_FAP_MAX)}

    motion = fit_motion(pts)
    return {
        "status": "ok",
        "n_points": int(len(r)),
        "baseline_days": float(baseline),
        "wavelength_range_um": [float(lam.min()), float(lam.max())],
        "chi2_dof": float(chi2 / max(dof, 1)),
        "p_constant": p_const,
        "amplitude": amplitude,
        "variable": variable,
        "period": period,
        "motion": motion,
        "spectrum": {"wavelength_um": [float(math.exp(x)) for x in nodes_x],
                     "flux_mjy": [float(y) for y in nodes_y]},
        "lightcurve": {"mjd": t.round(4).tolist(), "ratio": r.round(4).tolist(),
                       "ratio_err": re.round(4).tolist(),
                       "wavelength_um": lam.round(4).tolist()},
    }


def fit_motion(points, min_points=MIN_POINTS):
    """Weighted linear fit of centroid vs time -> proper motion (mas/yr)."""
    pts = [p for p in points if p.get("centroid_ra") is not None and p.get("mjd")]
    if len(pts) < min_points:
        return None
    t = np.array([p["mjd"] for p in pts]) / 365.25
    ra = np.array([p["centroid_ra"] for p in pts])
    dec = np.array([p["centroid_dec"] for p in pts])
    dec0 = np.median(dec)
    x = (ra - np.median(ra)) * np.cos(np.radians(dec0)) * 3.6e6     # mas
    y = (dec - dec0) * 3.6e6
    t0 = t - t.mean()
    if np.ptp(t0) < 0.05:
        return None
    out = {}
    for name, v in (("pm_ra", x), ("pm_dec", y)):
        slope, intercept = np.polyfit(t0, v, 1)
        resid = v - (slope * t0 + intercept)
        scatter = 1.4826 * np.median(np.abs(resid - np.median(resid)))
        err = scatter / math.sqrt(np.sum(t0 ** 2)) if np.sum(t0 ** 2) > 0 else np.inf
        out[name] = float(slope)
        out[name + "_err"] = float(err)
    total = math.hypot(out["pm_ra"], out["pm_dec"])
    err = math.hypot(out["pm_ra_err"], out["pm_dec_err"]) / math.sqrt(2)
    out["pm_total"] = total
    out["significance"] = float(total / err) if err > 0 else 0.0
    out["n_points"] = len(pts)
    return out


def flag_variables_against_ensemble(results, factor=ENSEMBLE_CHI2_FACTOR):
    """Second opinion across the field: stars measured the same way share the
    same unmodelled systematics, so a real variable must stand out from the
    field's typical star, not just from its own error bars.  Keeps the
    per-star flag as ``variable_self`` and rewrites ``variable``."""
    ok = [r for r in results if r.get("status") == "ok"]
    if len(ok) < 5:            # too few stars for an ensemble: keep per-star flags
        for r in ok:
            r["variable_self"] = r["variable"]
            r["ensemble_chi2_median"] = None
        return None
    med = float(np.median([r["chi2_dof"] for r in ok]))
    for r in ok:
        r["variable_self"] = r["variable"]
        r["ensemble_chi2_median"] = med
        r["variable"] = bool(r["variable"] and r["chi2_dof"] > factor * max(med, 1.0))
    return med
