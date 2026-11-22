"""The snapshot API must never forward a request to itself.

A transient bucket-credential error used to switch auto mode to "api"; on the
snapshot service that URL is its own, so /api/targets and /api/snapshot-history
recursed until Cloud Run timed out (504), which the website showed as
"history unavailable".
"""
import unittest
from unittest.mock import patch

import app


class SnapshotModeTests(unittest.TestCase):
    def tearDown(self):
        app._SNAPSHOT_MODE["value"] = None
        app._SNAPSHOT_STORE = None

    def test_self_url_is_detected(self):
        with patch.dict("os.environ", {"K_SERVICE": "spherex-lens-api"}), \
                patch.object(app, "SNAPSHOT_API",
                             "https://spherex-lens-api-m6cqoypnxa-uc.a.run.app/api"):
            self.assertTrue(app._snapshot_api_is_self())

    def test_live_service_is_not_self(self):
        with patch.dict("os.environ", {"K_SERVICE": "spherex-lens-live"}), \
                patch.object(app, "SNAPSHOT_API",
                             "https://spherex-lens-api-m6cqoypnxa-uc.a.run.app/api"):
            self.assertFalse(app._snapshot_api_is_self())

    def test_self_url_forces_bucket_mode_even_when_auto(self):
        app._SNAPSHOT_MODE["value"] = None
        with patch.dict("os.environ", {"K_SERVICE": "spherex-lens-api"}), \
                patch.object(app, "SNAPSHOT_API",
                             "https://spherex-lens-api-m6cqoypnxa-uc.a.run.app/api"), \
                patch.object(app, "SNAPSHOT_SOURCE", "auto"), \
                patch.object(app, "SNAPSHOT_BUCKET", "test-bucket"):
            self.assertEqual(app._snapshot_mode(), "bucket")
            self.assertEqual(app._SNAPSHOT_MODE["value"], "bucket")


if __name__ == "__main__":
    unittest.main()
