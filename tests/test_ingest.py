"""Phase 2 / 3.1 / 3.4 / 3.5: archive adapters, coverage, links, Parquet store,
normalisation, Gaia RUWE, NEOSSat cleaning.  Offline: every archive reply is
a trimmed real response from tests/fixtures/archive/."""
import gzip
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

import config
from spherexlens.ingest.base import Adapter, ArchiveUnavailable, Coverage

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(HERE, "fixtures", "archive")
NEOSSAT_FRAME = os.path.join(HERE, "fixtures", "neossat_NEOS_SCI_2024251021442.fits.gz")
HD80606 = (140.65752, 50.60359)          # Gaia DR3, the frame's target


def fixture(name, mode="rb"):
    with open(os.path.join(ARCHIVE, name), mode) as f:
        return f.read()


class FakeSession:
    """requests.Session stand-in: routes by URL substring to recorded bodies."""

    def __init__(self, routes):
        self.routes = routes            # [(substring, bytes | Exception)]
        self.headers = {}
        self.calls = []

    def request(self, method, url, params=None, data=None, timeout=None):
        self.calls.append((method, url, params, data))
        for key, body in self.routes:
            if key in url:
                if isinstance(body, Exception):
                    raise body
                if callable(body):
                    body = body(params, data)
                return Mock(status_code=200, content=body, url=url,
                            text=body.decode("utf-8", "replace"))
        return Mock(status_code=404, content=b"", url=url, text="not routed")


def offline(adapter):
    adapter.min_interval_s = 0
    return adapter


class FastRetries(unittest.TestCase):
    def setUp(self):
        self._backoff = config.HTTP_BACKOFF_S
        config.HTTP_BACKOFF_S = 0

    def tearDown(self):
        config.HTTP_BACKOFF_S = self._backoff


# ---------------------------------------------------------------- SPHEREx
class SpherexTests(FastRetries):
    def test_sia_coverage_counts_images_and_epochs(self):
        from spherexlens.ingest.spherex import SpherexSIA
        s = FakeSession([("irsa.ipac.caltech.edu/SIA",
                          fixture("spherex_sia2_dr1_wise0855.xml"))])
        cov = offline(SpherexSIA(session=s, use_cache=False)).coverage(
            133.7596, -7.2421, 0.1)
        self.assertTrue(cov.available)
        self.assertEqual(cov.count, 6)
        self.assertIn("spherex_dr1", cov.detail)
        self.assertEqual(s.calls[0][2]["COLLECTION"], "spherex_dr1")

    def test_s3_key_mapping_and_listing(self):
        from spherexlens.ingest.spherex import (SpherexS3, parse_s3_listing,
                                                s3_key_for_url)
        url = ("https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/2025W19_1B/"
               "l2b-v27-2026-197/3/level2_2025W19_1B_0116_1D3_spx_l2b-v27-2026-197.fits")
        self.assertEqual(s3_key_for_url(url),
                         "dr1/level2/2025W19_1B/l2b-v27-2026-197/3/"
                         "level2_2025W19_1B_0116_1D3_spx_l2b-v27-2026-197.fits")
        objs, prefixes, token = parse_s3_listing(fixture("spherex_s3_list_level2.xml"))
        self.assertEqual(len(objs), 4)
        self.assertTrue(objs[0]["key"].endswith("_spx_l2b-v27-2026-197.fits"))
        self.assertGreater(objs[0]["size"], 60e6)
        self.assertTrue(token)

        pages = [fixture("spherex_s3_list_level2.xml"),
                 fixture("spherex_s3_list_periods.xml")]
        s = FakeSession([("s3.amazonaws.com", lambda p, d: pages.pop(0))])
        listing = offline(SpherexS3(session=s, use_cache=False)).list(
            "dr1/level2/", max_pages=2)
        self.assertEqual(len(listing["objects"]), 4)
        self.assertIn("dr1/level2/2025W17_4B/", listing["prefixes"])
        self.assertTrue(listing["truncated"])          # page cap reached
        self.assertIn("continuation-token", s.calls[1][2])


