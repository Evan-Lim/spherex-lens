"""v18 real/bogus model (the default): NumPy-only serving, exact parity with training, tiers."""
import os
import unittest
from unittest import mock

import numpy as np

import config
from spherexlens.ml import imagefeat, paths, serve, v18

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "ml_v18_parity.npz")


def _bundle():
    import joblib
    return joblib.load(os.path.join(paths.model_dir(), config.ML_V18_FILE))


def _only_numpy(o):
    if isinstance(o, dict):
        return all(_only_numpy(v) for v in o.values())
    if isinstance(o, (list, tuple)):
        return all(_only_numpy(v) for v in o)
    return isinstance(o, (np.ndarray, str, int, float, bool, type(None)))


class TestV18(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = _bundle()
        cls.fx = np.load(FIX)

    def test_default_model_is_v18(self):
        """The default ("latest") follows LATEST.json, which must name v18."""
        import json
        latest = json.load(open(os.path.join(paths.model_dir(), "LATEST.json")))
        self.assertEqual(latest["version"], "rb-v18-2026.11.25")
        self.assertEqual(latest["model"], config.ML_V18_FILE)
        self.assertTrue(os.path.exists(os.path.join(paths.model_dir(), latest["json"])))
        for mode in ("latest", "v18"):
            with mock.patch.object(config, "ML_MODEL", mode):
                b = serve.load_model(force=True)
            self.assertEqual(b["kind"], "v18", mode)
            self.assertEqual(b["version"], "rb-v18-2026.11.25", mode)

    def test_v1_still_selectable(self):
        with mock.patch.object(config, "ML_MODEL", "v1"):
            b = serve.load_model(force=True)
        self.assertIsNotNone(b)
        self.assertNotEqual(b.get("kind"), "v18")
        self.assertEqual(b["version"], "rb-2026.11.13-1")

    def test_model_page_and_metrics_describe_v18(self):
        """/api/model and the metrics 'classifier' block read the card LATEST.json names."""
        from spherexlens.eval import metrics
        from spherexlens.routes import metrics as routes
        card = routes.get_model()
        self.assertEqual(card["version"], "rb-v18-2026.11.25")
        self.assertIn("rb-v18-2026.11.25", card.get("model_card_markdown", ""))
        block = metrics.classifier_block()
        self.assertEqual(block["model_version"], "rb-v18-2026.11.25")
        for k in ("precision", "recall", "pr_auc", "ci95", "confusion"):
            self.assertIn(k, block)

    def test_bundle_is_numpy_only(self):
        self.assertTrue(_only_numpy(self.b), "the website must not need LightGBM to serve v18")
        self.assertEqual(len(self.b["judges"]), 5)
        self.assertEqual(self.b["expert_order"], ["all", "move", "var"])
        self.assertEqual(len(self.b["feature_names"]), 61)

    def test_features_match_training_exactly(self):
        fx = self.fx; st = fx["stamps"]
        F = v18.block(fx["X"], imagefeat.derived(st), imagefeat.physics(st), imagefeat.physics2(st), fx["CF"], fx["COL"])
        np.testing.assert_array_equal(np.isnan(F), np.isnan(fx["F"]))
        self.assertEqual(float(np.nanmax(np.abs(F - fx["F"]))), 0.0)

    def test_scores_match_training_exactly(self):
        p, _ = v18.predict(self.b, self.fx["F"])
        self.assertLess(float(np.max(np.abs(p - self.fx["p"]))), 1e-6)

    def test_tiers(self):
        with mock.patch.object(config, "ML_V18_CANDIDATE", 0.5), mock.patch.object(config, "ML_V18_REVIEW", 0.2):
            self.assertEqual(v18.tier(0.9), "candidate")
            self.assertEqual(v18.tier(0.5), "candidate")
            self.assertEqual(v18.tier(0.3), "review")
            self.assertEqual(v18.tier(0.05), "low")
            self.assertIsNone(v18.tier(float("nan")))

    def test_unmeasurable_rows_get_nan(self):
        F = np.full((2, 61), np.nan, np.float32)
        p, _ = v18.predict(self.b, F)
        self.assertTrue(np.all(np.isnan(p)))

    def test_no_image_context_no_ml_block(self):
        dets = [{"detection_id": "x", "kind": "monopole", "snr": 12.0, "position": [10, 10]}]
        with mock.patch.object(config, "ML_MODEL", "v18"), mock.patch.object(config, "ML_FEATURE_CACHE", False):
            serve.score_detections(dets, {}, self.b)
        self.assertNotIn("ml", dets[0])
        self.assertIn("features", dets[0])

    def test_explain_returns_reasons(self):
        p, Xj = v18.predict(self.b, self.fx["F"][:3])
        r = v18.explain(self.b, Xj[0], float(p[0]), n=3)
        self.assertLessEqual(len(r), 3)
        for item in r:
            self.assertIn("feature", item); self.assertIn("effect", item)


if __name__ == "__main__":
    unittest.main()
