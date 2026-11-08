"""
step7_neowise.py

Infrared history for every position the dashboard cares about: the target
itself and each triaged detection. Run it after step1_ingest:

    python step7_neowise.py                         # every cached target
    python step7_neowise.py --targets WISE_0855 RW_Aurigae

For each target it sends ONE upload-join query per table to IRSA TAP. A
NEOWISE query costs roughly the same (tens of seconds, measured 16-83 s)
whether it holds 1 position or 30, so every position of a target goes into a
single uploaded table:

    neowiser_p1bs_psd   NEOWISE-R single exposures, 2013-12 .. 2024-08
    allwise_p3as_mep    AllWISE per-exposure photometry, 2010 .. 2011

Exposures are quality-cut, grouped into visits (WISE sees a given spot for
~1 day every ~6 months), and summarised per position:

    N1  visit light curve, W1 / W2, 2010-2024
    N2  variability numbers: amplitude, rms, reduced chi^2, linear trend
    N3  proper motion from the visit positions (faint movers Gaia misses)
    N4  the target's own history (outbursts, AGN variability)

Output: data_cache/<target>_neowise.json, read by app.py.
"""

import os
import io
import json
import time
import math
import argparse

import numpy as np
import requests

from catalog_tools import target_position, sep_arcsec, mjd_to_year, year_to_mjd

IRSA_TAP_SYNC = "https://irsa.ipac.caltech.edu/TAP/sync"
CACHE = os.environ.get("SPHEREX_CACHE", "data_cache")

DETECTION_RADIUS_ARCSEC = 8.0      # SPHEREx position (3" grid) + WISE 6" PSF
TARGET_RADIUS_ARCSEC = 6.0
VISIT_GAP_DAYS = 30.0
MIN_EXPOSURES_PER_VISIT = 3        # a 1-2 exposure "visit" has no usable error
# NEOWISE single exposures saturate brighter than about these (Vega mag)
SATURATION_MAG = {"w1": 8.0, "w2": 7.0}
SYSTEMATIC_MAG = 0.02              # per-visit floor, added in quadrature
TRACK_YEARS = np.arange(2010.0, 2025.0, 0.5)

TABLES = {
    "neowise": {
        "table": "neowiser_p1bs_psd",
        "cols": "n.ra, n.dec, n.mjd, n.w1mpro, n.w1sigmpro, n.w2mpro, "
                "n.w2sigmpro, n.qual_frame, n.qi_fact, n.saa_sep, "
                "n.moon_masked, n.cc_flags, n.nb, n.na",
        "w1": "w1mpro", "w1sig": "w1sigmpro", "w2": "w2mpro", "w2sig": "w2sigmpro",
    },
    "allwise_mep": {
        "table": "allwise_p3as_mep",
        "cols": "n.ra, n.dec, n.mjd, n.w1mpro_ep, n.w1sigmpro_ep, "
                "n.w2mpro_ep, n.w2sigmpro_ep, n.qi_fact, n.saa_sep, "
                "n.moon_masked, n.nb, n.na",
        "w1": "w1mpro_ep", "w1sig": "w1sigmpro_ep",
        "w2": "w2mpro_ep", "w2sig": "w2sigmpro_ep",
    },
}


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
def _votable(positions):
    rows = "".join(
        f"<TR><TD>{p['pid']}</TD><TD>{p['ra']:.7f}</TD><TD>{p['dec']:.7f}</TD>"
        f"<TD>{p['radius_arcsec'] / 3600.0:.7f}</TD></TR>" for p in positions)
    return ('<?xml version="1.0"?><VOTABLE version="1.3" '
            'xmlns="http://www.ivoa.net/xml/VOTable/v1.3"><RESOURCE><TABLE>'
            '<FIELD name="pid" datatype="int"/>'
            '<FIELD name="pra" datatype="double" unit="deg"/>'
            '<FIELD name="pdec" datatype="double" unit="deg"/>'
            '<FIELD name="prad" datatype="double" unit="deg"/>'
            f'<DATA><TABLEDATA>{rows}</TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>')