# ---------------------------------------------------------------- Gaia
def mast_session(designation_release=None):
    body = fixture("gaia_mast_dr3_wise0855.json")
    if designation_release:
        body = body.replace(b"Gaia DR3 ", designation_release.encode() + b" ")
    return FakeSession([("mast.stsci.edu", body)])


class GaiaTests(FastRetries):
    STEP1_KEYS = {"ra", "dec", "pm_ra", "pm_de", "gmag", "source_id", "parallax",
                  "parallax_error", "bpmag", "rpmag", "var_flag", "ruwe"}

    def irsa_rows(self):
        from spherexlens.ingest.gaia import parse_tsv
        return parse_tsv(fixture("gaia_irsa_tap_wise0855.tsv").decode())

    def test_irsa_is_step1_query(self):
        import step1_ingest
        from spherexlens.ingest.gaia import GaiaIRSA
        with patch.object(step1_ingest, "query_irsa_tap",
                          return_value=self.irsa_rows()) as q:
            res = GaiaIRSA().fetch(133.786245, -7.244372, 0.02)
        self.assertIn("gaia.gaia_dr3_source", q.call_args[0][0])
        self.assertTrue(q.call_args.kwargs["strict"])
        self.assertEqual(set(res.rows[0]), self.STEP1_KEYS)
        self.assertEqual(res.meta["mirror"], "irsa")

    def test_mast_rows_match_step1_format(self):
        from spherexlens.ingest.gaia import GaiaMAST
        res = offline(GaiaMAST(session=mast_session(), use_cache=False)).fetch(
            133.786245, -7.244372, 0.02)
        self.assertEqual(len(res.rows), 4)
        self.assertEqual(set(res.rows[0]), self.STEP1_KEYS)
        self.assertIsInstance(res.rows[0]["source_id"], str)
        self.assertEqual(res.rows, sorted(res.rows, key=lambda r: r["gmag"]))
        self.assertIsNotNone(res.rows[0]["ruwe"])

    def test_mast_wrong_release_is_refused(self):
        from spherexlens.ingest.gaia import GaiaMAST
        with self.assertRaises(ArchiveUnavailable):
            offline(GaiaMAST(session=mast_session("Gaia DR2"), use_cache=False)).fetch(
                133.786245, -7.244372, 0.02)

    def test_vizier_sentinel_rows_are_rejected(self):
        from spherexlens.ingest.gaia import GaiaVizieR
        s = FakeSession([("viz-bin", fixture("gaia_vizier_harvard_sentinel.xml"))])
        with self.assertRaises(ArchiveUnavailable) as cm:
            offline(GaiaVizieR(session=s, use_cache=False,
                               mirrors=("vizier.cfa.harvard.edu",))).fetch(
                133.786245, -7.244372, 0.05)
        self.assertIn("corrupt", str(cm.exception))

    def test_fallback_records_mirror(self):
        from spherexlens.ingest.gaia import (GaiaIRSA, GaiaMAST, GaiaVizieR,
                                             fetch_gaia_with_fallback)
        irsa = GaiaIRSA()
        irsa.fetch = Mock(side_effect=ArchiveUnavailable("IRSA down"))
        mast = offline(GaiaMAST(session=mast_session(), use_cache=False))
        vizier = GaiaVizieR()
        vizier.fetch = Mock()
        got = fetch_gaia_with_fallback(1, 2, 0.02, mirrors=[irsa, mast, vizier], log=None)
        self.assertEqual(got.mirror, "mast")
        self.assertEqual(got.state, "ok")
        self.assertEqual([a["mirror"] for a in got.attempts], ["irsa", "mast"])
        vizier.fetch.assert_not_called()

        for m in (mast, vizier):
            m.fetch = Mock(side_effect=ArchiveUnavailable("down"))
        got = fetch_gaia_with_fallback(1, 2, 0.02, mirrors=[irsa, mast, vizier], log=None)
        self.assertEqual((got.state, got.mirror, got.rows), ("unavailable", None, []))
        self.assertEqual(len(got.attempts), 3)

    def test_step1_falls_back_to_mast(self):
        import step1_ingest as ingest
        from spherexlens.ingest import gaia
        irsa = gaia.GaiaIRSA()
        mast = offline(gaia.GaiaMAST(session=mast_session(), use_cache=False))
        with patch.object(ingest, "query_irsa_tap_gaia",
                          side_effect=RuntimeError("IRSA TAP gaia query failed")), \
                patch.object(gaia, "mirror_adapters", return_value=[irsa, mast]), \
                patch.object(ingest, "query_irsa_tap_allwise", return_value=[]), \
                patch.object(ingest, "_query_vizier", return_value=([], "empty")):
            cats, status = ingest.download_catalogs_with_status(133.786245, -7.244372, 1.2)
        self.assertEqual(status["gaia"], "ok")
        self.assertEqual(status["gaia_mirror"], "mast")
        self.assertEqual(set(cats["gaia"][0]), self.STEP1_KEYS)

    def test_esa_variability_classes(self):
        from spherexlens.ingest.gaia import add_variability_classes
        s = FakeSession([("gea.esac.esa.int", fixture("gaia_esa_vari_classes.csv"))])
        rows = [{"source_id": "156430955257551360", "var_flag": "VARIABLE"},
                {"source_id": "5756955785394371328", "var_flag": "NOT_AVAILABLE"}]
        with patch.object(Adapter, "min_interval_s", 0):
            self.assertEqual(add_variability_classes(rows, session=s), 1)
        self.assertEqual(rows[0]["var_class"], "SOLAR_LIKE")
        self.assertNotIn("var_class", rows[1])

    def test_provisional_release_refused(self):
        from spherexlens.ingest.gaia import release_info
        with self.assertRaises(ArchiveUnavailable):
            release_info("DR4")


