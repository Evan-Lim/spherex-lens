"""
spherexlens.eval.metrics  (Phase 4.5)
Run every evaluation on one field and assemble eval/metrics.json exactly as
spherexlens/CONTRACT.md defines it.  The CLI is spherexlens.eval.run.
"""
import json
import os
import time

import numpy as np

import config
import version
from spherexlens.eval import answer_key, harness, injection, null_test, threshold


def default_options(quick=False, seed=None):
    seed = config.ML_RANDOM_SEED if seed is None else seed
    if quick:
        return {"quick": True, "seed": seed, "n_splits": 2, "max_groups": 4,
                "max_per_half": 4, "max_per_band": 4, "n_trials": 6,
                "snr_grid": (5, 10, 20), "mu_grid": (5, 20), "n_boot": 300}
    return {"quick": False, "seed": seed, "n_splits": config.EVAL_NULL_SPLITS,
            "max_groups": None, "max_per_half": None, "max_per_band": None,
            "n_trials": config.EVAL_INJECT_TRIALS,
            "snr_grid": config.EVAL_INJECT_SNR_GRID, "mu_grid": config.EVAL_INJECT_MU_GRID,
            "n_boot": config.EVAL_BOOTSTRAP}


def _on_edge(pos, npix, margin):
    y, x = pos
    return not (margin <= x < npix - margin and margin <= y < npix - margin)


