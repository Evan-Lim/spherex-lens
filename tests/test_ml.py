"""Phase 5: features, labels, training/validation, serving, active learning, spectral guard."""
import json
import math
import os

import numpy as np
import pytest

import config
from spherexlens.ml import active, features, labels, serve, spectral, train
from spherexlens.ml.features import FEATURE_NAMES, extract_features


@pytest.fixture(autouse=True)
def _isolated_ml(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ML_MODEL_DIR", str(tmp_path / "ml" / "models"))
    monkeypatch.setattr(config, "ML_DATA_DIR", str(tmp_path / "ml" / "data"))
    monkeypatch.setattr(config, "ML_FEATURE_CACHE", False)
    serve._STATE.update(key=None, bundle=None, error=None)


def test_feature_names_are_fixed():
    assert FEATURE_NAMES[:3] == ("snr", "snr_minus", "fwhm_ratio")
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES)) == 22
    assert set(features.FEATURE_LABELS) == set(FEATURE_NAMES)


def test_features_tolerate_missing_ctx():
    f = extract_features({"kind": "monopole", "pos": (10, 10), "snr": 6.0}, {})
    assert list(f) == list(FEATURE_NAMES)
    assert f["snr"] == 6.0 and f["kind_monopole"] == 1.0 and math.isnan(f["psf_chi2"])
    assert all(v is None or isinstance(v, float) for v in features.to_json(f).values())
    f = extract_features({"kind": "something_new"}, None)
    assert f["kind_dipole"] == f["kind_monopole"] == f["kind_monopole_negative"] == 0.0


def test_features_from_maps():
    rng = np.random.default_rng(0)
    n = 60
    yy, xx = np.mgrid[0:n, 0:n]
    blob = lambda y, x: np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.5 ** 2))
    D = rng.normal(0, 0.1, (n, n)) + 5 * blob(30, 34) - 5 * blob(30, 26)
    ctx = {"D": D, "S": D * 10, "noise_maps": {1: np.ones((n, n)), 2: np.ones((n, n))},
           "noises": {1: 1.0, 2: 1.0}, "excluded": np.zeros((n, n), int),
           "epoch_order": [1, 2], "ra": 150.0, "dec": 2.0}
    det = {"kind": "dipole", "pos_plus": (30, 34), "pos_minus": (30, 26), "snr": 50.0,
           "epochs": (1, 2), "asteroid": {"status": "no_match"}}
    f = extract_features(det, ctx)
    assert f["dipole_sep_px"] == 8.0 and f["masked_frac"] == 0.0
    assert 0.5 < f["fwhm_ratio"] < 2.0 and f["ellipticity"] < 0.3
    assert f["dipole_symmetry"] < 0.1 and f["asteroid_match"] == 0.0
    assert f["noise_ratio"] == 1.0 and 0 <= f["abs_ecl_lat_deg"] <= 90


def _synthetic_labels(n_fields=8, per_field=60, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_fields):
        for j in range(per_field):
            y = int(j % 2 == 0)
            r = {"field": f"F{i}", "source": "injection" if y else "null_test", "label": y,
                 "category": None, "kind": "dipole", "snr": 0.0,
                 "detection_id": f"F{i}:{j}"}
            for n in FEATURE_NAMES:
                r[n] = float("nan")
            r["snr"] = r["snr"] = float(rng.normal(9 if y else 5.5, 1.5))
            r["psf_chi2"] = float(rng.normal(1.0 if y else 2.5, 0.6))
            r["masked_frac"] = float(rng.uniform(0, 0.2))
            rows.append(r)
    return rows


def test_labels_roundtrip(tmp_path):
    rows = _synthetic_labels(2, 10)
    p = labels.write_labels(rows, str(tmp_path / "l.parquet"))
    X, y, meta = labels.read_labels(p)
    assert X.shape == (20, len(FEATURE_NAMES)) and y.sum() == 10
    assert set(meta["field"]) == {"F0", "F1"}
    assert labels.summary(rows)["by_source"] == {"injection:real": 10, "null_test:bogus": 10}


def test_vote_rows_need_features_and_majority():
    cache = {"T:p:1,1": {"features": {"snr": 7.0}, "kind": "monopole"}}
    votes = [{"detection_id": "T:p:1,1", "target": "T", "vote": "real"},
             {"detection_id": "T:p:1,1", "target": "T", "vote": "real"},
             {"detection_id": "T:p:1,1", "target": "T", "vote": "bogus"},
             {"detection_id": "T:p:9,9", "target": "T", "vote": "real"}]
    rows = labels.vote_rows(votes, cache)
    assert len(rows) == 1 and rows[0]["label"] == 1 and rows[0]["source"] == "vote"


