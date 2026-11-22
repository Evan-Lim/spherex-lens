"""
Metrics regression gate (Phase 4.6).

    python -m spherexlens.eval.gate [--metrics eval/metrics.json]
                                    [--baseline eval/baseline_metrics.json]

Fails (exit 1) when precision, recall or PR-AUC fall more than TOLERANCE below
the committed baseline, or the false-alarm rate rises more than TOLERANCE
above it.  Exits 0 with a note when either file is missing, so a clone without
real data still passes CI.
"""
import argparse
import json
import math
import os
import sys

TOLERANCE = 0.02

# (json path, higher_is_better)
CHECKS = (
    (("classifier", "precision"), True),
    (("classifier", "recall"), True),
    (("classifier", "pr_auc"), True),
    (("injection", "recall_overall"), True),
    (("null_test", "false_alarms_per_field"), False),
)


def _get(d, path):
    """A finite, non-boolean number at ``path``, else None."""
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return None
        d = d[key]
    if isinstance(d, bool) or not isinstance(d, (int, float)) or not math.isfinite(d):
        return None
    return d


def compare(metrics, baseline, tolerance=TOLERANCE):
    failures = []
    for path, higher in CHECKS:
        new, old = _get(metrics, path), _get(baseline, path)
        if old is None:
            continue                    # not baselined yet: nothing to protect
        if new is None:
            # a baselined metric that vanished, became null/NaN or a bool is a
            # regression, not a pass
            failures.append(f"{'.'.join(path)}: {old:.3f} -> missing/non-finite")
            continue
        worse = (old - new) if higher else (new - old)
        if worse > tolerance:
            failures.append(f"{'.'.join(path)}: {old:.3f} -> {new:.3f}")
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", default="eval/metrics.json")
    ap.add_argument("--baseline", default="eval/baseline_metrics.json")
    args = ap.parse_args(argv)
    if not (os.path.exists(args.metrics) and os.path.exists(args.baseline)):
        print("metrics gate: no metrics/baseline yet -- skipped")
        return 0
    with open(args.metrics) as f:
        metrics = json.load(f)
    with open(args.baseline) as f:
        baseline = json.load(f)
    failures = compare(metrics, baseline)
    for line in failures:
        print("REGRESSION", line)
    print("metrics gate:", "FAIL" if failures else "ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
