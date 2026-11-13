"""Phase 0: version stamp, config, enricher hook, router autoload, adapter base."""
import unittest
from unittest.mock import Mock

import config
import version
from spherexlens import enrich
from spherexlens.ingest.base import Adapter, ArchiveUnavailable


class VersionTests(unittest.TestCase):
    def test_stamp_has_version_and_hash(self):
        stamp = version.pipeline_stamp()
        self.assertEqual(stamp["pipeline_version"], version.PIPELINE_VERSION)
        self.assertEqual(len(stamp["config_hash"]), 12)

    def test_hash_changes_with_settings(self):
        before = version.config_hash()
        old = config.DETECTION_SIGMA
        try:
            config.DETECTION_SIGMA = old + 1
            self.assertNotEqual(before, version.config_hash())
        finally:
            config.DETECTION_SIGMA = old


class EnricherTests(unittest.TestCase):
    def test_failing_enricher_is_reported_not_raised(self):
        @enrich.register("_test_boom", order=999)
        def boom(payload, ctx):
            raise RuntimeError("archive down")

        @enrich.register("_test_ok", order=998)
        def ok(payload, ctx):
            payload["touched"] = True

        payload = {}
        enrich.run_enrichers(payload, {}, only={"_test_boom", "_test_ok"})
        self.assertTrue(payload["touched"])
        self.assertEqual(payload["enrichment"]["_test_boom"]["status"], "error")
        self.assertIn("archive down", payload["enrichment"]["_test_boom"]["error"])
        self.assertEqual(payload["enrichment"]["_test_ok"]["status"], "ok")


class AdapterTests(unittest.TestCase):
    def test_server_errors_become_archive_unavailable(self):
        session = Mock()
        session.headers = {}
        session.request.return_value = Mock(status_code=503, text="busy",
                                            content=b"")
        adapter = Adapter(session=session, use_cache=False)
        adapter.min_interval_s = 0
        old = config.HTTP_BACKOFF_S
        config.HTTP_BACKOFF_S = 0
        try:
            with self.assertRaises(ArchiveUnavailable):
                adapter.http("https://example.invalid/x")
        finally:
            config.HTTP_BACKOFF_S = old
        self.assertEqual(session.request.call_count, config.HTTP_RETRIES)

    def test_json_check(self):
        session = Mock()
        session.headers = {}
        session.request.return_value = Mock(status_code=200, content=b"<html>",
                                            url="u", text="<html>")
        adapter = Adapter(session=session, use_cache=False)
        adapter.min_interval_s = 0
        with self.assertRaises(ArchiveUnavailable):
            adapter.http_json("https://example.invalid/x")


class RouterTests(unittest.TestCase):
    def test_app_imports_with_routers(self):
        import app
        paths = {getattr(r, "path", None) for r in app.app.routes}
        self.assertIn("/api/dashboard", paths)


if __name__ == "__main__":
    unittest.main()