# ---------------------------------------------------------------- CADC
class CadcTests(FastRetries):
    def session(self):
        raw = gzip.decompress(open(NEOSSAT_FRAME, "rb").read())

        def tap(params, data):
            q = data["QUERY"]
            self.assertIn("obs_collection = 'NEOSSAT'", q)
            return fixture("cadc_neossat_count.tsv" if "COUNT(*)" in q
                           else "cadc_neossat_cone.tsv")
        return FakeSession([("argus/sync", tap),
                            ("caom2ops/datalink", fixture("cadc_neossat_datalink.xml")),
                            ("raven/files", raw)])

    def test_coverage_and_fetch(self):
        from spherexlens.ingest.cadc import CADCNeossat
        c = offline(CADCNeossat(session=self.session(), use_cache=False))
        cov = c.coverage(*HD80606, 0.05, t_start=60560.0, t_end=60561.0)
        self.assertEqual(cov.count, 358)
        res = c.fetch(*HD80606, 0.25, t_start=60560.0, t_end=60561.0)
        self.assertEqual(len(res.rows), 3)
        row = res.rows[0]
        self.assertEqual(row["obs_id"], "2024251021442")
        self.assertAlmostEqual(row["mjd"], 60560.0935, places=3)
        self.assertAlmostEqual(row["exposure_s"], 1.5007)
        self.assertIn("datalink", row["access_url"])

    def test_download_frame_returns_hdulist(self):
        from spherexlens.ingest.cadc import CADCNeossat
        c = offline(CADCNeossat(session=self.session(), use_cache=False))
        row = c.fetch(*HD80606, 0.25).rows[0]
        self.assertTrue(c.file_url(row).endswith("NEOS_SCI_2024251021442.fits"))
        hdul = c.download_frame(row)
        self.assertEqual(hdul[0].data.shape, (512, 856))
        tmp = tempfile.mkdtemp()
        try:
            path = c.save_frame(row, tmp)
            self.assertTrue(path.endswith("NEOS_SCI_2024251021442.fits"))
        finally:
            shutil.rmtree(tmp)