def query_positions(positions, key, retries=3, strict=False):
    """One upload-join TAP query; returns a list of {column: str} rows."""
    spec = TABLES[key]
    adql = (f"SELECT p.pid, {spec['cols']} FROM {spec['table']} AS n, "
            "TAP_UPLOAD.pos AS p WHERE 1=CONTAINS(POINT('ICRS', n.ra, n.dec), "
            "CIRCLE('ICRS', p.pra, p.pdec, p.prad))")
    for attempt in range(retries):
        try:
            t0 = time.time()
            resp = requests.post(
                IRSA_TAP_SYNC,
                data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "tsv",
                      "UPLOAD": "pos,param:posfile", "QUERY": adql},
                files={"posfile": ("pos.xml", io.BytesIO(_votable(positions).encode()),
                                   "application/x-votable+xml")},
                timeout=600)
            resp.raise_for_status()
            text = resp.text.strip()
            if text.startswith("<"):
                raise RuntimeError(text[:300])
            lines = text.splitlines()
            header = lines[0].split("\t")
            rows = [dict(zip(header, ln.split("\t"))) for ln in lines[1:]]
            print(f"    {spec['table']}: {len(rows)} rows for {len(positions)} "
                  f"cones in {time.time() - t0:.0f}s")
            return rows
        except Exception as e:
            print(f"    {spec['table']} attempt {attempt + 1} failed: "
                  f"{type(e).__name__}: {str(e)[:160]}")
            time.sleep(15)
    if strict:
        raise RuntimeError(
            f"{spec['table']} query failed after {retries} attempts")
    return []


