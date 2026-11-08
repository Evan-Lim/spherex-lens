import asyncio
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import app
import live_ingest
import step1_ingest
import step6_injection
from app import _normalize_data_path, _resolve_epochs, epoch_coverage_error
from live_ingest import _limit_slots


class EpochCoverageTests(unittest.TestCase):
    def test_explicit_single_epoch_is_not_inferred_as_two(self):
        epochs = _resolve_epochs([1, 1, 1], 3)

        self.assertEqual(epochs, [1, 1, 1])
        self.assertIn("only 1 observing epoch", epoch_coverage_error(
            epochs, ["SPHEREx-D1", "SPHEREx-D2", "SPHEREx-D3"]))

    def test_different_bands_cannot_be_compared(self):
        error = epoch_coverage_error(
            [1, 2], ["SPHEREx-D1", "SPHEREx-D2"])

        self.assertIn("no detector band", error)
        self.assertIn("colour", error)

    def test_shared_band_across_epochs_is_comparable(self):
        self.assertIsNone(epoch_coverage_error(
            [1, 1, 2, 2],
            ["SPHEREx-D1", "SPHEREx-D2", "SPHEREx-D1", "SPHEREx-D3"]))

    def test_legacy_metadata_without_epochs_uses_fallback(self):
        self.assertEqual(_resolve_epochs(None, 4), [1, 1, 2, 2])

    def test_windows_cache_paths_normalize_for_current_platform(self):
        self.assertEqual(
            _normalize_data_path(r"data_cache\image.fits"),
            os.path.join("data_cache", "image.fits"))

    def test_live_slot_limit_prioritizes_shared_bands_across_epochs(self):
        slots = [
            (epoch, [{"energy_bandpassname": band}])
            for epoch in (1, 2)
            for band in ("SPHEREx-D1", "SPHEREx-D2", "SPHEREx-D3")
        ]

        selected = _limit_slots(slots, 4)

        self.assertEqual(
            [(epoch, candidates[0]["energy_bandpassname"])
             for epoch, candidates in selected],
            [(1, "SPHEREx-D1"), (2, "SPHEREx-D1"),
             (1, "SPHEREx-D2"), (2, "SPHEREx-D2")])

    def test_live_slot_limit_keeps_both_epochs_without_shared_bands(self):
        slots = [
            (1, [{"energy_bandpassname": "SPHEREx-D1"}]),
            (2, [{"energy_bandpassname": "SPHEREx-D2"}]),
        ]

        selected = _limit_slots(slots, 2)

        self.assertEqual([epoch for epoch, _ in selected], [1, 2])

    def test_live_exposures_fetches_again_instead_of_reusing_ram(self):
        cutouts = [
            {"epoch": 1, "band": "SPHEREx-D1", "data": b"first"},
            {"epoch": 2, "band": "SPHEREx-D1", "data": b"last"},
        ]
        meta = {"meta": {"ra": 1.0, "dec": 2.0}}
        with patch.object(app, "live_cutouts", return_value=cutouts) as fetch, \
                patch.object(app, "query_sia2", return_value=[{"obs_id": "x"}]) as listing:
            first = app.live_exposures("target", meta)
            second = app.live_exposures("target", meta)

        self.assertEqual(fetch.call_count, 2)
        # Every request asks IRSA for a fresh listing and hands it to the
        # cutout fetch instead of letting it query a second time.
        self.assertEqual(listing.call_count, 2)
        self.assertTrue(all(call.kwargs["use_cache"] is False
                            for call in listing.call_args_list))
        self.assertEqual(fetch.call_args.kwargs["rows"], [{"obs_id": "x"}])
        self.assertFalse(first[2])
        self.assertFalse(second[2])

    def test_live_dashboard_does_not_reuse_custom_dashboard_cache(self):
        meta = {"custom": True, "meta": {"ra": 1.0, "dec": 2.0}}
        with patch.object(app, "load_target", return_value=meta), \
                patch.dict(app.FIELD_DASH_CACHE, {"custom": {"cached": True}}), \
                patch.object(app, "live_exposures",
                             side_effect=app.CoverageError("fresh fetch")) as fetch:
            response = app.get_dashboard(target="custom", source="live")

        fetch.assert_called_once()
        self.assertEqual(response.status_code, 422)

    def test_live_progress_stream_forwards_events_and_result(self):
        def fake_dashboard(**kwargs):
            app._emit_progress("irsa_query", "IRSA query completed", rows=3)
            return {"target": kwargs["target"], "source": "live"}

        async def collect_events():
            response = app._progress_stream(
                lambda: fake_dashboard(target="WISE_0855"),
                "Starting test request",
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
            return "".join(chunks)

        response_text = asyncio.run(collect_events())

        assert "event: progress" in response_text
        assert "IRSA query completed" in response_text
        assert "event: result" in response_text
        assert '"source":"live"' in response_text

    def test_recovery_stream_forwards_requested_source(self):
        def fake_recovery(**kwargs):
            app._emit_progress("recovery", "Recovery grid 1/24 cells",
                               completed=1, total=24)
            return {"target": kwargs["target"], "source": kwargs["source"]}

        async def collect_events():
            # The patch must remain active while the body is iterated because
            # the lambda inside stream_recovery calls get_recovery lazily from
            # a worker thread.
            with patch.object(app, "get_recovery", side_effect=fake_recovery):
                response = await app.stream_recovery(
                    target="WISE_0855", source="cache")
                chunks = []
                async for chunk in response.body_iterator:
                    chunks.append(
                        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
            return "".join(chunks)

        response_text = asyncio.run(collect_events())

        assert "cached-cutout recovery" in response_text
        assert "Recovery grid 1/24 cells" in response_text
        assert '"source":"cache"' in response_text

    def test_injection_recovery_reports_completed_cells(self):
        def fake_zogy(img1, img2, noise1, noise2, psf1, psf2):
            return np.zeros_like(img1), np.zeros_like(img1)

        events = []
        image = np.zeros((80, 80))
        with patch.object(step6_injection, "zogy", side_effect=fake_zogy):
            result = step6_injection.run_injection_recovery(
                image, image, 1.0, 1.0, 1.4,
                snr_grid=[3, 5], mu_grid=[1, 2], n_trials=2,
                progress=events.append,
            )

        assert len(result) == 4
        assert [event["completed"] for event in events] == [1, 2, 3, 4]
        assert events[-1]["total"] == 4
        assert events[-1]["trials"] == 2

    def test_live_mosaic_is_explicitly_unsupported_instead_of_returning_cache(self):
        response = app.get_mosaic(source="live")

        self.assertEqual(response.status_code, 501)
        self.assertIn(b"only serves cache-derived tiles", response.body)

    def test_fresh_sia2_query_bypasses_and_does_not_replace_disk_cache(self):
        votable = (
            b'<VOTABLE xmlns="http://www.ivoa.net/xml/VOTable/v1.3">'
            b'<RESOURCE><TABLE><FIELD name="obs_id"/>'
            b'<DATA><TABLEDATA/></DATA></TABLE></RESOURCE></VOTABLE>')
        response = Mock(status_code=200, content=votable)
        with tempfile.TemporaryDirectory() as cache_dir:
            cache_path = os.path.join(
                cache_dir, "sia2_spherex_qr2_1.0000_2.0000_0.1.xml")
            with open(cache_path, "wb") as cache_file:
                cache_file.write(b"stale local listing")
            with patch.object(step1_ingest, "CACHE", cache_dir), \
                    patch.object(step1_ingest.requests, "get",
                                 return_value=response) as request:
                rows = step1_ingest.query_sia2(
                    1.0, 2.0, collection="spherex_qr2", use_cache=False)

            self.assertEqual(rows, [])
            request.assert_called_once()
            self.assertEqual(
                request.call_args.kwargs["headers"]["Cache-Control"],
                "no-cache")
            with open(cache_path, "rb") as cache_file:
                self.assertEqual(cache_file.read(), b"stale local listing")

    def test_live_cutouts_requests_fresh_sia2_listing(self):
        with patch.object(live_ingest, "query_sia2", return_value=[]) as query:
            cutouts = live_ingest.live_cutouts(
                1.0, 2.0, bands=("SPHEREx-D1",), log=lambda _message: None)

        self.assertEqual(cutouts, [])
        self.assertFalse(query.call_args.kwargs["use_cache"])


if __name__ == "__main__":
    unittest.main()