# ---------------------------------------------------------------- NEOSSat cleaning
class NeossatCleanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from astropy.io import fits
        import io
        cls.raw = gzip.decompress(open(NEOSSAT_FRAME, "rb").read())
        cls.fits = fits
        cls.io = io

    def hdul(self):
        return self.fits.open(self.io.BytesIO(self.raw))

    def test_clean_real_frame(self):
        from spherexlens.ingest import neossat_clean as nc
        image, mask, meta = nc.clean_frame(self.hdul())
        self.assertEqual(image.shape, (512, 512))
        self.assertTrue(meta["wcs_ok"])
        self.assertLess(meta["pointing_offset_arcmin"], 2.0)
        self.assertAlmostEqual(meta["pixscale_arcsec"], 3.0, delta=0.05)
        self.assertAlmostEqual(meta["mjd"], 60560.0935, places=3)
        self.assertLess(abs(np.median(image)), 300)        # overscan removed
        self.assertLess(mask.mean(), 0.01)
        self.assertIn("Open Government Licence", nc.ATTRIBUTION)

    def test_stamp_sees_the_target_and_not_blank_sky(self):
        from spherexlens.ingest import neossat_clean as nc
        image, mask, meta = nc.clean_frame(self.hdul())
        st = nc.stamp(image, meta["wcs"], *HD80606, 15, mask=mask)
        self.assertTrue(st["inside"])
        self.assertTrue(st["detected"])
        self.assertGreater(st["snr"], 50)
        self.assertEqual(np.array(st["cutout"]).shape, (15, 15))
        # the faintest 15x15 patch in the frame interior: nothing there
        from scipy.ndimage import uniform_filter
        sm = uniform_filter(np.where(mask, np.median(image), image), 15)[40:-40, 40:-40]
        y, x = np.unravel_index(np.argmin(sm), sm.shape)
        (ra, dec), = meta["wcs"].all_pix2world([[x + 40, y + 40]], 0)
        blank = nc.stamp(image, meta["wcs"], ra, dec, 15, mask=mask)
        self.assertTrue(blank["inside"])
        self.assertFalse(blank["detected"])
        off = nc.stamp(image, meta["wcs"], HD80606[0] + 2, HD80606[1], 15)
        self.assertFalse(off["inside"])
        self.assertFalse(off["detected"])

    def test_cosmic_ray_masked_and_dark_hot_pixel(self):
        from spherexlens.ingest import neossat_clean as nc
        h = self.hdul()
        data = h[0].data.astype(np.int32)
        data[100, 344 + 50] += 3000                 # one-pixel spike in the image
        h[0].data = data.astype(np.uint16)
        image, mask, meta = nc.clean_frame(h)
        self.assertTrue(mask[100, 50])
        self.assertGreaterEqual(meta["n_cosmic"], 1)
        dark = np.zeros((512, 512))
        dark[10, 10] = 500.0
        _, mask2, meta2 = nc.clean_frame(self.hdul(), master_dark=dark)
        self.assertTrue(mask2[10, 10])
        self.assertGreaterEqual(meta2["n_hot"], 1)

    def test_tutorial_overscan_fallback(self):
        from spherexlens.ingest import neossat_clean as nc
        data = np.full((64, 300), 1000.0)
        data[:, -50:] = 900.0                       # overscan strip
        h = self.fits.HDUList([self.fits.PrimaryHDU(data)])
        image, mask, meta = nc.clean_frame(h)
        self.assertEqual(image.shape, (64, 250))
        self.assertAlmostEqual(float(np.median(image)), 100.0)
        self.assertFalse(meta["wcs_ok"])
        self.assertIsNone(meta["wcs"])
        self.assertFalse(nc.stamp(image, None, 0, 0)["detected"])


