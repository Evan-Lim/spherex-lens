"""Extra catalogues must survive the map overlay and the field-object merge.

The UI used to hard-code Gaia / AllWISE / VSX.  These tests pin the backend
half of the fix: any catalogue key in the metadata is drawn, merged and given a
magnitude even when its columns are not gmag/w1 (e.g. 2MASS JHK).
"""
import unittest

import app
from step2_coadd import make_target_wcs


class ExtraCatalogTests(unittest.TestCase):
    def setUp(self):
        self.wcs = make_target_wcs(10.0, 20.0, npix=180)
        self.meta = {
            "meta": {"ra": 10.0, "dec": 20.0},
            "catalogs": {
                "gaia": [{"ra": 10.0, "dec": 20.0, "gmag": 15.0}],
                "twomass": [
                    {"ra": 10.0, "dec": 20.0, "jmag": 12.5},
                    {"ra": 10.02, "dec": 20.01, "jmag": 13.5},
                ],
            },
        }

    def test_overlay_draws_extra_catalogue_and_reads_its_magnitude(self):
        rows = app.catalog_overlay(self.meta, self.wcs, npix=180)
        cats = {row["cat"] for row in rows}
        self.assertIn("twomass", cats)
        two_mass = [row for row in rows if row["cat"] == "twomass"]
        self.assertEqual(len(two_mass), 2)
        self.assertEqual(sorted(row["mag"] for row in two_mass), [12.5, 13.5])

    def test_field_objects_merge_extra_catalogue_rows(self):
        rows = app.build_field_objects(self.meta, [], self.wcs, npix=180)
        merged = [row for row in rows
                  if "gaia" in row["catalogs"] and "twomass" in row["catalogs"]]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["mag"], 15.0)   # Gaia row was created first
        self.assertTrue(any(row["catalogs"] == ["twomass"] for row in rows))

    def test_magnitude_fallbacks(self):
        self.assertEqual(app.source_magnitude({"jmag": 11.2}), 11.2)
        self.assertEqual(app.source_magnitude({"phot_g_mean_mag": 9.9}), 9.9)
        self.assertIsNone(app.source_magnitude({"mag": 99.0}))
        self.assertEqual(app.source_magnitude({"gmag": 14.0, "jmag": 11.0}), 14.0)

    def test_triage_uses_extra_catalogue_magnitudes(self):
        from catalog_tools import match_radius, source_mag
        self.assertEqual(source_mag("twomass", {"jmag": 12.0}), 12.0)
        self.assertIsNone(source_mag("twomass", {"jmag": 99.0}))
        self.assertGreater(match_radius("twomass", {"jmag": 8.0}),
                           match_radius("twomass", {"jmag": 18.0}))

    def test_expected_movers_include_extra_catalogues(self):
        catalogs = {"twomass": [{"ra": 10.0, "dec": 20.0,
                                 "pm_ra": 5000.0, "pm_de": 0.0, "jmag": 12.0}]}
        rows = app.expected_movers(catalogs, 60000.0, 60365.0, self.wcs, [],
                                   npix=180)
        self.assertTrue(any(row["cat"] == "twomass" for row in rows))
        self.assertEqual(rows[0]["mag"], 12.0)

    def test_local_target_list_without_a_snapshot_store(self):
        import json
        import os
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "Demo_meta.json"), "w") as file:
                json.dump({"target": "Demo",
                           "meta": {"ra": 10.0, "dec": 20.0,
                                    "common": "Demo field",
                                    "type": "Custom field"},
                           "paths": [], "epochs": [], "catalogs": {}}, file)
            with patch.object(app, "CACHE", tmp), \
                    patch.object(app, "_snapshot_store", lambda: None):
                body = app.list_targets()
        self.assertEqual([t["name"] for t in body["targets"]], ["Demo"])
        self.assertEqual(body["targets"][0]["common"], "Demo field")


if __name__ == "__main__":
    unittest.main()
