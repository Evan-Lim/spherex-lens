"""
spherexlens.ml.v18 -- serving for the v18 real/bogus model (the default since Nov 2026).

v18 comes from a data-centric study (see the SPHEREx-Lens ML report, v1-v18): the model recipe is the
same family as the retired v16 (three tree "experts" plus a small "judge" averaged over 5 seeds), but it was
retrained on much better data:
  * 281 real brightness changes of 77 catalogued stars from SPHEREx DR1 + QR3 (14 patches), measured at
    matched wavelength, 342 real non-changing stars, each star weighted equally;
  * co-add simulations of movers, brightness changes and glitches.
It scores the website's own images: wavelength-matched, multi-detector co-adds of each epoch.

Inputs (61 numbers per detection, computed with the same functions as in training; see imagefeat.py):
  16 production features (bright_star_gmag capped at 16 -> missing) + 7 stamp numbers + 6 residual
  shapes + 7 brightness-vs-shift numbers + 10 field-context numbers + 9 colour numbers + 6 flags for
  missing values.  The judge also sees the three experts' opinions (as logits).

Held-out results (leave-patches-out, real sky): changing stars AUC 0.989 (259/281 caught at 0.5, 8/342
steady flagged); co-add asteroid exam 61/65 caught, 0/76 steady flagged; simulated glitches 3.3 % passed.
Not yet tested on live data or by independent reviewers, so the score stays a ranking aid: the
rule-based KNOWN/CANDIDATE verdict is never changed.

Tiers: score >= config.ML_V18_CANDIDATE -> "candidate"; >= config.ML_V18_REVIEW -> "review" (vote queue);
otherwise "low".  ``on_catalogued_star`` tells whether a Gaia star sits within 4.5" (the variable-star
case) or not (movers, new sources, glitches).
"""
import numpy as np

import config
from spherexlens.ml import imagefeat as V16
from spherexlens.ml.features import FEATURE_NAMES, extract_features

USED = V16.USED
MISS = ("fwhm_ratio", "psf_chi2", "noise_ratio", "snr_minus")


def block(X22, der, phy, p2, cf, co):
    """The 61 model inputs from their parts; identical to the training code (v17a.block)."""
    X = np.array(X22, np.float32, copy=True, ndmin=2)
    gi = FEATURE_NAMES.index("bright_star_gmag")
    cap = np.isfinite(X[:, gi]) & (X[:, gi] >= 15.999)
    X[cap, gi] = np.nan
    base = X[:, [FEATURE_NAMES.index(n) for n in USED]]
    co = np.asarray(co, np.float32).reshape(len(X), -1)
    miss = np.column_stack([~np.isfinite(X[:, FEATURE_NAMES.index(n)]) for n in MISS]
                           + [~np.isfinite(co[:, 0]), cap]).astype(np.float32)
    parts = [base, der, phy, p2, cf, co, miss]
    return np.column_stack([np.asarray(p, np.float32).reshape(len(X), -1) for p in parts]).astype(np.float32)


def vectors(detections, ctx):
    """(F, on_star) for the detections; rows for detections without image context are all NaN."""
    from spherexlens.eval import harness
    n = len(detections)
    F = np.full((n, 61), np.nan, np.float32)
    on_star = np.zeros(n, bool)
    if not (ctx.get("stacks") and ctx.get("noise_maps")):
        return F, on_star
    pairs = {}
    for i, d in enumerate(detections):
        pos = harness.detection_position(d)
        pr = V16._pair_of(d, ctx)
        if pos is None or pr is None:
            continue
        if pr not in pairs:
            try:
                pairs[pr] = V16._pair_setup(ctx, d, *pr)
            except Exception:
                pairs[pr] = None
        P = pairs[pr]
        if P is None:
            continue
        try:
            f = extract_features(d, ctx)
        except Exception:
            continue
        x22 = np.array([float(f.get(k)) if f.get(k) is not None else np.nan for k in FEATURE_NAMES], np.float32)
        st = V16.make_stamp(P["a"], P["b"], P["D"], P["S"], P["na"], P["nb"], P["sd"], int(pos[0]), int(pos[1]))[None]
        cf = np.array(V16.ctx_row(st[0].astype(np.float32), P["stats"]), np.float32)
        try:
            co = np.array(V16.col_row(pos, P["cmod"], P["a"], P["b"]), np.float32)
        except Exception:
            co = np.full(len(V16.COL), np.nan, np.float32)
        F[i] = block(x22, V16.derived(st), V16.physics(st), V16.physics2(st), cf[None], co[None])[0]
        on_star[i] = bool(np.isfinite(co[2]))
    return F, on_star


def judge_inputs(bundle, F):
    pe = [V16.forest_proba(bundle["experts"][e], F) for e in bundle["expert_order"]]
    return np.column_stack([V16._logit(p)[:, None] for p in pe] + [F]).astype(np.float32)


def predict(bundle, F):
    """P(real) per row; rows that could not be measured get NaN."""
    F = np.asarray(F, np.float32)
    Xj = judge_inputs(bundle, F)
    p = np.mean([V16.forest_proba(t, Xj) for t in bundle["judges"]], 0)
    p[np.all(np.isnan(F), 1)] = np.nan
    return p, Xj


def tier(p):
    if not np.isfinite(p):
        return None
    if p >= config.ML_V18_CANDIDATE:
        return "candidate"
    return "review" if p >= config.ML_V18_REVIEW else "low"


def explain(bundle, xj, p, n=3):
    """Top-n reasons: each judge input in turn set to its training median."""
    names = [f"expert_{e}" for e in bundle["expert_order"]] + list(bundle["feature_names"])
    med = np.asarray(bundle.get("medians") or [], float)
    if len(med) != len(xj):
        return []
    rows, idx = [], []
    for k in range(len(xj)):
        if not np.isfinite(xj[k]) or not np.isfinite(med[k]) or xj[k] == med[k]:
            continue
        r = xj.copy(); r[k] = med[k]; rows.append(r); idx.append(k)
    if not rows:
        return []
    alt = np.mean([V16.forest_proba(t, np.vstack(rows)) for t in bundle["judges"]], 0)
    eff = sorted(((names[k], p - float(q)) for k, q in zip(idx, alt)), key=lambda e: -abs(e[1]))
    return [{"feature": k, "label": k.replace("_", " "), "effect": round(e, 3)} for k, e in eff[:n] if abs(e) > 1e-4]