# ---------------------------------------------------------------- links
class LinksTests(unittest.TestCase):
    def test_wiseview_keeps_parameter_set(self):
        from spherexlens.ingest.links import WISEVIEW_TEMPLATE, wiseview_url
        url = wiseview_url(10.5, -20.25, 120)
        keys = lambda u: [p.split("=")[0] for p in u.split("#")[1].split("&")]
        self.assertEqual(keys(url), keys(WISEVIEW_TEMPLATE))
        self.assertIn("#ra=10.500000&dec=-20.250000&size=120&band=2", url)
        self.assertTrue(url.endswith("&synth_b_mjd="))

    def test_enricher_adds_links(self):
        from spherexlens.ingest.links import add_links
        payload = {"target_position": {"ra": 133.78, "dec": -7.24},
                   "detections": [{"ra": 133.8, "dec": -7.2}, {"snr": 5}]}
        add_links(payload, {})
        self.assertEqual(set(payload["links"]),
                         {"wiseview", "irsa_finder", "simbad", "gaia_mast"})
        self.assertIn("ra=133.800000", payload["detections"][0]["links"]["wiseview"])
        self.assertNotIn("links", payload["detections"][1])
        self.assertIn("GaiaDR3", payload["links"]["gaia_mast"])


# ---------------------------------------------------------------- coverage
class FakeCov(Adapter):
    def __init__(self, name, result=None, delay=0.0):
        self.name, self.result, self.delay = name, result, delay
        self.calls = 0

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        self.calls += 1
        time.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return Coverage(self.name, True, self.result or 0, "fake")


class CoverageTests(unittest.TestCase):
    def setUp(self):
        from spherexlens.ingest import coverage
        coverage.clear_cache()

    def test_report_failure_slow_and_cache(self):
        from spherexlens.ingest.coverage import coverage_report
        ok, bad, slow = (FakeCov("spherex", 18), FakeCov("gaia", ArchiveUnavailable("down")),
                         FakeCov("neossat", 3, delay=1.0))
        t0 = time.time()
        rep = coverage_report(10.0, 20.0, 0.1, timeout_s=0.3, adapters=[ok, bad, slow])
        self.assertLess(time.time() - t0, 0.9)
        self.assertEqual([e["archive"] for e in rep], ["spherex", "gaia", "neossat"])
        self.assertEqual(rep[0]["count"], 18)
        self.assertFalse(rep[1]["available"])
        self.assertIn("down", rep[1]["error"])
        self.assertTrue(rep[2]["pending"])
        time.sleep(1.0)                          # the slow one lands in the cache
        rep2 = coverage_report(10.0, 20.0, 0.1, timeout_s=0.3, adapters=[ok, bad, slow])
        self.assertEqual(rep2[2]["count"], 3)
        self.assertTrue(rep2[0]["cached"])
        self.assertEqual((ok.calls, slow.calls), (1, 1))

    def test_enricher_switch_and_budget(self):
        from spherexlens.ingest import coverage
        payload = {"target_position": {"ra": 1.0, "dec": 2.0}}
        with patch.dict(os.environ, {"SPHEREX_COVERAGE_ENRICH": "false"}):
            coverage.add_coverage(payload, {})
        self.assertNotIn("coverage", payload)
        with patch.dict(os.environ, {"SPHEREX_COVERAGE_ENRICH": "true"}), \
                patch.object(coverage, "default_adapters",
                             return_value=[FakeCov("spherex", 2)]):
            coverage.add_coverage(payload, {})
        self.assertEqual(payload["coverage"][0]["count"], 2)

    def test_route_validates_and_answers(self):
        from fastapi.testclient import TestClient
        import app
        client = TestClient(app.app)
        self.assertEqual(client.get("/api/coverage?ra=400&dec=0").status_code, 422)
        self.assertEqual(client.get("/api/coverage?ra=10&dec=-91").status_code, 422)
        self.assertEqual(client.get("/api/coverage?ra=10&dec=0&radius=2").status_code, 422)
        with patch("spherexlens.ingest.coverage.default_adapters",
                   return_value=[FakeCov("spherex", 7)]):
            r = client.get("/api/coverage?ra=10&dec=0&radius=0.2")
        body = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["radius_deg"], 0.2)
        self.assertEqual(body["coverage"][0]["count"], 7)
        self.assertIn("config_hash", body["pipeline"])


