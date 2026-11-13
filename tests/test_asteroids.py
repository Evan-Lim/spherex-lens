"""Phase 1.1: asteroid check (JPL sb_ident + SBDB + CADC SSOIS), offline.

Fixtures are real, trimmed responses (tests/fixtures/archive/):
  * jpl_sb_ident_piazzia_20250925T12.json -- geocentric sb_ident for a
    0.2 deg box at 19h06m38.4s -30d04m48s, 2025-09-25 12:00 UT: lists
    (1000) Piazzia, a numbered main-belt asteroid, 34" from the centre;
  * jpl_sb_ident_piazzia_20251002T12.json -- the same box a week later
    (Piazzia has left);
  * jpl_sbdb_1000.json                     -- SBDB record of (1000) Piazzia;
  * ssois_1000_20250920_20251005.tsv       -- CADC SSOIS (CADC ephemeris)
    DECam images of Piazzia on 2025-09-24 and 2025-09-25.
"""
import copy
import json
import os
import unittest
from unittest.mock import Mock

import numpy as np
import requests

import config
from spherexlens.ingest import jpl, ssois
from spherexlens.ingest.jpl import JPLSbdb, JPLSbIdent
from spherexlens.ingest.ssois import CadcSsois
from spherexlens.triage import asteroids

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "archive")

FIELD_RA, FIELD_DEC = 286.66, -30.08          # 19:06:38.4 -30:04:48
MJD_A, MJD_B = 60943.5, 60950.5               # 2025-09-25 12:00, 2025-10-02 12:00
PIAZZIA_RA = jpl.parse_ra("19:06:37.64")      # JPL second pass at MJD_A
PIAZZIA_DEC = jpl.parse_dec("-30 05'20.1\"")


def _read(name, mode="rb"):
    with open(os.path.join(FIX, name), mode) as f:
        return f.read()


class FakeSession:
    """Serves the recorded responses by URL; ``down`` names failing hosts."""

    def __init__(self, down=()):
        self.headers = {}
        self.down = set(down)
        self.calls = []

    def request(self, method, url, params=None, data=None, timeout=None):
        self.calls.append((url, dict(params or {}), timeout))
        for host in self.down:
            if host in url:
                raise requests.ConnectionError(f"{host} unreachable")
        p = params or {}
        if "sb_ident.api" in url:
            day = p["obs-time"][:10]
            body = {"2025-09-25": _read("jpl_sb_ident_piazzia_20250925T12.json"),
                    "2025-10-02": _read("jpl_sb_ident_piazzia_20251002T12.json")}[day]
        elif "sbdb.api" in url:
            body = (_read("jpl_sbdb_1000.json") if p.get("sstr") == "1000" else
                    b'{"code":"200","message":"specified object was not found"}')
        elif "ssosclf.pl" in url:
            body = _read("ssois_1000_20250920_20251005.tsv")
        else:
            raise AssertionError(f"unexpected URL {url}")
        return Mock(status_code=200, content=body, text=body.decode(), url=url)


def adapters(session):
    out = {"sb_ident": JPLSbIdent(session=session, use_cache=False),
           "sbdb": JPLSbdb(session=session, use_cache=False),
           "ssois": CadcSsois(session=session, use_cache=False)}
    for a in out.values():
        a.min_interval_s = 0
    return out


def detection(ra, dec, kind="monopole_negative", category="CANDIDATE", **kw):
    d = {"ra": ra, "dec": dec, "kind": kind, "snr": 8.0, "position": [90, 90],
         "epochs": [1, 2], "mjd_a": MJD_A, "mjd_b": MJD_B,
         "pair": "E1→E2", "category": category,
         "verdict": "CANDIDATE" if category == "CANDIDATE" else "KNOWN",
         "priority": 1 if category == "CANDIDATE" else 3,
         "reasons": ["no catalogue entry within the match radius"]}
    if kind == "monopole_negative":
        d["sign"] = -1
    d.update(kw)
    return d


def run(dets, down=(), **ctx_extra):
    session = FakeSession(down=down)
    payload = {"detections": dets, "ra": FIELD_RA, "dec": FIELD_DEC,
               "stats": {"categories": {}, "n_priority": 0}}
    ctx = {"ra": FIELD_RA, "dec": FIELD_DEC, "npix": 180, "pixscale_arcsec": 3.0,
           "epochs": [1, 2], "mjds": [MJD_A, MJD_B], "epoch_order": [1, 2],
           "epoch_mjd": {1: MJD_A, 2: MJD_B},
           "asteroid_adapters": adapters(session)}
    ctx.update(ctx_extra)
    asteroids.check_asteroids(payload, ctx)
    return payload, session


