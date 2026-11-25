"""Scientific-validity fixes (Nov 2026):

* wavelength-matched exposure selection (spherexlens.ingest.lvf + step1_ingest.select_exposures)
* IRSA SIA2 point query (config.SIA2_RADIUS_DEG)
* OUTLIER pixels kept in the co-add so moving asteroids survive (step2_coadd.BAD_BITS)
* JPL predictions fainter than SPHEREx's depth are not matches (config.ASTEROID_VMAG_LIMIT)
"""
import unittest
from unittest import mock

import numpy as np

import config
import step1_ingest
import step2_coadd
from spherexlens.ingest import lvf

RA0, DEC0 = 150.0, 2.0
SIDE = 1.0      # degrees, synthetic detector footprint


def row(u, v, det=1, t=60900.0, name="x"):
    """An SIA2 row whose footprint puts (RA0, DEC0) at detector position (u, v)."""
    cd = np.cos(np.radians(DEC0))
    ra_l = RA0 - u * SIDE / cd
    dec_b = DEC0 - v * SIDE
    ra_r, dec_t = ra_l + SIDE / cd, dec_b + SIDE
    corners = [(ra_l, dec_b), (ra_r, dec_b), (ra_r, dec_t), (ra_l, dec_t)]
    s_region = "POLYGON ICRS " + " ".join(f"{a:.8f} {b:.8f}" for a, b in corners)
    return {"dataproduct_type": "image", "access_url": f"https://x/{name}.fits",
            "t_min": str(t), "energy_bandpassname": f"SPHEREx-D{det}", "s_region": s_region,
            "obs_id": name}


class WavelengthTests(unittest.TestCase):
    def test_detector_position_inverts_the_footprint(self):
        for u, v in ((0.5, 0.5), (0.2, 0.7), (0.85, 0.1)):
            uu, vv = lvf.detector_position(row(u, v), RA0, DEC0)
            self.assertAlmostEqual(uu, u, places=3)
            self.assertAlmostEqual(vv, v, places=3)

    def test_wavelength_changes_across_the_detector_and_stays_in_band(self):
        lam = [lvf.wavelength_um(row(u, 0.5, det=1), RA0, DEC0) for u in (0.1, 0.5, 0.9)]
        lam2 = [lvf.wavelength_um(row(0.5, v, det=1), RA0, DEC0) for v in (0.1, 0.5, 0.9)]
        allv = lam + lam2
        self.assertTrue(all(0.7 < x < 1.15 for x in allv), allv)      # D1 is 0.75-1.1 micron
        self.assertGreater(max(allv) - min(allv), 0.05)               # the LVF really varies

    def test_unknown_detector_or_bad_polygon_gives_none(self):
        r = row(0.5, 0.5); r["energy_bandpassname"] = "WISE-W1"
        self.assertIsNone(lvf.wavelength_um(r, RA0, DEC0))
        r = row(0.5, 0.5); r["s_region"] = ""
        self.assertIsNone(lvf.wavelength_um(r, RA0, DEC0))

    def test_matched_choice_puts_the_closest_wavelength_first(self):
        e1 = [row(0.2, 0.5, name="a1"), row(0.5, 0.5, name="a2")]
        e2 = [row(0.9, 0.5, name="b1"), row(0.21, 0.5, name="b2")]
        out, worst = lvf.matched_choice([e1, e2], RA0, DEC0, 0.012)
        self.assertEqual(out[0][0]["obs_id"], "a1")
        self.assertEqual(out[1][0]["obs_id"], "b2")
        self.assertLess(worst, 0.01)


class SelectExposureTests(unittest.TestCase):
    def rows(self):
        # epoch 1 (t=60900) and epoch 2 (t=61100); band D1.  "Depth" = distance of the target
        # to the nearest footprint edge.  Epoch 2's deepest exposure (u=0.5) sees a different
        # wavelength than epoch 1's (u=0.4); e2_match (u=0.401) is less deep but matches.
        return [row(0.4, 0.5, t=60900.0, name="e1_deep"), row(0.8, 0.5, t=60900.1, name="e1_edge"),
                row(0.5, 0.5, t=61100.0, name="e2_deep"), row(0.401, 0.5, t=61100.1, name="e2_match")]

    def test_epochs_get_wavelength_matched_exposures(self):
        with mock.patch.object(config, "MATCH_WAVELENGTH", True):
            slots, n = step1_ingest.select_exposures(self.rows(), RA0, DEC0, bands=("SPHEREx-D1",))
        self.assertEqual(n, 2)
        first = {e: c[0]["obs_id"] for e, c in slots}
        lam = {e: lvf.wavelength_um(c[0], RA0, DEC0) for e, c in slots}
        self.assertLess(abs(np.log(lam[1] / lam[2])), config.MATCH_WAVELENGTH_MAX_DLNLAM)
        self.assertEqual(first[2], "e2_match")

    def test_switch_off_restores_depth_ranking(self):
        with mock.patch.object(config, "MATCH_WAVELENGTH", False):
            slots, _ = step1_ingest.select_exposures(self.rows(), RA0, DEC0, bands=("SPHEREx-D1",))
        self.assertEqual({e: c[0]["obs_id"] for e, c in slots}, {1: "e1_deep", 2: "e2_deep"})


class SiaRadiusTests(unittest.TestCase):
    def test_default_is_a_point_query(self):
        seen = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            seen.update(params)
            raise RuntimeError("stop")
        with mock.patch.object(step1_ingest.requests, "get", side_effect=fake_get), \
                mock.patch.object(step1_ingest.time, "sleep", lambda s: None):
            with self.assertRaises(Exception):
                step1_ingest.query_sia2(RA0, DEC0, use_cache=False, retries=1)
        self.assertTrue(seen["POS"].endswith(f" {config.SIA2_RADIUS_DEG}"), seen)
        self.assertLessEqual(config.SIA2_RADIUS_DEG, 0.01)


class FlagMaskTests(unittest.TestCase):
    def test_outlier_pixels_stay_in_the_coadd_but_not_in_photometry(self):
        self.assertEqual(step2_coadd.BAD_BITS & step2_coadd.BIT_OUTLIER, 0)
        self.assertTrue(step2_coadd.PHOT_BAD_BITS & step2_coadd.BIT_OUTLIER)
        self.assertTrue(step2_coadd.BAD_BITS & step2_coadd.BIT_TRANSIENT)   # cosmic rays still out
        from spherexlens.deep import photometry
        self.assertEqual(photometry.BAD_FLAG_MASK, step2_coadd.PHOT_BAD_BITS)


if __name__ == "__main__":
    unittest.main()
