"""
spherexlens.ml.train  (Phase 5.3-5.5, 5.7)
Train the real/bogus classifier.

    python -m spherexlens.ml.train --labels ml/data/labels.parquet
                                   [--heldout-frac 0.2] [--seed N] [--out ml/models]

Validation scheme (no field ever appears on both sides of a split):
  1. ~20% of fields (config.ML_HELDOUT_FIELD_FRACTION, seeded with
     config.ML_RANDOM_SEED) are held out and never used for training or
     calibration.
  2. On the remaining fields, GroupKFold by field gives out-of-fold scores of a
     HistGradientBoostingClassifier (handles NaN features natively).
  3. An isotonic calibrator is fitted on those out-of-fold scores only -- the
     calibrator never sees a score from a model trained on the same row.
  4. The final classifier is refit on all training fields; P(real) =
     isotonic(classifier score).
  5. Held-out metrics: precision / recall / F1 at P >= 0.5, PR-AUC (average
     precision), PR curve, calibration curve, confusion matrix, bootstrap 95%
     CIs (resampling held-out fields, then rows), permutation importance.

Writes ml/models/real_bogus-<version>.joblib + .json, ml/models/LATEST.json and
ml/MODEL_CARD.md.  Version: rb-YYYY.MM.DD-N.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

import config
import version as pipeline_version
from spherexlens.ml import paths
from spherexlens.ml.features import FEATURE_NAMES

MODEL_PREFIX = "real_bogus-"


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
def make_classifier(seed):
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(
        max_iter=200, learning_rate=0.08, max_leaf_nodes=15, min_samples_leaf=10,
        l2_regularization=1.0, early_stopping=False, random_state=seed)


def predict_proba(bundle, X):
    """Calibrated P(real) for rows of X (n, n_features)."""
    X = np.asarray(X, dtype=float)
    raw = bundle["model"].predict_proba(X)[:, 1]
    iso = bundle.get("isotonic")
    p = iso.predict(raw) if iso is not None else raw
    return np.clip(p, 0.0, 1.0)


def split_fields(fields, frac, seed):
    """(train_fields, heldout_fields).  Held out only if >= 3 fields."""
    uniq = sorted(set(fields))
    rng = np.random.default_rng(seed)
    n_hold = int(round(frac * len(uniq)))
    if len(uniq) >= 3:
        n_hold = min(max(n_hold, 1), len(uniq) - 2)
    else:
        n_hold = 0
    hold = set(rng.choice(uniq, n_hold, replace=False).tolist()) if n_hold else set()
    return [f for f in uniq if f not in hold], sorted(hold)


def oof_scores(X, y, groups, seed, n_folds=None):
    """Out-of-fold raw scores with GroupKFold by field; also returns the folds."""
    from sklearn.model_selection import GroupKFold
    n_groups = len(set(groups))
    k = min(n_folds or config.ML_CV_FOLDS, n_groups)
    oof = np.full(len(y), np.nan)
    folds = []
    if k < 2:
        return oof, folds
    for tr, te in GroupKFold(n_splits=k).split(X, y, groups):
        folds.append((sorted(set(np.asarray(groups)[tr])), sorted(set(np.asarray(groups)[te]))))
        if len(set(y[tr])) < 2:
            continue
        clf = make_classifier(seed).fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])[:, 1]
    return oof, folds


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def classification_metrics(y, p, threshold=None):
    from sklearn.metrics import average_precision_score, precision_recall_curve
    thr = config.ML_DECISION_THRESHOLD if threshold is None else threshold
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    pred = (p >= thr).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    out = {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
           "n": int(len(y)), "n_real": int(y.sum()), "threshold": thr,
           "confusion": {"labels": ["real", "bogus"], "matrix": [[tp, fn], [fp, tn]]}}
    if len(set(y)) == 2:
        out["pr_auc"] = round(float(average_precision_score(y, p)), 4)
        pr, rc, _ = precision_recall_curve(y, p)
        idx = np.unique(np.linspace(0, len(pr) - 1, min(len(pr), 50)).astype(int))
        out["pr_curve"] = [[round(float(rc[i]), 4), round(float(pr[i]), 4)] for i in idx]
    else:
        out["pr_auc"] = None
        out["pr_curve"] = []
    out["calibration"] = calibration_curve(y, p)
    return out


def calibration_curve(y, p, n_bins=10):
    bins = np.linspace(0, 1, n_bins + 1)
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        sel = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if sel.sum():
            out.append([round(float(p[sel].mean()), 4), round(float(y[sel].mean()), 4),
                        int(sel.sum())])
    return out


def bootstrap_ci(y, p, groups, n_boot=None, seed=None):
    """95% CIs: resample fields with replacement, then rows within them."""
    from sklearn.metrics import average_precision_score
    n_boot = int(n_boot or min(config.EVAL_BOOTSTRAP, 500))
    rng = np.random.default_rng(config.ML_RANDOM_SEED if seed is None else seed)
    groups = np.asarray(groups)
    uniq = np.unique(groups)
    by = {g: np.nonzero(groups == g)[0] for g in uniq}
    vals = {"precision": [], "recall": [], "f1": [], "pr_auc": []}
    thr = config.ML_DECISION_THRESHOLD
    for _ in range(n_boot):
        if len(uniq) > 1:
            gs = rng.choice(uniq, len(uniq))
            idx = np.concatenate([rng.choice(by[g], len(by[g])) for g in gs])
        else:
            idx = rng.choice(len(y), len(y))
        yy, pp = y[idx], p[idx]
        pred = pp >= thr
        tp = np.sum(pred & (yy == 1))
        prec = tp / pred.sum() if pred.sum() else np.nan
        rec = tp / (yy == 1).sum() if (yy == 1).sum() else np.nan
        vals["precision"].append(prec)
        vals["recall"].append(rec)
        vals["f1"].append(2 * prec * rec / (prec + rec) if prec + rec else np.nan)
        if len(set(yy)) == 2:
            vals["pr_auc"].append(average_precision_score(yy, pp))
    ci = {}
    for k, v in vals.items():
        v = np.array([x for x in v if np.isfinite(x)])
        if v.size >= 10:
            ci[k] = [round(float(np.percentile(v, 2.5)), 4),
                     round(float(np.percentile(v, 97.5)), 4)]
    ci["method"] = ("field bootstrap" if len(uniq) > 1 else "row bootstrap") + f", n={n_boot}"
    return ci


def permutation_importance(bundle, X, y, n_repeats=5, seed=None):
    """Mean drop in average precision when a feature column is shuffled."""
    from sklearn.metrics import average_precision_score
    if len(set(y)) < 2:
        return {}
    rng = np.random.default_rng(config.ML_RANDOM_SEED if seed is None else seed)
    base = average_precision_score(y, predict_proba(bundle, X))
    out = {}
    for j, name in enumerate(FEATURE_NAMES):
        if not np.isfinite(X[:, j]).any():
            out[name] = 0.0
            continue
        drops = []
        for _ in range(n_repeats):
            Xp = X.copy()
            Xp[:, j] = rng.permutation(Xp[:, j])
            drops.append(base - average_precision_score(y, predict_proba(bundle, Xp)))
        out[name] = round(float(np.mean(drops)), 5)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------------
# versioning / saving
# ---------------------------------------------------------------------------
def next_version(model_dir, when=None):
    day = time.strftime("%Y.%m.%d", time.gmtime(when))
    n = 1
    if os.path.isdir(model_dir):
        for name in os.listdir(model_dir):
            prefix = f"{MODEL_PREFIX}rb-{day}-"
            if name.startswith(prefix) and name.endswith(".json"):
                try:
                    n = max(n, int(name[len(prefix):-5]) + 1)
                except ValueError:
                    pass
    return f"rb-{day}-{n}"


def save_model(bundle, card, model_dir):
    import joblib
    os.makedirs(model_dir, exist_ok=True)
    v = card["version"]
    base = f"{MODEL_PREFIX}{v}"
    joblib.dump(bundle, os.path.join(model_dir, base + ".joblib"), compress=3)
    with open(os.path.join(model_dir, base + ".json"), "w") as f:
        json.dump(card, f, indent=1)
    with open(os.path.join(model_dir, "LATEST.json"), "w") as f:
        json.dump({"version": v, "model": base + ".joblib", "json": base + ".json",
                   "generated_utc": card["generated_utc"]}, f, indent=1)
    return os.path.join(model_dir, base + ".joblib")


def model_card_md(card):
    m = card["metrics"]
    h = m.get("heldout") or {}
    cv = m.get("cv") or {}
    ds = card["data"]

    def fmt(block, key):
        v = block.get(key)
        ci = (block.get("ci95") or {}).get(key)
        if v is None:
            return "n/a"
        return f"{v:.3f}" + (f" (95% CI {ci[0]:.3f}-{ci[1]:.3f})" if ci else "")

    imp = list((card.get("permutation_importance") or {}).items())[:8]
    lines = [
        f"# Model card: SPHEREx-Lens real/bogus classifier `{card['version']}`",
        "",
        f"Generated {card['generated_utc']} by pipeline {card['pipeline']['pipeline_version']} "
        f"(config {card['pipeline']['config_hash']}).",
        "",
        "## Intended use",
        "Ranks ZOGY difference-image detections by the calibrated probability that they "
        "are real astrophysical changes (movers, variables) rather than artefacts. It is a "
        "*ranking aid* for human review and public voting: it never changes the rule-based "
        "KNOWN/CANDIDATE verdict, and a low score is not grounds to discard a detection "
        "without looking. Not validated for crowded Galactic-plane fields or for "
        "extended / saturated sources.",
        "",
        "## Model",
        f"HistGradientBoostingClassifier on {len(card['features'])} features "
        "(spherexlens/ml/features.py, fixed order) + isotonic calibration fitted on "
        "out-of-fold scores. Missing features are NaN and handled natively.",
        "",
        "## Data",
        f"{ds['n']} labelled detections ({ds['n_real']} real, {ds['n_bogus']} bogus) from "
        f"{len(ds['fields'])} fields. Training fields: {len(card['train_fields'])}; "
        f"held-out fields (never used for training or calibration): "
        f"{', '.join(card['heldout_fields']) or 'none (too few fields)'}.",
        "",
        "Label sources (count): " + ", ".join(f"{k} {v}" for k, v in sorted(ds['by_source'].items())),
        "",
        "## Label sources and their biases",
        "- **injection (real)** -- synthetic point sources added to real co-adds with the "
        "measured PSF and recovered by the strict test. Biased towards clean, isolated, "
        "PSF-perfect sources; real movers with odd colours or blends are under-represented.",
        "- **answer_key (real)** -- catalogued high-proper-motion stars, VSX variables and "
        "known asteroids that were detected. Biased towards bright, catalogued objects.",
        "- **null_test (bogus)** -- detections in half-vs-half subtractions of one epoch. "
        "Representative of noise and wavelength-sampling systematics, but not of artefacts "
        "that only appear between epochs (persistence, ghosts at new roll angles).",
        "- **mask (bogus)** -- detections on masked or edge pixels; defined using "
        "`masked_frac`, which is also a feature, so that feature's importance is inflated.",
        "- **vote** -- public votes (majority, ties dropped) when available.",
        "",
        "## Validation",
        f"Split: {card['split']}. Decision threshold P >= {config.ML_DECISION_THRESHOLD}.",
        "",
        "| metric | held-out fields | GroupKFold out-of-fold (training fields) |",
        "|---|---|---|",
    ]
    for k in ("precision", "recall", "f1", "pr_auc"):
        lines.append(f"| {k} | {fmt(h, k)} | {fmt(cv, k)} |")
    lines += [
        "",
        f"Held-out rows: {h.get('n', 0)} ({h.get('n_real', 0)} real). "
        f"Confusion [[TP, FN], [FP, TN]]: {(h.get('confusion') or {}).get('matrix')}.",
        "",
        "Top permutation importances (drop in average precision on held-out data): "
        + (", ".join(f"`{k}` {v:+.3f}" for k, v in imp) or "n/a"),
        "",
        "## Limitations",
        "- Small number of fields: the confidence intervals are wide and per-field "
        "behaviour (zodiacal level, crowding, ecliptic latitude) is barely sampled.",
        "- Labels are proxies (see biases). Real precision on genuinely new sources "
        "is unknown until candidates are followed up.",
        "- Features that the pipeline does not yet supply (cross-detector colour, "
        "spectral indices, exposure quality, n_pairs_seen) are NaN and carry no weight.",
        "- Retrain whenever the detection pipeline or the S-map normalisation changes; "
        "the model is tied to the config hash above.",
        "",
        f"Version `{card['version']}`; artefacts `ml/models/{MODEL_PREFIX}{card['version']}.joblib/.json`.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------
def train(X, y, fields, sources=None, seed=None, heldout_frac=None, model_dir=None,
          card_path=None, n_boot=None, write=True):
    seed = config.ML_RANDOM_SEED if seed is None else seed
    heldout_frac = config.ML_HELDOUT_FIELD_FRACTION if heldout_frac is None else heldout_frac
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=int)
    fields = np.asarray([str(f) for f in fields])
    if len(set(y)) < 2:
        raise ValueError("need both real and bogus labels to train")
    if len(set(fields)) < 2:
        raise ValueError("need >= 2 fields: validation is grouped by field")
    train_f, hold_f = split_fields(fields, heldout_frac, seed)
    tr = np.isin(fields, train_f)
    te = np.isin(fields, hold_f)
    if len(set(y[tr])) < 2:
        raise ValueError("training fields hold only one class")
    oof, folds = oof_scores(X[tr], y[tr], fields[tr], seed)
    ok = np.isfinite(oof)
    from sklearn.isotonic import IsotonicRegression
    iso = None
    if ok.sum() >= 10 and len(set(y[tr][ok])) == 2:
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(oof[ok], y[tr][ok])
    clf = make_classifier(seed).fit(X[tr], y[tr])
    med = np.nanmedian(np.where(np.isfinite(X[tr]), X[tr], np.nan), axis=0)
    medians = {n: (None if not np.isfinite(v) else float(v)) for n, v in zip(FEATURE_NAMES, med)}
    bundle = {"model": clf, "isotonic": iso, "features": list(FEATURE_NAMES),
              "medians": medians}
    metrics = {}
    if ok.sum() and len(set(y[tr][ok])) == 2:
        p_oof = iso.predict(oof[ok]) if iso is not None else oof[ok]
        cv = classification_metrics(y[tr][ok], p_oof)
        cv["ci95"] = bootstrap_ci(y[tr][ok], p_oof, fields[tr][ok], n_boot, seed)
        cv["n_fields"] = len(set(fields[tr]))
        cv["note"] = "isotonic fitted on these same OOF scores: calibration is optimistic"
        metrics["cv"] = cv
    importance = {}
    if te.any():
        p_te = predict_proba(bundle, X[te])
        ho = classification_metrics(y[te], p_te)
        ho["ci95"] = bootstrap_ci(y[te], p_te, fields[te], n_boot, seed)
        ho["n_fields"] = len(hold_f)
        metrics["heldout"] = ho
        importance = permutation_importance(bundle, X[te], y[te], seed=seed)
    split = (f"GroupKFold({len(folds)}) by field on {len(train_f)} training fields + "
             f"{len(hold_f)} held-out fields" if hold_f else
             f"GroupKFold({len(folds)}) by field only ({len(set(fields))} fields: too few "
             "to hold any out)")
    mdir = model_dir or paths.model_dir()
    from spherexlens.ml.labels import summary as label_summary
    rows = [{"field": f, "label": int(l), "source": (sources[i] if sources is not None else "unknown")}
            for i, (f, l) in enumerate(zip(fields, y))]
    card = {
        "version": next_version(mdir), "kind": "real_bogus",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipeline": pipeline_version.pipeline_stamp(),
        "features": list(FEATURE_NAMES), "medians": medians,
        "train_fields": train_f, "heldout_fields": hold_f,
        "folds": [{"train": a, "test": b} for a, b in folds],
        "split": split, "seed": seed, "metrics": metrics,
        "permutation_importance": importance,
        "data": label_summary(rows),
        "calibration": "isotonic on GroupKFold out-of-fold scores" if iso is not None else "none",
        "uncertain_band": [config.ML_UNCERTAIN_LOW, config.ML_UNCERTAIN_HIGH],
    }
    bundle["version"] = card["version"]
    if write:
        save_model(bundle, card, mdir)
        cp = card_path or os.path.join(os.path.dirname(mdir.rstrip("/\\")), "MODEL_CARD.md")
        with open(cp, "w") as f:
            f.write(model_card_md(card))
    return bundle, card


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train the real/bogus classifier")
    ap.add_argument("--labels", default=os.path.join(paths.data_dir(), "labels.parquet"))
    ap.add_argument("--seed", type=int, default=config.ML_RANDOM_SEED)
    ap.add_argument("--heldout-frac", type=float, default=config.ML_HELDOUT_FIELD_FRACTION)
    ap.add_argument("--out", default=None, help="model dir (default config.ML_MODEL_DIR)")
    ap.add_argument("--card", default=None, help="MODEL_CARD.md path (default ml/MODEL_CARD.md)")
    args = ap.parse_args(argv)
    from spherexlens.ml.labels import read_labels
    X, y, meta = read_labels(args.labels)
    try:
        _, card = train(X, y, meta["field"], meta.get("source"), seed=args.seed,
                        heldout_frac=args.heldout_frac,
                        model_dir=paths.resolve(args.out) if args.out else None,
                        card_path=args.card)
    except ValueError as e:
        print(f"train: cannot train: {e}")
        return 1
    h = card["metrics"].get("heldout") or card["metrics"].get("cv") or {}
    print(f"train: {card['version']}  {card['split']}")
    print(f"train: precision {h.get('precision')} recall {h.get('recall')} "
          f"f1 {h.get('f1')} pr_auc {h.get('pr_auc')} (n={h.get('n')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