class _NoBackoff(unittest.TestCase):
    def setUp(self):
        self._backoff = config.HTTP_BACKOFF_S
        config.HTTP_BACKOFF_S = 0

    def tearDown(self):
        config.HTTP_BACKOFF_S = self._backoff


class AdapterParsingTests(_NoBackoff):
    def test_sexagesimal_parameters(self):
        self.assertEqual(jpl.ra_param(286.66), "19-06-38.40")
        self.assertEqual(jpl.dec_param(-30.08), "M30-04-48.0")
        self.assertEqual(jpl.dec_param(21.5), "21-30-00.0")
        self.assertEqual(jpl.mjd_to_obs_time(MJD_A), "2025-09-25_12:00:00")

    def test_designation_keys(self):
        self.assertEqual(jpl.sbdb_key("1000 Piazzia (A923 PF)"), "1000")
        self.assertEqual(jpl.sbdb_key("(2013 UZ47)"), "2013 UZ47")
        self.assertEqual(jpl.sbdb_key("C/2022 P1 (NEOWISE)"), "C/2022 P1")

    def test_sb_ident_rows(self):
        a = adapters(FakeSession())["sb_ident"]
        res = a.query(FIELD_RA, FIELD_DEC, MJD_A, 0.1)
        names = [r["designation"] for r in res.rows]
        self.assertIn("1000 Piazzia (A923 PF)", names)
        p = res.rows[names.index("1000 Piazzia (A923 PF)")]
        self.assertAlmostEqual(p["ra"], PIAZZIA_RA, places=6)
        self.assertAlmostEqual(p["dec"], PIAZZIA_DEC, places=6)
        self.assertEqual(p["v_mag"], 15.5)
        self.assertAlmostEqual(p["ra_rate_arcsec_h"], 26.15)
        self.assertEqual(p["key"], "1000")

    def test_sb_ident_request_is_geocentric_two_pass_with_long_timeout(self):
        s = FakeSession()
        adapters(s)["sb_ident"].query(FIELD_RA, FIELD_DEC, MJD_A, 0.1)
        url, params, timeout = s.calls[0]
        self.assertEqual(params["mpc-code"], "500")
        self.assertEqual(params["two-pass"], "true")
        self.assertEqual(params["fov-ra-center"], "19-06-38.40")
        self.assertEqual(params["fov-dec-center"], "M30-04-48.0")
        self.assertGreaterEqual(timeout, config.JPL_SB_IDENT_TIMEOUT_S)

    def test_sbdb_record_and_not_found(self):
        a = adapters(FakeSession())["sbdb"]
        info = a.lookup("1000")
        self.assertEqual(info["designation"], "1000 Piazzia")
        self.assertEqual(info["orbit_class"], "MBA")
        self.assertFalse(info["neo"])
        self.assertEqual(info["h_mag"], 10.63)
        self.assertEqual(info["pos_unc_arcsec"], 1.0)        # U = 0
        self.assertIn("sstr=1000", info["sbdb_url"])
        self.assertIsNone(a.lookup("nonesuch"))

    def test_ssois_rows_and_interpolation(self):
        res = adapters(FakeSession())["ssois"].search("1000", MJD_A - 2, MJD_A + 2)
        self.assertEqual(len(res.rows), 4)
        self.assertTrue(all("DECam" in r["instrument"] for r in res.rows))
        self.assertEqual(res.meta["n_neossat"], 0)
        pos = ssois.position_at(res.rows, MJD_A, 2.0)
        self.assertEqual(pos["method"], "interpolated")
        sep = asteroids.sep_arcsec(pos["ra"], pos["dec"], PIAZZIA_RA, PIAZZIA_DEC)
        self.assertLess(sep, 5.0)                 # two ephemerides agree
        self.assertIsNone(ssois.position_at(res.rows, MJD_A + 10, 2.0))

    def test_ssois_not_found_and_garbage(self):
        rows, found = ssois.parse_tsv("\t".join(ssois.TSV_COLUMNS)
                                      + "\nObject 'zz' not found.<br/>\n")
        self.assertEqual((rows, found), ([], False))
        with self.assertRaises(ssois.ArchiveUnavailable):
            ssois.parse_tsv("<html>502 Bad Gateway</html>")