def evaluate_field(field, opts, sink=None, sigma=None):
    """
    Null test + strict injection + answer key on one field.
    sink -- optional ml.labels.LabelSink; every run feeds it labelled detections.
    Returns a dict of per-field results (JSON-safe except "bh_maps").
    """
    t0 = time.time()
    name = field["target"]
    seed = opts.get("seed")
    out = {"target": name, "n_exposures": len(field["exposures"]),
           "epochs": sorted({x["epoch"] for x in field["exposures"]}, key=str)}

    def on_null(r, key):
        for d in r["detections"]:
            sink.add(d, r["ctx"], name, "null_test", 0, f"{key[0]}/{key[1]}")

    out["null"] = null_test.run_null_test(
        field, n_splits=opts.get("n_splits"), sigma=sigma, seed=seed,
        max_groups=opts.get("max_groups"), max_per_half=opts.get("max_per_half"),
        on_realisation=on_null if sink is not None else None)

    out["bh_maps"] = []
    pair = harness.deep_pair(field)
    if pair is None:
        out["pair"] = None
        out["note"] = "no epoch pair with a shared band"
        out["seconds"] = round(time.time() - t0, 1)
        return out
    ea, eb = pair
    rng = np.random.default_rng(seed)
    ctx = harness.run_pair(field, ea, eb, sigma=sigma, max_per_band=opts.get("max_per_band"),
                           rng=rng)
    if ctx is None:
        out["pair"] = None
        out["note"] = "co-add of the deep pair failed"
        return out
    dets = ctx["detections"]
    nm = ctx["noise_maps"]
    valid = harness.valid_mask([nm[ea], nm[eb]], ctx["excluded"],
                               margin=config.EVAL_EDGE_MARGIN_PX)
    if ctx.get("edge_map") is not None:        # footprint edges (step2 extras)
        from scipy.ndimage import binary_dilation
        edge = np.asarray(ctx["edge_map"]) > 0
        if edge.any():
            valid &= ~binary_dilation(edge, iterations=max(1, getattr(config, "EDGE_MASK_PX", 4)))
    out["pair"] = [ea, eb]
    out["dt_years"] = round(ctx["dt_years"], 4)
    out["n_exposures_pair"] = {str(k): v for k, v in ctx["n_exposures"].items()}
    out["n_detections"] = len(dets)
    out["psf_model"] = ctx["psf"]["model"]
    out["bh_maps"].append((ctx["S"], valid))

    def on_rec(det, ictx, src):
        a, b = ictx["stacks_injected"]
        fctx = dict(ctx, D=ictx["D"], S=ictx["S"], stacks={ea: a, eb: b}, _ml_cache={})
        det = dict(det, epochs=(ea, eb))
        sink.add(det, fctx, name, "injection", 1, src["category"])

    avoid = [harness.detection_position(d) for d in dets if harness.detection_position(d)]
    out["injection"] = injection.run_strict_injection(
        ctx["stacks"][ea], ctx["stacks"][eb], nm[ea], nm[eb], psf=ctx["psf"],
        snr_grid=opts.get("snr_grid"), mu_grid=opts.get("mu_grid"),
        n_trials=opts.get("n_trials"), seed=seed, dt_years=ctx["dt_years"],
        pixscale=field["pixscale"], valid=valid, avoid=avoid, sigma_thresh=sigma,
        on_recovered=on_rec if sink is not None else None)

    key = answer_key.build_answer_key(field["catalogs"], field["epoch_mjd"].get(ea),
                                      field["epoch_mjd"].get(eb), field["wcs"],
                                      field["npix"], valid)
    asteroids = [d for d in dets if (d.get("asteroid") or {}).get("status") == "match"]
    out["answer_key"] = answer_key.score(key, dets, asteroids)
    out["answer_key_items"] = [{k: v for k, v in o.items() if k != "_match"} for o in key]

    if sink is not None:
        matched = set()
        for o in key:
            m = o.get("_match")
            if m is not None:
                matched.add(id(m))
                sink.add(m, ctx, name, "answer_key", 1, o["class"])
        from spherexlens.ml.features import masked_fraction
        for d in dets:
            if id(d) in matched:
                continue
            p = harness.detection_position(d)
            if p is None:
                continue
            mf = masked_fraction(ctx, p[0], p[1], [ea, eb])
            if _on_edge(p, field["npix"], config.EVAL_EDGE_MARGIN_PX // 2) or mf >= 0.5:
                sink.add(d, ctx, name, "mask", 0, "edge" if mf < 0.5 else "masked")
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ---------------------------------------------------------------------------
# classifier block
# ---------------------------------------------------------------------------
def classifier_block():
    """CONTRACT "classifier" block from ml/models/LATEST.json, or None."""
    from spherexlens.ml import paths
    latest = os.path.join(paths.model_dir(), "LATEST.json")
    try:
        with open(latest) as f:
            lt = json.load(f)
        with open(os.path.join(paths.model_dir(), lt["json"])) as f:
            card = json.load(f)
    except Exception:
        return None
    m = card.get("metrics", {}).get("heldout") or card.get("metrics", {}).get("cv") or {}
    if not m:
        return None
    keep = ("precision", "recall", "f1", "pr_auc", "ci95", "pr_curve", "calibration",
            "confusion", "n", "n_real", "n_fields")
    block = {k: m[k] for k in keep if k in m}
    block["split"] = card.get("split", "GroupKFold by field + held-out fields")
    block["model_version"] = card.get("version")
    return block


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------
def build_metrics(field_results, opts, threshold_choice=None, bh=None):
    nulls = [r["null"] for r in field_results if r.get("null")]
    null_block = null_test.summarise(nulls, seed=opts.get("seed"), n_boot=opts.get("n_boot"))
    injections = [r.get("injection") for r in field_results if r.get("injection")]
    inj = injection.merge(injections) if injections else None
    ak_keys = [r.get("answer_key_items") or [] for r in field_results]
    ak = answer_key.summarise(ak_keys, seed=opts.get("seed"), n_boot=opts.get("n_boot"))

    m = {"pipeline": version.pipeline_stamp(),
         "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "options": {k: (list(v) if isinstance(v, tuple) else v) for k, v in opts.items()},
         "detection_sigma_used": config.DETECTION_SIGMA,
         "s_normalisation": config.EVAL_S_NORMALISATION}
    if threshold_choice:
        m["threshold"] = {"sigma": threshold_choice["sigma"],
                          "false_alarms_per_field": threshold_choice["false_alarms_per_field"],
                          "method": threshold_choice["method"] + (" + BH FDR" if bh else ""),
                          "n_realisations": threshold_choice["n_realisations"]}
        if bh:
            m["threshold"]["bh_fdr"] = bh
    else:
        m["threshold"] = {"sigma": None, "false_alarms_per_field": None,
                          "method": "not enough null-test data", "bh_fdr": bh}
    m["null_test"] = null_block
    if inj:
        m["injection"] = {"recall_overall": inj["recall_overall"],
                          "ci95": inj["overall"]["ci95"], "n": inj["overall"]["n"],
                          "by_category": inj["by_category"],
                          "grid": [{k: g[k] for k in ("snr", "mu", "category", "recall", "n",
                                                      "n_recovered", "ci95")}
                                   for g in inj["grid"]],
                          "n_fields": inj.get("n_fields"),
                          "psf_model": inj.get("psf_model"),
                          "snr_definition": "PSF-matched single-epoch SNR of the injected flux change",
                          "match": f"kind-correct detection within "
                                   f"{config.EVAL_MATCH_TOLERANCE_PX} px"}
    else:
        m["injection"] = {"recall_overall": None, "grid": [], "n": 0}
    m["answer_key"] = ak
    # rule-based pipeline purity: real-pair detections vs expected null false alarms
    n_det = [r.get("n_detections") for r in field_results if r.get("n_detections") is not None]
    if n_det and null_block.get("false_alarms_per_field") is not None:
        mean_det = float(np.mean(n_det))
        fa = null_block["false_alarms_per_field"]
        m["pipeline_purity_estimate"] = {
            "detections_per_field": round(mean_det, 3), "false_alarms_per_field": fa,
            "purity": round(max(0.0, 1.0 - fa / mean_det), 4) if mean_det > 0 else None,
            "note": "1 - null false alarms / detections, at the sigma in use"}
    clf = classifier_block()
    if clf:
        m["classifier"] = clf
    m["fields"] = [{k: v for k, v in r.items()
                    if k not in ("null", "injection", "bh_maps", "answer_key_items")}
                   for r in field_results]
    for r, fr in zip(field_results, m["fields"]):
        if r.get("injection"):
            fr["injection_recall"] = r["injection"]["recall_overall"]
            fr["injection_n"] = r["injection"]["overall"]["n"]
    return m


def history_entry(metrics):
    clf = metrics.get("classifier") or {}
    return {"pipeline_version": metrics["pipeline"]["pipeline_version"],
            "config_hash": metrics["pipeline"]["config_hash"],
            "generated_utc": metrics["generated_utc"],
            "precision": clf.get("precision"), "recall": clf.get("recall"),
            "pr_auc": clf.get("pr_auc"),
            "injection_recall": (metrics.get("injection") or {}).get("recall_overall"),
            "false_alarms_per_field": (metrics.get("null_test") or {}).get("false_alarms_per_field"),
            "answer_key_recall": (metrics.get("answer_key") or {}).get("recall"),
            "quick": (metrics.get("options") or {}).get("quick")}


def write_metrics(metrics, out_dir, history=True):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "metrics.json")
    with open(path, "w") as f:
        json.dump(metrics, f, indent=1, default=_json_default)
    if history:
        hpath = os.path.join(out_dir, "metrics_history.json")
        try:
            with open(hpath) as f:
                hist = json.load(f)
        except Exception:
            hist = []
        hist.append(history_entry(metrics))
        with open(hpath, "w") as f:
            json.dump(hist, f, indent=1, default=_json_default)
    return path


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, tuple):
        return list(o)
    return str(o)


def threshold_from_results(field_results, target=None):
    nulls = [r["null"] for r in field_results if r.get("null")]
    peaks, weights, n = null_test.pooled_peaks(nulls)
    choice = threshold.choose_threshold(peaks, weights, n, target) if n else None
    bh = threshold.bh_summary([m for r in field_results for m in r.get("bh_maps") or []])
    return choice, bh, {"peaks": peaks.tolist(), "weights": weights.tolist(),
                        "n_realisations": n, "bh_fdr": bh}
