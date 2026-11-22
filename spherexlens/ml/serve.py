"""
spherexlens.ml.serve  (Phase 5.6, 5.8, 5.10)
Dashboard enricher: features + calibrated real/bogus score per detection.

For every ``payload["detections"][i]``:
    detection["features"] = {name: value | None}          (always)
    detection["ml"] = {"real_bogus": 0.93, "model_version": "rb-...",
                       "reasons": [{"feature", "label", "effect"}],   # top 3
                       "uncertain": 0.35 < p < 0.65}                  (with a model)
and payload["pipeline"]["model_version"], payload["ml_status"].

Reasons are a cheap local attribution: each feature in turn is replaced by its
training median and the change in P(real) is the feature's effect (positive =
pushes towards real); the three largest |effect| are reported with a
human-readable label.

No model on disk -> features only and ``payload["ml_status"] = {"status":
"no_model"}``; this enricher never raises for a missing model.  The ML score
only *ranks*: the rule-based KNOWN/CANDIDATE verdict is never touched.
"""
import json
import os
import threading
import time

import numpy as np

import config
from spherexlens.enrich import register
from spherexlens.ml import paths
from spherexlens.ml.features import (FEATURE_LABELS, FEATURE_NAMES, extract_features,
                                     to_json, to_vector)

_LOCK = threading.Lock()
_STATE = {"key": None, "bundle": None, "error": None}


def _latest():
    p = os.path.join(paths.model_dir(), "LATEST.json")
    try:
        st = os.stat(p)
        with open(p) as f:
            return json.load(f), st.st_mtime
    except Exception:
        return None, None


def load_model(force=False):
    """The current model bundle (or None).  Thread-safe, reloads when LATEST changes."""
    latest, mtime = _latest()
    if latest is None:
        with _LOCK:
            _STATE.update(key=None, bundle=None, error=None)
        return None
    key = (latest.get("model"), mtime)
    with _LOCK:
        if not force and _STATE["key"] == key:
            return _STATE["bundle"]
        try:
            import joblib
            bundle = joblib.load(os.path.join(paths.model_dir(), latest["model"]))
            bundle.setdefault("version", latest.get("version"))
            _STATE.update(key=key, bundle=bundle, error=None)
        except Exception as e:
            _STATE.update(key=key, bundle=None, error=f"{type(e).__name__}: {e}")
        return _STATE["bundle"]


def _proba(bundle, X):
    from spherexlens.ml.train import predict_proba
    return predict_proba(bundle, X)


def explain(bundle, x, p=None, n=None):
    """Top-n reasons for one feature vector x."""
    n = n or config.ML_N_REASONS
    names = bundle.get("features") or list(FEATURE_NAMES)
    med = bundle.get("medians") or {}
    p = float(_proba(bundle, x[None, :])[0]) if p is None else p
    rows, idx = [], []
    for j, name in enumerate(names):
        m = med.get(name)
        if m is None or not np.isfinite(x[j]) or x[j] == m:
            continue
        xr = x.copy()
        xr[j] = m
        rows.append(xr)
        idx.append(j)
    if not rows:
        return []
    alt = _proba(bundle, np.vstack(rows))
    effects = [(names[j], p - float(a)) for j, a in zip(idx, alt)]
    effects = [e for e in effects if abs(e[1]) > 1e-4]
    effects.sort(key=lambda e: -abs(e[1]))
    out = []
    for name, eff in effects[:n]:
        pos, neg = FEATURE_LABELS.get(name, (name, name))
        out.append({"feature": name, "label": pos if eff > 0 else neg,
                    "effect": round(eff, 3)})
    return out


def is_uncertain(p):
    return config.ML_UNCERTAIN_LOW < p < config.ML_UNCERTAIN_HIGH


def _remember(target, detections):
    """Append detection features to ml/data/feature_cache.jsonl (for votes -> labels)."""
    if not config.ML_FEATURE_CACHE:
        return
    try:
        path = paths.feature_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _LOCK, open(path, "a") as f:
            for d in detections:
                if d.get("detection_id") and d.get("features"):
                    f.write(json.dumps({"detection_id": d["detection_id"], "target": target,
                                        "kind": d.get("kind"), "features": d["features"],
                                        "ts": ts}) + "\n")
    except Exception:
        pass                      # read-only deployments simply skip the cache


def score_detections(detections, ctx, bundle=None):
    """Features (+ ml block if a model is given) on a list of detection dicts."""
    vecs = []
    for d in detections:
        f = extract_features(d, ctx)
        d["features"] = to_json(f)
        vecs.append(to_vector(f))
    if bundle is None or not vecs:
        return detections
    X = np.vstack(vecs)
    probs = _proba(bundle, X)
    for d, x, p in zip(detections, X, probs):
        p = float(p)
        d["ml"] = {"real_bogus": round(p, 4), "model_version": bundle.get("version"),
                   "reasons": explain(bundle, x, p), "uncertain": bool(is_uncertain(p))}
    return detections


@register("ml", order=50)
def ml_enricher(payload, ctx):
    dets = payload.get("detections") or []
    bundle = load_model()
    score_detections(dets, ctx or {}, bundle)
    if bundle is None:
        payload["ml_status"] = {"status": "no_model",
                                "detail": _STATE.get("error") or
                                "no trained model (ml/models/LATEST.json); features only"}
    else:
        payload.setdefault("pipeline", {})["model_version"] = bundle.get("version")
        payload["ml_status"] = {"status": "ok", "model_version": bundle.get("version"),
                                "n_scored": len(dets),
                                "n_uncertain": sum(1 for d in dets if (d.get("ml") or {}).get("uncertain"))}
    _remember((ctx or {}).get("target") or payload.get("target"), dets)