class EnricherTests(_NoBackoff):
    def test_match_becomes_known_asteroid_with_ssois_agreement(self):
        # Piazzia was in the field at epoch a only: negative monopole
        d = detection(PIAZZIA_RA + 2.0 / 3600, PIAZZIA_DEC)
        payload, _ = run([d])
        a = d["asteroid"]
        self.assertEqual(a["status"], "match")
        self.assertEqual(d["category"], "KNOWN_ASTEROID")
        self.assertEqual(d["verdict"], "KNOWN")
        self.assertEqual(d["priority"], config.ASTEROID_KNOWN_PRIORITY)
        m = a["match"]
        self.assertEqual(m["designation"], "1000 Piazzia")
        self.assertEqual(m["orbit_class"], "MBA")
        self.assertFalse(m["neo"])
        self.assertEqual(m["epoch"], "a")
        self.assertAlmostEqual(m["mjd"], MJD_A)
        self.assertLess(m["sep_arcsec"], 3.0)
        self.assertTrue(m["ssois_agrees"])
        self.assertLess(m["ssois_sep_arcsec"], config.SSOIS_AGREEMENT_ARCSEC)
        self.assertIn("sbdb_lookup", m["sbdb_url"])
        self.assertIn("ssois", m["ssois_url"])
        self.assertEqual(a["checked"], ["jpl_sb_ident", "jpl_sbdb", "ssois"])
        self.assertGreaterEqual(a["n_in_field"], 1)
        self.assertTrue(d["reasons"][0].startswith("known asteroid 1000 Piazzia"))
        self.assertIn("CADC SSOIS agrees", d["reasons"][0])
        self.assertEqual(payload["asteroid_check"]["status"], "ok")
        self.assertEqual(payload["asteroid_check"]["n_queries"], 2)
        self.assertEqual(payload["stats"]["categories"], {"KNOWN_ASTEROID": 1})

    def test_sign_convention_positive_monopole_is_checked_against_epoch_b(self):
        # same spot, but a *positive* monopole = present in the later epoch,
        # when Piazzia had already gone: no match
        d = detection(PIAZZIA_RA, PIAZZIA_DEC, kind="monopole")
        run([d])
        self.assertEqual(d["asteroid"]["status"], "no_match")
        self.assertNotEqual(d["category"], "KNOWN_ASTEROID")

    def test_no_match_near_ecliptic_is_possible_unknown_asteroid(self):
        d = detection(FIELD_RA + 0.03, FIELD_DEC + 0.03, kind="monopole")
        payload, _ = run([d])
        self.assertEqual(d["asteroid"]["status"], "no_match")
        self.assertEqual(d["category"], "POSSIBLE_UNKNOWN_ASTEROID")
        self.assertEqual(d["verdict"], "CANDIDATE")
        self.assertEqual(d["priority"], config.ASTEROID_POSSIBLE_PRIORITY)
        self.assertIn("one epoch", d["reasons"][0])
        self.assertLess(abs(payload["asteroid_check"]["ecliptic_latitude_deg"]),
                        config.ECLIPTIC_LATITUDE_WARN_DEG)

    def test_known_variable_is_never_relabelled(self):
        d = detection(PIAZZIA_RA, PIAZZIA_DEC, category="KNOWN_VARIABLE")
        run([d])
        self.assertEqual(d["asteroid"]["status"], "match")
        self.assertEqual(d["category"], "KNOWN_VARIABLE")
        self.assertEqual(d["verdict"], "KNOWN")
        self.assertIn("may contribute", d["reasons"][-1])

    def test_dipole_is_not_a_possible_asteroid(self):
        d = detection(FIELD_RA + 0.03, FIELD_DEC + 0.03, kind="dipole")
        run([d])
        self.assertEqual(d["category"], "CANDIDATE")

    def test_variable_in_several_epochs_is_not_possible_asteroid(self):
        # three epochs; the source is bright in epochs 2 and 3 -> not one-epoch
        img = np.zeros((180, 180))
        yy, xx = np.mgrid[0:180, 0:180]
        blob = 50 * np.exp(-((yy - 90) ** 2 + (xx - 90) ** 2) / (2 * 1.4 ** 2))
        stacks = {1: img, 2: img + blob, 3: img + blob}
        d = detection(FIELD_RA + 0.03, FIELD_DEC + 0.03, kind="monopole")
        run([d], stacks=stacks, noises={1: 1.0, 2: 1.0, 3: 1.0},
            epoch_order=[1, 2, 3])
        self.assertEqual(d["category"], "CANDIDATE")
        d2 = detection(FIELD_RA + 0.03, FIELD_DEC + 0.03, kind="monopole")
        run([d2], stacks={1: img, 2: img + blob, 3: img},
            noises={1: 1.0, 2: 1.0, 3: 1.0}, epoch_order=[1, 2, 3])
        self.assertEqual(d2["category"], "POSSIBLE_UNKNOWN_ASTEROID")

    def test_jpl_down_marks_unavailable_and_keeps_categories(self):
        dets = [detection(PIAZZIA_RA, PIAZZIA_DEC),
                detection(FIELD_RA + 0.03, FIELD_DEC, kind="monopole"),
                detection(FIELD_RA, FIELD_DEC, category="KNOWN_VARIABLE")]
        before = [(d["category"], d["verdict"], d["priority"]) for d in dets]
        payload, _ = run(dets, down={"ssd-api.jpl.nasa.gov"})
        self.assertEqual([(d["category"], d["verdict"], d["priority"]) for d in dets],
                         before)
        for d in dets:
            self.assertEqual(d["asteroid"]["status"], "unavailable")
            self.assertIn("unreachable", d["asteroid"]["error"])
            self.assertIn(asteroids.UNAVAILABLE_REASON, d["reasons"])
        chk = payload["asteroid_check"]
        self.assertEqual(chk["status"], "unavailable")
        self.assertEqual(chk["n_queries"], 2)
        self.assertTrue(chk["errors"])

    def test_ssois_down_still_matches_with_null_agreement(self):
        d = detection(PIAZZIA_RA, PIAZZIA_DEC)
        payload, _ = run([d], down={"cadc-ccda"})
        m = d["asteroid"]["match"]
        self.assertEqual(d["category"], "KNOWN_ASTEROID")
        self.assertIsNone(m["ssois_agrees"])
        self.assertNotIn("ssois", d["asteroid"]["checked"])
        self.assertIn("SSOIS cross-check unavailable", d["reasons"])
        self.assertTrue(any("ssois" in e for e in payload["asteroid_check"]["errors"]))

    def test_ssois_disagreement_is_flagged(self):
        d = detection(PIAZZIA_RA, PIAZZIA_DEC)
        old = config.SSOIS_AGREEMENT_ARCSEC
        config.SSOIS_AGREEMENT_ARCSEC = 0.01
        try:
            run([d])
        finally:
            config.SSOIS_AGREEMENT_ARCSEC = old
        self.assertIs(d["asteroid"]["match"]["ssois_agrees"], False)
        self.assertIn(asteroids.DISAGREE_REASON, d["reasons"])

    def test_skips_without_detections_or_times(self):
        payload, s = run([])
        self.assertEqual(payload["asteroid_check"]["status"], "skipped")
        self.assertEqual(s.calls, [])
        d = detection(PIAZZIA_RA, PIAZZIA_DEC)
        payload, s = run([d], mjds=[None, None], epoch_mjd={})
        self.assertEqual(payload["asteroid_check"]["status"], "skipped")
        self.assertEqual(d["category"], "CANDIDATE")
        self.assertEqual(d["asteroid"]["status"], "unavailable")
        self.assertEqual(s.calls, [])

    def test_query_times_dedupe_and_cap(self):
        ctx = {"epochs": [1] * 6 + [2] * 6,
               "mjds": [100.0, 100.001, 100.5, 101.0, 101.5, 102.0,
                        200.0, 200.002, 200.4, 200.8, 201.2, 201.6]}
        times = asteroids.query_times(ctx, cap=4)
        self.assertEqual(sum(len(v) for v in times.values()), 4)
        self.assertEqual(times[1][0], 100.0)
        self.assertEqual(times[1][-1], 102.0)
        self.assertEqual(len(asteroids.query_times(ctx, cap=1)), 1)

    def test_registered_with_enrich_hook(self):
        from spherexlens import enrich
        self.assertIn("asteroids", enrich.registered())


