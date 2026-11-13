"""
spherexlens.ml.labels  (Phase 5.2)
Build the real/bogus training table.

    python -m spherexlens.ml.labels --cache data_cache [--fields A B] [--quick]
                                    [--out ml/data/labels.parquet]

Every row = one detection's feature vector (features.FEATURE_NAMES) plus
    field   -- the field it came from (the GroupKFold group)
    source  -- why we believe the label:
        injection   real  -- an injected source the strict test recovered
                             (features of the matched detection)
        answer_key  real  -- a catalogued high-PM star / VSX variable / known
                             asteroid that was detected
        vote        real|bogus -- public votes (spherexlens.votes), when present
        null_test   bogus -- any detection in a null (half-vs-half) subtraction
        mask        bogus -- real-pair detections on masked / edge pixels
    label   -- 1 real, 0 bogus
    category, kind, snr, detection_id

Known biases (also in the model card): injected sources are idealised point
sources on the measured PSF; null detections are dominated by the field's
noise and colour systematics, not by e.g. ghosts that repeat between epochs;
"mask" labels are defined from masked_frac, which is itself a feature.

The eval run (spherexlens.eval.run --labels ...) builds this table as a side
effect of the same null / injection / answer-key runs.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

import config
from spherexlens.ml import paths
from spherexlens.ml.features import FEATURE_NAMES, extract_features

META_COLUMNS = ("field", "source", "label", "category", "kind", "snr", "detection_id")


def row(det, ctx, field, source, label, category=None, features=None):
    f = features if features is not None else extract_features(det, ctx)
    p = det.get("position") or det.get("pos_plus") or det.get("pos") or (None, None)
    r = {"field": str(field), "source": source, "label": int(label),
         "category": category, "kind": det.get("kind"),
         "snr": float(abs(det.get("snr") or 0.0)),
         "detection_id": det.get("detection_id") or
         f"{field}:{det.get('pair', '')}:{p[0]},{p[1]}:{source}"}
    for n in FEATURE_NAMES:
        v = f.get(n)
        r[n] = float(v) if v is not None else float("nan")
    return r


class LabelSink:
    """Collects label rows from an eval run (see eval.metrics.evaluate_field)."""

    def __init__(self, max_per_source_per_field=400):
        self.rows = []
        self.cap = max_per_source_per_field
        self._count = {}

    def add(self, det, ctx, field, source, label, category=None):
        k = (field, source)
        if self._count.get(k, 0) >= self.cap:
            return
        try:
            self.rows.append(row(det, ctx, field, source, label, category))
            self._count[k] = self._count.get(k, 0) + 1
        except Exception as e:           # a bad detection never stops the run
            print(f"labels: skipped a {source} detection ({type(e).__name__}: {e})")


# ---------------------------------------------------------------------------
# votes
# ---------------------------------------------------------------------------
def load_feature_cache(path=None):
    """{detection_id: {"features", "target"}} from the serve-time cache."""
    path = path or paths.feature_cache_path()
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            try:
                rec = json.loads(line)
                out[rec["detection_id"]] = rec
            except Exception:
                continue
    return out


def vote_rows(votes=None, feature_cache=None):
    """Rows for real/bogus votes whose detection's features we have seen."""
    if votes is None:
        try:
            from spherexlens.votes import load_votes
            votes = load_votes()
        except Exception:
            votes = []
    if not votes:
        return []
    cache = feature_cache if feature_cache is not None else load_feature_cache()
    tally = {}
    for v in votes:
        if v.get("vote") not in ("real", "bogus"):
            continue
        t = tally.setdefault(v.get("detection_id"), [0, 0, v.get("target")])
        t[0 if v["vote"] == "real" else 1] += 1
    rows = []
    for det_id, (n_real, n_bogus, target) in tally.items():
        rec = cache.get(det_id)
        if rec is None or n_real == n_bogus:
            continue                       # no features, or a tie: no label
        feats = {k: (float("nan") if v is None else v)
                 for k, v in (rec.get("features") or {}).items()}
        field = target or (det_id.split(":")[0] if det_id else "unknown")
        r = {"field": field, "source": "vote", "label": int(n_real > n_bogus),
             "category": f"votes {n_real}:{n_bogus}", "kind": rec.get("kind"),
             "snr": float(feats.get("snr") or float("nan")), "detection_id": det_id}
        for n in FEATURE_NAMES:
            r[n] = float(feats.get(n, float("nan")))
        rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# parquet I/O (pyarrow only; pandas is not a dependency)
