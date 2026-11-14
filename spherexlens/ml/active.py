"""
spherexlens.ml.active  (Phase 5.10)
Active learning: what should people vote on next, and retraining from votes.

    uncertainty_queue(detections, n)   most-uncertain first (used lazily by
                                       /api/votes/queue)
    python -m spherexlens.ml.active retrain [--labels ml/data/labels.parquet]

Ranking: |P(real) - 0.5| ascending when detections carry ``ml.real_bogus``;
detections without a score fall back to |snr - DETECTION_SIGMA| ascending
(the ones nearest the cut are the least certain), after all scored ones.
"""
import argparse
import os
import sys

import config


def _score_key(d):
    p = (d.get("ml") or {}).get("real_bogus")
    if isinstance(p, (int, float)):
        return (0, abs(float(p) - 0.5))
    snr = d.get("snr")
    if isinstance(snr, (int, float)):
        return (1, abs(abs(float(snr)) - float(config.DETECTION_SIGMA)))
    return (2, 0.0)


def uncertainty_queue(detections, n=10):
    """The n detections most worth a human vote, most uncertain first."""
    dets = [d for d in (detections or []) if isinstance(d, dict)]
    return sorted(dets, key=_score_key)[:max(0, int(n))]


def retrain(labels_path=None, out_labels=None, votes=None, **train_kwargs):
    """Merge votes into the label table and retrain.  Returns the model card."""
    from spherexlens.ml import paths
    from spherexlens.ml.labels import merge_rows, read_labels, rows_from_parquet, vote_rows, write_labels
    from spherexlens.ml.train import model_card_md, train  # noqa: F401

    labels_path = labels_path or os.path.join(paths.data_dir(), "labels.parquet")
    base = rows_from_parquet(labels_path) if os.path.exists(labels_path) else []
    base = [r for r in base if r.get("source") != "vote"]     # votes are re-tallied
    rows = merge_rows(base, vote_rows(votes))
    if not rows:
        raise ValueError("no labels and no usable votes")
    out = out_labels or labels_path
    write_labels(rows, out)
    X, y, meta = read_labels(out)
    _, card = train(X, y, meta["field"], meta.get("source"), **train_kwargs)
    return card


def main(argv=None):
    ap = argparse.ArgumentParser(description="Active learning utilities")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("retrain", help="merge votes into labels and retrain")
    r.add_argument("--labels", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "retrain":
        try:
            card = retrain(args.labels)
        except ValueError as e:
            print(f"active: cannot retrain: {e}")
            return 1
        print(f"active: retrained {card['version']} on {card['data']['n']} labels "
              f"({card['data']['by_source']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