if __name__ == "__main__":
    unittest.main()


class TimeBoxTests(unittest.TestCase):
    def test_slow_jpl_is_time_boxed_not_blocking(self):
        import threading
        import config
        from spherexlens.triage import asteroids as ast
        release = threading.Event()

        class SlowIdent:
            def query(self, *a, **k):
                release.wait(5)
                raise ast.ArchiveUnavailable("late")

        payload = {"ra": 10.0, "dec": 0.0, "detections": [
            {"kind": "monopole", "snr": 8, "category": "CANDIDATE",
             "verdict": "CANDIDATE", "position": (50, 50), "ra": 10.0, "dec": 0.0,
             "epochs": [1, 2], "mjd_a": 60800.0, "mjd_b": 60990.0, "reasons": []}]}
        ctx = {"ra": 10.0, "dec": 0.0, "npix": 180, "pixscale_arcsec": 3.0,
               "mjds": [60800.0, 60990.0], "epochs": [1, 2],
               "asteroid_adapters": {"sb_ident": SlowIdent(), "sbdb": object(),
                                     "ssois": object()}}
        old = config.ASTEROID_TIME_BOX_S
        config.ASTEROID_TIME_BOX_S = 0.3
        try:
            import time as _t
            t0 = _t.monotonic()
            ast.check_asteroids(payload, ctx)
            self.assertLess(_t.monotonic() - t0, 3.0)
        finally:
            config.ASTEROID_TIME_BOX_S = old
            release.set()
        self.assertEqual(payload["asteroid_check"]["status"], "unavailable")
        self.assertEqual(payload["asteroid_check"]["pending"], 2)
        self.assertEqual(payload["detections"][0]["asteroid"]["status"], "unavailable")
        self.assertEqual(payload["detections"][0]["category"], "CANDIDATE")
