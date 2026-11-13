"""
spherexlens.eval.threshold  (Phase 4.4)
Choose the detection cut from the measured null distribution.

    python -m spherexlens.eval.threshold --null eval/null_peaks.json [--out eval/threshold.json]

(eval.run calls ``choose_threshold`` directly and writes the same file.)

Method
------
1. Pool every null-test |S| extremum (5x5 local max of S and of -S), each
   weighted by full-field area / usable area of its realisation.
2. Empirical expected false alarms per field above t:
       E_emp(t) = sum_i w_i [p_i > t] / n_realisations
3. A finite number of nulls cannot resolve 0.1 per field directly, so the tail
   is also modelled.  For a smooth Gaussian random field the expected number of
   local extrema above a high t scales like the 2-D Euler characteristic
   density, t * exp(-t^2/2); its amplitude is fitted to E_emp on
   ``fit_range``.  E(t) = max(E_emp(t), E_model(t)) -- a heavy (non-Gaussian)
   tail measured in the null always wins over the model.
4. sigma = smallest t on a 0.05 grid with E(t) <= target
   (config.TARGET_FALSE_ALARMS_PER_FIELD).

Counting both maxima and minima is conservative: an unpaired trough only
becomes a detection with the new "monopole_negative" kind.

For comparison we also report the cut Benjamini-Hochberg FDR (q = 0.05) gives
over the pixel p-values of the S maps (two-sided normal).  BH is valid under
positive dependence (PRDS), which PSF-smoothed noise satisfies; when no pixel
is rejected the BH cut equals the Bonferroni bound z(q / 2m).
"""
import argparse
import json
import os

import numpy as np
from scipy.stats import norm

import config
import version

GRID = np.round(np.arange(3.0, 12.0001, 0.05), 2)


def euler_shape(t):
    t = np.asarray(t, dtype=float)
    return t * np.exp(-0.5 * t * t)


def expected_curve(peaks, weights, n_realisations, grid=GRID, fit_range=(3.0, 4.0)):
    """(E_emp, E_model, A) on `grid`."""
    peaks = np.asarray(peaks, dtype=float)
    weights = np.ones_like(peaks) if weights is None else np.asarray(weights, dtype=float)
    n = max(int(n_realisations), 1)
    order = np.argsort(peaks)
    p, w = peaks[order], weights[order]
    tail = np.concatenate([np.cumsum(w[::-1])[::-1], [0.0]])
    idx = np.searchsorted(p, grid, side="right")
    e_emp = tail[idx] / n
    sel = (grid >= fit_range[0]) & (grid <= fit_range[1]) & (e_emp > 0)
    if sel.any():
        amp = float(e_emp[sel].sum() / euler_shape(grid[sel]).sum())
    else:
        amp = 0.0
    return e_emp, amp * euler_shape(grid), amp


def choose_threshold(peaks, weights, n_realisations, target=None, grid=GRID,
                     fit_range=(3.0, 4.0)):
    """Detection sigma whose expected null false alarms per field <= target."""
    target = config.TARGET_FALSE_ALARMS_PER_FIELD if target is None else float(target)
    if n_realisations <= 0:
        return None
    e_emp, e_mod, amp = expected_curve(peaks, weights, n_realisations, grid, fit_range)
    e = np.maximum(e_emp, e_mod)
    ok = np.nonzero(e <= target)[0]
    if not ok.size:
        return None
    i = int(ok[0])
    # contiguous: everything above the cut also has to stay below target
    while i < len(grid) and np.any(e[i:] > target):
        i += 1
    i = min(i, len(grid) - 1)
    sigma = float(grid[i])
    return {
        "sigma": sigma,
        "false_alarms_per_field": round(float(e[i]), 4),
        "false_alarms_per_field_empirical": round(float(e_emp[i]), 4),
        "target_false_alarms_per_field": target,
        "method": "null-test extrema + Gaussian-field (Euler) tail fit",
        "fit_amplitude": amp, "n_realisations": int(n_realisations),
        "n_peaks": int(len(peaks)),
        "curve": [[float(t), round(float(a), 5), round(float(b), 5)]
                  for t, a, b in zip(grid[::10], e_emp[::10], e_mod[::10])],
    }


def bh_sigma(S, valid=None, q=None):
    """
    The |S| cut BH-FDR at level q gives on one S map.
    Returns (sigma, n_rejected, m).
    """
    q = config.BH_FDR_Q if q is None else q
    s = np.asarray(S, dtype=float)
    if valid is not None:
        s = s[np.asarray(valid, dtype=bool)]
    s = np.abs(s[np.isfinite(s)]).ravel()
    m = s.size
    if m == 0:
        return None, 0, 0
    p = np.sort(2.0 * norm.sf(s))
    crit = q * np.arange(1, m + 1) / m
    below = np.nonzero(p <= crit)[0]
    if below.size:
        k = int(below[-1]) + 1
        return float(norm.isf(p[k - 1] / 2.0)), k, m
    return float(norm.isf(q / (2.0 * m))), 0, m


def bh_summary(maps):
    """maps: [(S, valid)] -> {"sigma_median", "n_maps", "n_rejected_total"}."""
    vals, rej = [], 0
    for S, valid in maps:
        sig, k, m = bh_sigma(S, valid)
        if sig is not None:
            vals.append(sig)
            rej += k
    if not vals:
        return None
    return {"sigma_median": round(float(np.median(vals)), 3),
            "sigma_range": [round(float(min(vals)), 3), round(float(max(vals)), 3)],
            "n_maps": len(vals), "n_rejected_total": int(rej), "q": config.BH_FDR_Q}


def threshold_record(choice, bh=None):
    """The eval/threshold.json document."""
    rec = {"sigma": choice["sigma"],
           "false_alarms_per_field": choice["false_alarms_per_field"],
           "method": choice["method"] + (" ; BH FDR q=%.2f reported" % config.BH_FDR_Q
                                         if bh else ""),
           "pipeline": version.pipeline_stamp(),
           "target_false_alarms_per_field": choice["target_false_alarms_per_field"],
           "n_realisations": choice["n_realisations"]}
    if bh:
        rec["bh_fdr"] = bh
    return rec


def write_threshold(path, record):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--null", required=True,
                    help="JSON with {peaks, weights, n_realisations} (eval.run writes it)")
    ap.add_argument("--out", default=os.path.join(config.EVAL_DIR, "threshold.json"))
    ap.add_argument("--target", type=float, default=None)
    args = ap.parse_args(argv)
    with open(args.null) as f:
        d = json.load(f)
    choice = choose_threshold(d["peaks"], d.get("weights"), d["n_realisations"], args.target)
    if choice is None:
        print("threshold: not enough null data")
        return 1
    write_threshold(args.out, threshold_record(choice, d.get("bh_fdr")))
    print(f"threshold: sigma={choice['sigma']} "
          f"(expected {choice['false_alarms_per_field']} false alarms/field) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