# ---------------------------------------------------------------------------
def write_labels(rows, path=None):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = path or os.path.join(paths.data_dir(), "labels.parquet")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    cols = list(META_COLUMNS) + list(FEATURE_NAMES)
    data = {c: [r.get(c) for r in rows] for c in cols}
    for c in ("snr",) + tuple(FEATURE_NAMES):
        data[c] = pa.array([float("nan") if v is None else float(v) for v in data[c]],
                           type=pa.float64())
    data["label"] = pa.array([int(v) for v in data["label"]], type=pa.int8())
    for c in ("field", "source", "category", "kind", "detection_id"):
        data[c] = pa.array([None if v is None else str(v) for v in data[c]], type=pa.string())
    table = pa.table(data)
    table = table.replace_schema_metadata({
        "feature_names": json.dumps(list(FEATURE_NAMES)),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    pq.write_table(table, path)
    return path


def read_labels(path):
    """-> (X float array [n, n_features], y int array, meta dict of column lists)."""
    import pyarrow.parquet as pq

    t = pq.read_table(path)
    names = t.column_names
    X = np.column_stack([np.asarray(t.column(n).to_numpy(zero_copy_only=False), dtype=float)
                         if n in names else np.full(t.num_rows, np.nan)
                         for n in FEATURE_NAMES]) if t.num_rows else np.zeros((0, len(FEATURE_NAMES)))
    y = np.asarray(t.column("label").to_numpy(), dtype=int)
    meta = {c: t.column(c).to_pylist() for c in META_COLUMNS if c in names}
    return X, y, meta


def merge_rows(*row_lists):
    """Concatenate, dropping duplicate (detection_id, source) -- later wins."""
    seen = {}
    for rows in row_lists:
        for r in rows:
            seen[(r.get("detection_id"), r.get("source"))] = r
    return list(seen.values())


def rows_from_parquet(path):
    import pyarrow.parquet as pq
    return pq.read_table(path).to_pylist()


def summary(rows):
    out = {"n": len(rows), "n_real": sum(1 for r in rows if r["label"] == 1),
           "fields": sorted({r["field"] for r in rows})}
    by = {}
    for r in rows:
        k = f"{r['source']}:{'real' if r['label'] else 'bogus'}"
        by[k] = by.get(k, 0) + 1
    out["by_source"] = by
    out["n_bogus"] = out["n"] - out["n_real"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build ml/data/labels.parquet")
    ap.add_argument("--cache", default=os.environ.get("SPHEREX_CACHE", "data_cache"))
    ap.add_argument("--fields", nargs="*")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=os.path.join(paths.data_dir(), "labels.parquet"))
    ap.add_argument("--seed", type=int, default=config.ML_RANDOM_SEED)
    args = ap.parse_args(argv)
    from spherexlens.eval import harness, metrics

    sink = LabelSink()
    fields = args.fields or harness.list_fields(args.cache)
    opts = metrics.default_options(args.quick, args.seed)
    for t in fields:
        print(f"labels: {t}", flush=True)
        field = harness.load_field(args.cache, t)
        metrics.evaluate_field(field, opts, sink=sink)
    rows = merge_rows(sink.rows, vote_rows())
    if not rows:
        print("labels: no rows (no usable fields?)")
        return 1
    write_labels(rows, args.out)
    print(json.dumps(summary(rows), indent=1))
    print(f"labels -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