# ---------------------------------------------------------------- store
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root)

    CATS = {"gaia": [{"ra": 1.0, "dec": 2.0, "gmag": 12.0, "source_id": "1",
                      "ruwe": None, "var_flag": "VARIABLE"},
                     {"ra": 1.1, "dec": 2.1, "gmag": 99.0, "source_id": "2",
                      "ruwe": 2.5, "var_flag": None}],
            "wise": [{"ra": 1.0, "dec": 2.0, "w1": 9.1, "pm_ra": None}],
            "vsx": []}

    def test_roundtrip_versions_and_dedupe(self):
        from spherexlens.ingest import store
        m1 = store.save_snapshot("T", self.CATS, {"gaia": {"mirror": "irsa",
                                                          "release": "DR3"}}, self.root)
        self.assertEqual(m1["version"], 1)
        self.assertEqual(m1["catalogs"]["gaia"]["mirror"], "irsa")
        self.assertEqual(m1["catalogs"]["gaia"]["rows"], 2)
        self.assertEqual(len(m1["catalogs"]["gaia"]["sha256"]), 64)
        cats, man = store.load_snapshot("T", root=self.root)
        self.assertEqual(cats, self.CATS)
        self.assertEqual(store.save_snapshot("T", self.CATS, root=self.root)["snapshot_id"],
                         m1["snapshot_id"])
        more = {**self.CATS, "vsx": [{"ra": 1, "dec": 2, "name": "X"}]}
        self.assertEqual(store.save_snapshot("T", more, root=self.root)["version"], 2)

    def test_meta_stamp_json_first_and_migration(self):
        from spherexlens.ingest import store
        import importlib.util
        rec = {"target": "T", "catalogs": self.CATS}
        store.snapshot_meta(rec, root=self.root)
        self.assertIn("catalog_snapshot_id", rec)
        self.assertIs(store.catalogs_for_meta(rec), rec["catalogs"])
        self.assertEqual(store.catalogs_for_meta(
            {"target": "T", "catalog_snapshot_id": rec["catalog_snapshot_id"]},
            root=self.root), self.CATS)

        cache = os.path.join(self.root, "cache")
        os.makedirs(cache)
        with open(os.path.join(cache, "F_meta.json"), "w") as f:
            json.dump({"target": "F", "catalogs": self.CATS}, f)
        spec = importlib.util.spec_from_file_location(
            "mig", os.path.join(HERE, "..", "scripts", "migrate_catalogs_to_parquet.py"))
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        log = []
        mig.migrate(cache, os.path.join(cache, "catalogs"), apply=False, log=log.append)
        self.assertNotIn("catalog_snapshot_id", json.load(open(os.path.join(cache, "F_meta.json"))))
        mig.migrate(cache, os.path.join(cache, "catalogs"), apply=True, log=log.append)
        meta = json.load(open(os.path.join(cache, "F_meta.json")))
        self.assertIn("catalog_snapshot_id", meta)
        self.assertEqual(meta["catalogs"], self.CATS)          # JSON kept


