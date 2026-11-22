"""Phase 7: spectrum-normalised deep-field light curves, periods, motion."""
import unittest

import numpy as np

from spherexlens.deep import lightcurve, select


def simulate(n=300, period=None, amp=0.0, slope=-1.5, pm_mas=0.0, seed=1,
             noise=0.01, correlated=False):
    rng = np.random.default_rng(seed)
    t = np.sort(rng.uniform(60790, 61170, n))
    if correlated:            # LVF scan: wavelength drifts with time
        lam = 0.75 + 4.25 * ((t - t.min()) / 47.0 % 1.0)
    else:
        lam = rng.uniform(0.75, 5.0, n)
    spectrum = 50.0 * (lam / 1.0) ** slope + 5.0 * np.exp(-((lam - 3.3) / 0.1) ** 2)
    r = 1.0 + (amp * np.sin(2 * np.pi * t / period) if period else 0.0)
    err = noise * spectrum
    f = spectrum * r + rng.normal(0, err)
    ra0, dec0 = 270.0, 66.56
    yrs = (t - t.mean()) / 365.25
    pts = []
    for i in range(n):
        pts.append({
            "mjd": float(t[i]), "wavelength_um": float(lam[i]),
            "flux_mjy": float(f[i]), "err_mjy": float(err[i]), "flagged": False,
            "centroid_ra": ra0 + pm_mas * yrs[i] / 3.6e6 / np.cos(np.radians(dec0))
                           + rng.normal(0, 200) / 3.6e6,
            "centroid_dec": dec0 + rng.normal(0, 200) / 3.6e6,
        })
    return pts


class LightCurveTests(unittest.TestCase):
    def test_constant_star_with_steep_spectrum_is_not_variable(self):
        # The raw fluxes vary by >10x because each visit sees another colour.
        s = lightcurve.analyse_source(simulate())
        self.assertEqual(s["status"], "ok")
        self.assertFalse(s["variable"], s)
        self.assertLess(s["amplitude"], 0.03)

    def test_pulsating_star_is_variable_with_correct_period(self):
        s = lightcurve.analyse_source(simulate(period=37.0, amp=0.25))
        self.assertTrue(s["variable"])
        self.assertTrue(s["period"]["significant"])
        self.assertAlmostEqual(s["period"]["period_days"], 37.0, delta=1.5)

    def test_wavelength_time_correlation_does_not_fake_variability(self):
        s = lightcurve.analyse_source(simulate(correlated=True, seed=4))
        self.assertFalse(s["variable"], s["chi2_dof"])

    def test_motion_fit_recovers_proper_motion(self):
        s = lightcurve.analyse_source(simulate(pm_mas=3000.0, seed=3))
        self.assertAlmostEqual(s["motion"]["pm_ra"], 3000.0, delta=300)
        self.assertGreater(s["motion"]["significance"], 5)

    def test_flagged_and_too_few_points(self):
        pts = simulate(n=10)
        self.assertEqual(lightcurve.analyse_source(pts)["status"], "too_few_points")
        pts = simulate(n=40)
        for p in pts:
            p["flagged"] = True
        self.assertEqual(lightcurve.analyse_source(pts)["status"], "too_few_points")


class SelectionTests(unittest.TestCase):
    def test_stratified_sample_spans_time_and_bands(self):
        rows = [{"t_min": str(60790 + i * 0.5),
                 "energy_bandpassname": f"SPHEREx-D{1 + i % 6}"}
                for i in range(2000)]
        chosen = select.select_rows(rows, max_exposures=120)
        self.assertLessEqual(len(chosen), 120)
        self.assertGreater(len(chosen), 100)
        t = [float(r["t_min"]) for r in chosen]
        self.assertLess(min(t), 60790 + 60)
        self.assertGreater(max(t), 60790 + 940)
        self.assertEqual(len({r["energy_bandpassname"] for r in chosen}), 6)


class RouteTests(unittest.TestCase):
    def test_deep_routes(self):
        import os
        import tempfile
        import json
        from fastapi.testclient import TestClient
        import app
        with tempfile.TemporaryDirectory() as d:
            os.environ["SPHEREX_DEEP_DIR"] = d
            try:
                with open(os.path.join(d, "NEP.json"), "w") as f:
                    json.dump({"name": "NEP", "ra": 270, "dec": 66.56,
                               "sources": []}, f)
                c = TestClient(app.app)
                self.assertEqual(c.get("/api/deep").json()["fields"][0]["name"], "NEP")
                self.assertEqual(c.get("/api/deep/NEP").status_code, 200)
                self.assertEqual(c.get("/api/deep/nope").status_code, 404)
                self.assertEqual(c.get("/api/deep/..%2Fx").status_code in (400, 404), True)
            finally:
                os.environ.pop("SPHEREX_DEEP_DIR", None)