def test_train_groups_never_share_a_field(tmp_path):
    rows = _synthetic_labels()
    X = np.array([[r[n] for n in FEATURE_NAMES] for r in rows])
    y = np.array([r["label"] for r in rows])
    fields = [r["field"] for r in rows]
    bundle, card = train.train(X, y, fields, [r["source"] for r in rows], seed=1,
                               card_path=str(tmp_path / "MODEL_CARD.md"), n_boot=50)
    assert set(card["train_fields"]).isdisjoint(card["heldout_fields"])
    assert card["heldout_fields"]
    for fold in card["folds"]:
        assert set(fold["train"]).isdisjoint(fold["test"])
        assert set(fold["test"]).isdisjoint(card["heldout_fields"])
    p = train.predict_proba(bundle, X)
    assert np.all((p >= 0) & (p <= 1))
    h = card["metrics"]["heldout"]
    assert h["pr_auc"] > 0.8 and set(h["confusion"]) == {"labels", "matrix"}
    latest = json.load(open(os.path.join(config.ML_MODEL_DIR, "LATEST.json")))
    assert latest["version"].startswith("rb-") and os.path.exists(
        os.path.join(config.ML_MODEL_DIR, latest["model"]))
    assert "Intended use" in (tmp_path / "MODEL_CARD.md").read_text()
    # versions increment on the same day
    assert train.next_version(config.ML_MODEL_DIR).endswith("-2")


def test_train_refuses_single_field():
    rows = _synthetic_labels(1, 20)
    X = np.array([[r[n] for n in FEATURE_NAMES] for r in rows])
    with pytest.raises(ValueError):
        train.train(X, [r["label"] for r in rows], [r["field"] for r in rows], write=False)


def test_serve_without_model_gives_features_only():
    payload = {"detections": [{"kind": "monopole", "position": [5, 5], "snr": 6.0}],
               "pipeline": {"model_version": None}}
    serve.ml_enricher(payload, {})
    assert payload["ml_status"]["status"] == "no_model"
    d = payload["detections"][0]
    assert d["features"]["snr"] == 6.0 and "ml" not in d
    assert payload["pipeline"]["model_version"] is None


def test_serve_with_model_writes_ml_blocks(tmp_path):
    rows = _synthetic_labels()
    X = np.array([[r[n] for n in FEATURE_NAMES] for r in rows])
    train.train(X, [r["label"] for r in rows], [r["field"] for r in rows],
                card_path=str(tmp_path / "card.md"), n_boot=20)
    payload = {"detections": [{"kind": "dipole", "position": [5, 5], "snr": 12.0},
                              {"kind": "monopole", "position": [9, 9], "snr": 5.1,
                               "verdict": "CANDIDATE", "category": "CANDIDATE"}],
               "pipeline": {}}
    serve.ml_enricher(payload, {})
    assert payload["ml_status"]["status"] == "ok"
    assert payload["pipeline"]["model_version"].startswith("rb-")
    for d in payload["detections"]:
        ml = d["ml"]
        assert 0 <= ml["real_bogus"] <= 1 and isinstance(ml["uncertain"], bool)
        assert len(ml["reasons"]) <= config.ML_N_REASONS
        for r in ml["reasons"]:
            assert {"feature", "label", "effect"} == set(r)
    assert payload["detections"][1]["verdict"] == "CANDIDATE"     # ML never changes it
    assert payload["detections"][0]["ml"]["real_bogus"] > payload["detections"][1]["ml"]["real_bogus"]


def test_enricher_is_registered():
    from spherexlens import enrich
    assert "ml" in enrich.registered()


def test_uncertainty_queue_order():
    dets = [{"id": "a", "ml": {"real_bogus": 0.95}}, {"id": "b", "ml": {"real_bogus": 0.52}},
            {"id": "c", "snr": config.DETECTION_SIGMA + 0.1}, {"id": "d", "ml": {"real_bogus": 0.3}}]
    assert [d["id"] for d in active.uncertainty_queue(dets, 3)] == ["b", "d", "a"]
    assert [d["id"] for d in active.uncertainty_queue(dets[2:3], 5)] == ["c"]


def test_spectral_minimum_sample_guard():
    rows = [{"field": "F", "label": "star", **{f: 0.1 for f in spectral.SPEC_FEATURES}}] * 5
    bundle, card = spectral.train_rows(rows)
    assert bundle is None and card["status"] == "insufficient_data"
    assert spectral.label_object({"parallax": 50, "parallax_error": 1}, {"w1": 10, "w2": 10.1,
                                                                        "ext_flg": "0"}) == "star"
    assert spectral.label_object(None, {"w1": 14, "w2": 12, "ext_flg": "0"}) == "brown_dwarf"