def _f(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def good_exposure(row, key):
    """Standard WISE single-exposure quality cuts (Explanatory Supplement)."""
    if (_f(row.get("qi_fact")) or 0) <= 0 or (_f(row.get("saa_sep")) or 0) <= 0:
        return False
    if not str(row.get("moon_masked", "0")).startswith("00"):
        return False
    if key == "neowise":
        if (_f(row.get("qual_frame")) or 0) <= 0:
            return False
        if not str(row.get("cc_flags", "0000")).startswith("00"):
            return False
    if (_f(row.get("nb")) or 1) > 2 or (_f(row.get("na")) or 0) > 0:
        return False
    return True


# ---------------------------------------------------------------------------
# Visits and summaries
# ---------------------------------------------------------------------------
def group_visits(points):
    """points: [{mjd, ra, dec, w1, w1sig, w2, w2sig}] -> per-visit medians."""
    points = sorted(points, key=lambda p: p["mjd"])
    visits, current = [], []
    for p in points:
        if current and p["mjd"] - current[-1]["mjd"] > VISIT_GAP_DAYS:
            visits.append(current)
            current = []
        current.append(p)
    if current:
        visits.append(current)

    out = []
    for v in visits:
        entry = {"mjd": round(float(np.median([p["mjd"] for p in v])), 3),
                 "n": len(v),
                 "ra": float(np.median([p["ra"] for p in v])),
                 "dec": float(np.median([p["dec"] for p in v])),
                 "source": v[0]["source"]}
        entry["year"] = round(mjd_to_year(entry["mjd"]), 3)
        for band in ("w1", "w2"):
            mags = np.array([p[band] for p in v if p.get(band) is not None])
            sigs = np.array([p[band + "sig"] for p in v
                             if p.get(band) is not None and p.get(band + "sig")])
            if mags.size == 0:
                entry[band], entry[band + "_err"] = None, None
                continue
            stat = (1.2533 * np.std(mags, ddof=1) / math.sqrt(mags.size)
                    if mags.size >= 3 else (float(np.median(sigs)) if sigs.size else 0.1))
            entry[band] = round(float(np.median(mags)), 4)
            entry[band + "_err"] = round(math.hypot(stat, SYSTEMATIC_MAG), 4)
        out.append(entry)
    return out


def band_stats(visits, band):
    """N2: variability numbers for one band from the visit means."""
    v = [(x["year"], x[band], x[band + "_err"]) for x in visits
         if x.get(band) is not None and x.get(band + "_err")]
    if len(v) < 2:
        return {"n_visits": len(v)}
    t, m, e = (np.array(a, dtype=float) for a in zip(*v))
    w = 1.0 / e ** 2
    mean = float(np.sum(w * m) / np.sum(w))
    stats = {"n_visits": len(v), "mean": round(mean, 3),
             "saturated": mean < SATURATION_MAG[band],
             "amplitude": round(float(m.max() - m.min()), 3),
             "rms": round(float(np.std(m)), 3),
             "chi2_red": round(float(np.sum(((m - mean) / e) ** 2) / (len(v) - 1)), 2),
             "years": [round(float(t.min()), 2), round(float(t.max()), 2)]}
    if len(v) >= 4:
        coef, cov = np.polyfit(t, m, 1, w=1.0 / e, cov="unscaled")
        stats["trend_mag_per_yr"] = round(float(coef[0]), 4)
        stats["trend_err"] = round(float(math.sqrt(cov[0, 0])), 4)
    return stats


def is_variable(w1, w2):
    """
    N2 decision: did this position already vary in WISE?

    Default rule: in W1 or W2, at least 4 visits, reduced chi^2 >= 10 against
    a constant AND a peak-to-peak amplitude >= 0.1 mag. The chi^2 alone fires
    on bright stars with tiny errors; the amplitude alone fires on faint noisy
    ones -- requiring both keeps each in check.
    """
    for s in (w1, w2):
        if s.get("saturated"):        # profile-fit mags unreliable: no vote
            continue
        if (s.get("n_visits", 0) >= 4 and s.get("chi2_red", 0) >= 10.0
                and s.get("amplitude", 0) >= 0.10):
            return True
    return False


def fit_motion(visits, ref_ra, ref_dec):
    """N3: weighted linear proper-motion fit to the visit positions (mas/yr)."""
    if len(visits) < 4:
        return None
    t = np.array([v["year"] for v in visits])
    if t.max() - t.min() < 3.0:
        return None
    cosd = math.cos(math.radians(ref_dec))
    x = np.array([(v["ra"] - ref_ra) * cosd * 3.6e6 for v in visits])
    y = np.array([(v["dec"] - ref_dec) * 3.6e6 for v in visits])
    (px, _), cx = np.polyfit(t, x, 1, cov=True)
    (py, _), cy = np.polyfit(t, y, 1, cov=True)
    ex, ey = math.sqrt(cx[0, 0]), math.sqrt(cy[0, 0])
    total = math.hypot(px, py)
    err = math.hypot(px * ex, py * ey) / total if total > 0 else max(ex, ey)
    px, py = float(px), float(py)
    return {"pm_ra_masyr": round(px, 1), "pm_dec_masyr": round(py, 1),
            "pm_ra_err": round(ex, 1), "pm_dec_err": round(ey, 1),
            "pm_total_masyr": round(total, 1), "pm_err_masyr": round(err, 1),
            "pm_significant": bool(total > 5 * err and total > 100)}


def summarise(visits, ref_ra, ref_dec):
    good = [v for v in visits if v["n"] >= MIN_EXPOSURES_PER_VISIT]
    w1, w2 = band_stats(good, "w1"), band_stats(good, "w2")
    out = {"n_visits": len(good), "n_visits_all": len(visits), "w1": w1, "w2": w2,
           "variable": is_variable(w1, w2)}
    motion = fit_motion(good, ref_ra, ref_dec)
    if motion:
        out.update(motion)
    return out


# ---------------------------------------------------------------------------
# Per target
# ---------------------------------------------------------------------------
def target_cones(meta_block):
    """
    Cones for the target. A fast mover gets one cone per half year along its
    track (radius padded by a quarter year of motion), and rows are later
    kept only near the predicted position at their own epoch.
    """
    pm = math.hypot(meta_block.get("pm_ra") or 0, meta_block.get("pm_dec") or 0)
    if pm < 100:
        return [{"ra": meta_block["ra"], "dec": meta_block["dec"],
                 "radius_arcsec": TARGET_RADIUS_ARCSEC, "year": None}]
    cones = []
    for yr in TRACK_YEARS:
        ra, dec = target_position(meta_block, year_to_mjd(yr))
        cones.append({"ra": ra, "dec": dec, "year": float(yr),
                      "radius_arcsec": TARGET_RADIUS_ARCSEC + pm * 0.3 / 1e3})
    return cones


def moving_match(det, min_pm=100.0):
    """Motion block of the fast-moving catalogue star a detection matched."""
    from catalog_tools import source_pm, CATALOG_EPOCH_MJD
    for cat in ("gaia", "wise"):
        m = (det.get("matches") or {}).get(cat)
        if not m:
            continue
        src = m["source"]
        pm = source_pm(cat, src)
        if pm and math.hypot(*pm) >= min_pm:
            return {"ra": src.get("ra_cat", src["ra"]), "dec": src.get("dec_cat", src["dec"]),
                    "pm_ra": pm[0], "pm_dec": pm[1],
                    "pos_epoch": mjd_to_year(CATALOG_EPOCH_MJD[cat])}
    return None


def run_target(name, det_radius=DETECTION_RADIUS_ARCSEC, dashboard=None, meta=None,
               strict=False):
    import app as portal

    meta = meta or portal.load_target(name)
    if meta is None:
        print(f"  {name}: no cache, skipped")
        return None
    print(f"\n=== {name} ===")
    dash = dashboard if dashboard is not None else portal.get_dashboard(target=name)
    if not isinstance(dash, dict):
        print(f"  dashboard failed: {getattr(dash, 'body', dash)}")
        return None

    block = portal.target_motion_block(name, meta)
    entries, positions = [], []

    def add(label, kind, cones, ref_ra, ref_dec, track=None):
        idx = len(entries)
        entries.append({"label": label, "kind": kind, "ra": ref_ra,
                        "dec": ref_dec, "cones": cones, "points": [],
                        "track": track})
        for c in cones:
            positions.append({"pid": len(positions), "entry": idx, **c})

    add(block.get("common", name), "target", target_cones(block),
        block["ra"], block["dec"], block)
    for i, det in enumerate(dash.get("detections", [])):
        mover = moving_match(det)
        if mover is not None:
            # A detection on a fast catalogued star: follow that star's track,
            # or a fixed cone only catches it passing through (fake fading).
            add(f"det{i}", "detection", target_cones(mover),
                det["ra"], det["dec"], mover)
        else:
            add(f"det{i}", "detection",
                [{"ra": det["ra"], "dec": det["dec"], "radius_arcsec": det_radius,
                  "year": None}], det["ra"], det["dec"])

    by_pid = {p["pid"]: p for p in positions}
    for key, spec in TABLES.items():
        for row in query_positions(positions, key, strict=strict):
            pos = by_pid.get(int(row.get("pid", -1)))
            if pos is None or not good_exposure(row, key):
                continue
            mjd, ra, dec = _f(row.get("mjd")), _f(row.get("ra")), _f(row.get("dec"))
            if None in (mjd, ra, dec):
                continue
            entry = entries[pos["entry"]]
            if pos.get("year") is not None:
                # track cone: keep only rows near the prediction for their epoch
                if abs(mjd_to_year(mjd) - pos["year"]) > 0.25:
                    continue
                p_ra, p_dec = target_position(entry["track"], mjd)
                if sep_arcsec(ra, dec, p_ra, p_dec) > TARGET_RADIUS_ARCSEC:
                    continue
            entry["points"].append({
                "mjd": mjd, "ra": ra, "dec": dec, "source": key,
                "w1": _f(row.get(spec["w1"])), "w1sig": _f(row.get(spec["w1sig"])),
                "w2": _f(row.get(spec["w2"])), "w2sig": _f(row.get(spec["w2sig"]))})

    results = []
    for e in entries:
        seen, pts = set(), []
        for p in e["points"]:                 # overlapping track cones
            k = (round(p["mjd"], 6), round(p["ra"], 6))
            if k not in seen:
                seen.add(k)
                pts.append(p)
        visits = group_visits(pts)
        summary = summarise(visits, e["ra"], e["dec"])
        results.append({"label": e["label"], "kind": e["kind"],
                        "ra": e["ra"], "dec": e["dec"],
                        "n_exposures": len(pts), "visits": visits,
                        "summary": summary})
        flag = "VARIABLE" if summary["variable"] else "stable"
        pm = (f", pm {summary['pm_total_masyr']:.0f}+-{summary['pm_err_masyr']:.0f} mas/yr"
              if summary.get("pm_total_masyr") is not None else "")
        print(f"  {e['label']:<24} {len(pts):4d} exp, {len(visits):2d} visits, {flag}{pm}")

    record = {"target": name,
              "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "tables": [t["table"] for t in TABLES.values()],
              "detection_radius_arcsec": det_radius,
              "positions": results}
    path = os.path.join(CACHE, f"{name}_neowise.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=1)
    print(f"  wrote {path}")
    return record


def main():
    parser = argparse.ArgumentParser(description="NEOWISE/AllWISE history per target.")
    parser.add_argument("--targets", nargs="*", default=None)
    parser.add_argument("--radius", type=float, default=DETECTION_RADIUS_ARCSEC,
                        help="cone radius around each detection, arcsec")
    args = parser.parse_args()

    names = args.targets or sorted(
        f[:-len("_meta.json")] for f in os.listdir(CACHE) if f.endswith("_meta.json"))
    for name in names:
        run_target(name, det_radius=args.radius)


if __name__ == "__main__":
    main()