# ---------------------------------------------------------------- normalise / RUWE
class NormalizeTests(unittest.TestCase):
    def test_times(self):
        from spherexlens.ingest.normalize import to_mjd
        self.assertAlmostEqual(to_mjd("2024-09-07T02:14:42.053"), 60560.09354, places=5)
        self.assertAlmostEqual(to_mjd(2460560.59354228), 60560.09354228, places=6)
        self.assertAlmostEqual(to_mjd("2460560.59354228", "jd"), 60560.09354228, places=6)
        self.assertEqual(to_mjd(60560.5), 60560.5)
        self.assertAlmostEqual(to_mjd(2016.0, "year"), 57388.5)
        self.assertIsNone(to_mjd(None))
        from datetime import datetime
        self.assertAlmostEqual(to_mjd(datetime(2000, 1, 1, 12)), 51544.5)

    def test_fluxes(self):
        from catalog_tools import mag_to_mjy
        from spherexlens.ingest.normalize import flux_to_mjy
        self.assertAlmostEqual(flux_to_mjy(10.0, "vega_mag", "W1"), mag_to_mjy("W1", 10.0)[1])
        self.assertIsNone(flux_to_mjy(99.0, "vega_mag", "G"))
        self.assertAlmostEqual(flux_to_mjy(0.0, "ab_mag"), 3631e3)
        self.assertAlmostEqual(flux_to_mjy(2.0, "Jy"), 2000.0)
        self.assertAlmostEqual(flux_to_mjy(5.0, "uJy"), 0.005)
        self.assertAlmostEqual(flux_to_mjy(1.0, "MJy/sr", pix_sr=1e-9), 1.0)
        with self.assertRaises(ValueError):
            flux_to_mjy(1.0, "MJy/sr")
        with self.assertRaises(ValueError):
            flux_to_mjy(1.0, "furlong")

    def test_propagate_flags_high_ruwe(self):
        from spherexlens.ingest.normalize import catalog_epoch_mjd, propagate_to_epoch
        cats = {"gaia": [{"ra": 10.0, "dec": 0.0, "pm_ra": 1000.0, "pm_de": 0.0, "ruwe": 3.0},
                         {"ra": 11.0, "dec": 0.0, "pm_ra": 1000.0, "pm_de": 0.0, "ruwe": 1.0}],
                "vsx": [{"ra": 1.0, "dec": 1.0}]}
        out = propagate_to_epoch(cats, "2026-01-01T00:00:00")
        self.assertAlmostEqual(out["gaia"][0]["moved_arcsec"], 10.0, delta=0.05)
        self.assertEqual(out["gaia"][0]["propagation"], "approximate")
        self.assertNotIn("propagation", out["gaia"][1])
        self.assertEqual(out["vsx"], cats["vsx"])
        self.assertAlmostEqual(catalog_epoch_mjd("gaia"), 57388.5)


class RuweTests(unittest.TestCase):
    def test_gaia_reliable(self):
        from catalog_tools import gaia_reliable
        self.assertTrue(gaia_reliable({"ruwe": 1.0}))
        self.assertTrue(gaia_reliable({"ruwe": None}))
        self.assertTrue(gaia_reliable({}))
        self.assertFalse(gaia_reliable({"ruwe": 2.0}))

    def test_step4_keeps_match_and_adds_reason(self):
        from step4_triage import triage
        det = {"pos": (90, 90), "kind": "monopole", "snr": 9.0}
        star = {"ra": 50.0, "dec": 10.0, "gmag": 15.0, "pm_ra": 0.0, "pm_de": 0.0,
                "source_id": "42", "ruwe": 3.2}
        res = triage(det, 50.0, 10.0, 180, 3.0, {"gaia": [star]})
        self.assertIn("gaia", res["matches"])
        self.assertEqual(res["verdict"], "KNOWN")
        self.assertIn("Gaia astrometry unreliable (RUWE 3.2)", res["reasons"])
        self.assertFalse(res["gaia"]["astrometry_reliable"])
        ok = triage(det, 50.0, 10.0, 180, 3.0, {"gaia": [{**star, "ruwe": 1.1}]})
        self.assertFalse(any("RUWE" in r for r in ok["reasons"]))


if __name__ == "__main__":
    unittest.main()