if __name__ == "__main__":
    unittest.main()


class RealCutoutPhotometryTests(unittest.TestCase):
    """Forced photometry on a committed DR1 cutout (WISE 0855 field, D1)."""

    def test_source_flag_does_not_reject_real_stars(self):
        import glob
        import os
        from astropy.io import fits
        from astropy.wcs import WCS
        from spherexlens.deep import photometry
        path = sorted(glob.glob(os.path.join(os.path.dirname(__file__),
                                             "fixtures", "level2_*_cut.fits.gz")))[0]
        with fits.open(path) as h:
            img = h["IMAGE"].data
            flags = h["FLAGS"].data
            wcs = WCS(h["IMAGE"].header)
        # brightest pixels away from the edge = real stars, many carry SOURCE
        y, x = np.unravel_index(np.argsort(np.nan_to_num(img[10:-10, 10:-10]),
                                           axis=None)[-40:], (img.shape[0] - 20,
                                                              img.shape[1] - 20))
        src = []
        for i, (yy, xx) in enumerate(zip(y + 10, x + 10)):
            ra, dec = wcs.pixel_to_world_values(float(xx), float(yy))
            src.append({"id": i, "ra": float(ra), "dec": float(dec)})
        on_source = sum(bool(flags[yy, xx] & (1 << 21)) for yy, xx in zip(y + 10, x + 10))
        out = photometry.measure(path, src)
        self.assertGreater(len(out), 10)
        self.assertGreater(on_source, 0, "fixture should contain SOURCE-flagged stars")
        unflagged = [m for m in out if not m["flagged"]]
        self.assertGreater(len(unflagged), len(out) // 2)
        self.assertTrue(all(m["wavelength_um"] for m in out))
        self.assertTrue(all(m["flux_mjy"] > 0 for m in unflagged[:5]))


class EnsembleTests(unittest.TestCase):
    def test_only_stars_standing_out_from_the_field_stay_variable(self):
        res = [{"status": "ok", "variable": True, "chi2_dof": c}
               for c in (2.0, 2.5, 3.0, 2.2, 40.0)]
        med = lightcurve.flag_variables_against_ensemble(res)
        self.assertAlmostEqual(med, 2.5)
        self.assertEqual([r["variable"] for r in res], [False] * 4 + [True])
        self.assertTrue(all(r["variable_self"] for r in res))


class DeepCloudTests(unittest.TestCase):
    def test_publish_writes_strict_json_to_bucket_and_route_reads_it(self):
        import json
        import os
        from unittest.mock import patch
        from spherexlens.deep import run as deep_run
        from spherexlens.routes import deep as deep_route

        class Blob:
            def __init__(self, name): self.name = name

        class Bucket:
            def __init__(self): self.objects = {}
            def list_blobs(self, prefix=""):
                raise AssertionError("the API must never list the bucket")

        class FakeStore:
            bucket = Bucket()
            def __init__(self, *a, **k): pass
            def write_json(self, name, value):
                self.bucket.objects[name] = json.dumps(value, allow_nan=False)
            def read_json(self, name):
                raw = self.bucket.objects.get(name)
                return None if raw is None else json.loads(raw)

        os.environ["SPHEREX_SNAPSHOT_BUCKET"] = "fake-bucket"
        try:
            import app as _app
            with patch("snapshot_store.SnapshotStore", FakeStore),                     patch.object(_app, "_snapshot_store", lambda: FakeStore()):
                deep_route._BUCKET_CACHE.clear()
                key = deep_run.publish("NEP", {"name": "NEP", "ra": 270.0,
                                               "sources": [{"chi2": float("inf")}]})
                self.assertEqual(key, "deep/NEP.json")
                from fastapi.testclient import TestClient
                import app
                c = TestClient(app.app)
                self.assertEqual(c.get("/api/deep").json()["fields"][0]["name"], "NEP")
                body = c.get("/api/deep/NEP").json()
                self.assertIsNone(body["sources"][0]["chi2"])       # inf -> null
                self.assertEqual(c.get("/api/deep/nope").status_code, 404)
        finally:
            os.environ.pop("SPHEREX_SNAPSHOT_BUCKET", None)
            deep_route._BUCKET_CACHE.clear()
