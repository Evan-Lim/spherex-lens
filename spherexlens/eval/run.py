"""
spherexlens.eval.run  (Phase 4.5)

    python -m spherexlens.eval.run --cache DIR [--fields A B ...] [--quick]
                                   [--out eval] [--sigma 5.0]
                                   [--write-threshold] [--labels ml/data/labels.parquet]

For every field with >= 2 cutouts on disk: null test, strict injection-recovery
on the deepest epoch pair, answer-key recall; then the null-calibrated
threshold (and BH-FDR comparison), the classifier block from the latest model
(if any), and writes <out>/metrics.json (CONTRACT.md format) + appends to
<out>/metrics_history.json.  <out>/null_peaks.json keeps the pooled null
extrema for spherexlens.eval.threshold.

--write-threshold also writes <out>/threshold.json, which config.py loads as
DETECTION_SIGMA when <out> is the repository's eval/ directory.
--labels also writes the ML training table from the same runs.
--quick: few splits / groups / exposures and a small injection grid.
"""
import argparse
import json
import os
import sys
import time
import traceback

import config
from spherexlens.eval import harness, metrics, threshold


def main(argv=None):
    ap = argparse.ArgumentParser(description="SPHEREx-Lens evaluation")
    ap.add_argument("--cache", default=os.environ.get("SPHEREX_CACHE", "data_cache"))
    ap.add_argument("--fields", nargs="*")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=None, help="output dir (default: repo eval/)")
    ap.add_argument("--sigma", type=float, default=None,
                    help="detection sigma to evaluate at (default config.DETECTION_SIGMA)")
    ap.add_argument("--seed", type=int, default=config.ML_RANDOM_SEED)
    ap.add_argument("--write-threshold", action="store_true")
    ap.add_argument("--labels", default=None, help="also write ML labels parquet here")
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--normalisation", choices=("pipeline", "robust"), default=None,
                    help="S-map normalisation (default config.EVAL_S_NORMALISATION)")
    args = ap.parse_args(argv)
    if args.normalisation:
        config.EVAL_S_NORMALISATION = args.normalisation

    from spherexlens.ml import paths
    out_dir = args.out or paths.eval_dir()
    fields = args.fields or harness.list_fields(args.cache)
    if not fields:
        print(f"eval: no field in {args.cache} has >= 2 cutouts on disk")
        return 1
    opts = metrics.default_options(args.quick, args.seed)
    sink = None
    if args.labels:
        from spherexlens.ml.labels import LabelSink
        sink = LabelSink()

    results = []
    for t in fields:
        t0 = time.time()
        try:
            field = harness.load_field(args.cache, t)
            r = metrics.evaluate_field(field, opts, sink=sink, sigma=args.sigma)
        except Exception as e:
            traceback.print_exc()
            print(f"eval: {t} failed: {type(e).__name__}: {e}")
            continue
        results.append(r)
        nr = len(r["null"]["realisations"])
        inj = (r.get("injection") or {}).get("recall_overall")
        ak = r.get("answer_key") or {}
        print(f"eval: {t}: {r['n_exposures']} exposures, null realisations {nr} "
              f"({len(r['null']['skipped'])} groups skipped), detections "
              f"{r.get('n_detections')}, injection recall {inj}, answer key "
              f"{ak.get('n_detected')}/{ak.get('n')}  [{time.time() - t0:.0f}s]", flush=True)

    choice, bh, null_doc = metrics.threshold_from_results(results)
    m = metrics.build_metrics(results, opts, choice, bh)
    path = metrics.write_metrics(m, out_dir, history=not args.no_history)
    with open(os.path.join(out_dir, "null_peaks.json"), "w") as f:
        json.dump(null_doc, f)
    print(f"eval: metrics -> {path}")
    if choice:
        print(f"eval: null-calibrated sigma {choice['sigma']} "
              f"(expected {choice['false_alarms_per_field']} false alarms / field, "
              f"{choice['n_realisations']} null realisations)")
        if args.write_threshold:
            p = threshold.write_threshold(os.path.join(out_dir, "threshold.json"),
                                          threshold.threshold_record(choice, bh))
            print(f"eval: threshold -> {p}")
    else:
        print("eval: no null realisations -> threshold not chosen")
    if bh:
        print(f"eval: BH FDR q={bh['q']} would give sigma ~{bh['sigma_median']} "
              f"({bh['n_maps']} maps, {bh['n_rejected_total']} pixels rejected)")
    if sink is not None:
        from spherexlens.ml.labels import merge_rows, summary, vote_rows, write_labels
        rows = merge_rows(sink.rows, vote_rows())
        if rows:
            write_labels(rows, args.labels)
            print(f"eval: labels -> {args.labels}: {json.dumps(summary(rows))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
