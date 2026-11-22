"""Phase 6: combined evidence score."""
import unittest
from unittest.mock import patch

from astropy.wcs import WCS

from spherexlens.triage import evidence
from step2_coadd import make_target_wcs


def wcs():
    return make_target_wcs(133.78, -7.24, npix=180, pixscale_arcsec=3.0)


def dipole(dx_px=2.0, snr=12.0, mjd_a=60801.5, mjd_b=61166.5):
    return {"kind": "dipole", "snr": snr, "category": "CANDIDATE",
            "position": (90, 90 + dx_px), "position_minus": (90, 90),
            "mjd_a": mjd_a, "mjd_b": mjd_b, "ra": 133.78, "dec": -7.24}


class EvidenceTests(unittest.TestCase):
    def test_spherex_motion_from_lobes(self):
        # 2 px of 3" over one year -> 6000 mas/yr, mostly in RA (x)
        pra, pdec, err = evidence.spherex_motion(dipole(), wcs(), 1.0)
        self.assertAlmostEqual(abs(pra), 6000, delta=120)
        self.assertLess(abs(pdec), 120)

    def test_motion_agreement_with_gaia_gives_high_value(self):
        d = dipole()
        pra, pdec, _ = evidence.spherex_motion(d, wcs(), 1.0)
        d["matches"] = {"gaia": {"source": {"pm_ra": pra, "pm_de": pdec,
                                            "ra": 133.78, "dec": -7.24, "gmag": 15}}}
        with patch("catalog_tools.source_pm", return_value=(pra, pdec)):
            value, detail = evidence.part_motion(d, {"wcs": wcs()})
        self.assertGreater(value, 0.9)
        self.assertIn("Gaia", detail)

    def test_motion_disagreement_gives_low_value(self):
        d = dipole()
        d["matches"] = {"gaia": {"source": {}}}
        with patch("catalog_tools.source_pm", return_value=(-6000.0, 4000.0)):
            value, _ = evidence.part_motion(d, {"wcs": wcs()})
        self.assertLess(value, 0.01)

    def test_unavailable_parts_carry_no_weight(self):
        d = {"kind": "monopole", "snr": 5.0, "category": "CANDIDATE",
             "position": (10, 10), "asteroid": {"status": "unavailable"}}
        payload = {"detections": [d]}
        evidence.enrich_evidence(payload, {"wcs": wcs(), "skip_neossat": True})
        ev = d["evidence"]
        names = {p["name"]: p for p in ev["parts"]}
        self.assertIsNone(names["ml"]["value"])
        self.assertIsNone(names["asteroid_check"]["value"])
        self.assertEqual(names["asteroid_check"]["detail"], "asteroid check unavailable")
        # only "signal" is evaluated -> score equals its value (0.5 at threshold)
        self.assertAlmostEqual(ev["score"], names["signal"]["value"], places=3)

    def test_combined_score_uses_ml_and_asteroid(self):
        d = dipole(snr=20)
        d["ml"] = {"real_bogus": 0.9, "model_version": "rb-test"}
        d["asteroid"] = {"status": "match", "match": {"designation": "433 Eros"}}
        payload = {"detections": [d]}
        evidence.enrich_evidence(payload, {"wcs": wcs(), "skip_neossat": True})
        self.assertGreater(d["evidence"]["score"], 0.9)

    def test_neossat_without_adapter_is_unavailable_not_failure(self):
        with patch.dict("sys.modules", {"spherexlens.ingest.cadc": None}):
            block = evidence.neossat_check(dipole())
        self.assertEqual(block["status"], "unavailable")
        value, detail = evidence.part_neossat(block)
        self.assertIsNone(value)


if __name__ == "__main__":
    unittest.main()
