"""
spherexlens.eval.answer_key  (Phase 4.3)
Real-sky positives from each field's own catalogues.

* high_pm_star  -- Gaia stars (G <= ANSWER_KEY_GAIA_GMAG_MAX) whose catalogued
                   motion moves them >= ANSWER_KEY_MIN_DISPLACEMENT_ARCSEC (one
                   native SPHEREx pixel) between the two epochs.  Recovered when
                   a "dipole" has its positive lobe within ANSWER_KEY_MATCH_PX of
                   the star's epoch-B position (or its negative lobe at epoch A).
* vsx_variable  -- VSX variables with catalogued amplitude
                   >= ANSWER_KEY_VSX_AMPLITUDE_MIN and brightest magnitude
                   <= ANSWER_KEY_VSX_MAG_MAX.  Recovered by any non-dipole
                   detection within ANSWER_KEY_MATCH_PX.  NB: a catalogued
                   variable need not have changed between two given epochs, so
                   this recall is a lower bound on the pipeline's sensitivity.
* asteroid      -- detections the asteroid enricher matched to a known object
                   (``asteroid.status == "match"``).  They are positives by
                   construction (recall 1 among themselves); they are counted
                   here and used as ML labels.

Pure functions; no module-level app import.
"""
import math

import numpy as np

import config
from catalog_tools import CATALOG_EPOCH_MJD, harmonise_motions, propagate, source_pm, vsx_amplitude
from spherexlens.eval import harness


def _inside(x, y, npix, valid=None, margin=None):
    margin = config.EVAL_EDGE_MARGIN_PX if margin is None else margin
    if not (margin <= x < npix - margin and margin <= y < npix - margin):
        return False
    if valid is not None and not valid[int(round(y)), int(round(x))]:
        return False
    return True


def build_answer_key(catalogs, mjd_a, mjd_b, wcs, npix, valid=None):
    """List of answer-key objects for one epoch pair."""
    key = []
    if not catalogs:
        return key
    if mjd_a and mjd_b and mjd_b > mjd_a:
        dt = (mjd_b - mjd_a) / 365.25
        cats = harmonise_motions(catalogs)
        for s in cats.get("gaia") or []:
            g = s.get("gmag")
            if g is None or g > config.ANSWER_KEY_GAIA_GMAG_MAX:
                continue
            pm = source_pm("gaia", s)
            if pm is None:
                continue
            disp = math.hypot(*pm) * dt / 1e3
            if disp < config.ANSWER_KEY_MIN_DISPLACEMENT_ARCSEC:
                continue
            ref = CATALOG_EPOCH_MJD["gaia"]
            a = propagate(s["ra"], s["dec"], pm[0], pm[1], (mjd_a - ref) / 365.25)
            b = propagate(s["ra"], s["dec"], pm[0], pm[1], (mjd_b - ref) / 365.25)
            try:
                xa, ya = (float(v) for v in wcs.world_to_pixel_values(*a))
                xb, yb = (float(v) for v in wcs.world_to_pixel_values(*b))
            except Exception:
                continue
            if not (_inside(xa, ya, npix, valid) and _inside(xb, yb, npix, valid)):
                continue
            key.append({"class": "high_pm_star", "name": s.get("source_id"),
                        "mag": g, "ra": b[0], "dec": b[1], "x_a": xa, "y_a": ya,
                        "x_b": xb, "y_b": yb, "displacement_arcsec": round(disp, 2)})
    for s in catalogs.get("vsx") or []:
        try:
            amp, _ = vsx_amplitude(s)
        except Exception:
            amp = None
        bright = s.get("max")
        if amp is None or amp < config.ANSWER_KEY_VSX_AMPLITUDE_MIN:
            continue
        if bright is None or float(bright) > config.ANSWER_KEY_VSX_MAG_MAX:
            continue
        try:
            x, y = (float(v) for v in wcs.world_to_pixel_values(s["ra"], s["dec"]))
        except Exception:
            continue
        if not _inside(x, y, npix, valid):
            continue
        key.append({"class": "vsx_variable", "name": s.get("name"), "mag": bright,
                    "amplitude": amp, "ra": s["ra"], "dec": s["dec"],
                    "x_a": x, "y_a": y, "x_b": x, "y_b": y})
    return key


def _near(d, key, x, y, tol):
    p = d.get(key)
    if p is None:
        return False
    return math.hypot(float(p[1]) - x, float(p[0]) - y) <= tol


def match_object(obj, detections, tol=None):
    """The detection that recovers `obj`, or None."""
    tol = config.ANSWER_KEY_MATCH_PX if tol is None else tol
    best = None
    for d in detections:
        kind = d.get("kind")
        if obj["class"] == "high_pm_star":
            if kind != "dipole":
                continue
            ok = (_near(d, "pos_plus", obj["x_b"], obj["y_b"], tol)
                  or _near(d, "position", obj["x_b"], obj["y_b"], tol)
                  or _near(d, "pos_minus", obj["x_a"], obj["y_a"], tol)
                  or _near(d, "position_minus", obj["x_a"], obj["y_a"], tol))
        else:
            if kind == "dipole":
                continue
            p = harness.detection_position(d)
            ok = p is not None and math.hypot(p[1] - obj["x_b"], p[0] - obj["y_b"]) <= tol
        if ok and (best is None or abs(d.get("snr", 0)) > abs(best.get("snr", 0))):
            best = d
    return best


def score(key, detections, asteroid_detections=()):
    """Recall overall and by class.  Mutates key items with detected/snr."""
    for obj in key:
        m = match_object(obj, detections)
        obj["detected"] = m is not None
        obj["det_snr"] = round(float(m["snr"]), 2) if m is not None else None
        obj["_match"] = m
    for d in asteroid_detections:
        key.append({"class": "asteroid", "name": ((d.get("asteroid") or {}).get("match") or {})
                    .get("designation"), "detected": True, "_match": d,
                    "det_snr": d.get("snr")})
    return summarise([key])


def summarise(keys, seed=None, n_boot=None):
    items = [o for k in keys for o in k]
    # asteroids are positives by construction: listed per class, not in recall
    scored = [o for o in items if o["class"] != "asteroid"]
    n = len(scored)
    hits = np.array([1.0 if o["detected"] else 0.0 for o in scored])
    by_class, by_class_n = {}, {}
    for c in sorted({o["class"] for o in items}):
        sel = [o for o in items if o["class"] == c]
        by_class[c] = round(sum(o["detected"] for o in sel) / len(sel), 4)
        by_class_n[c] = len(sel)
    out = {"n": n, "n_detected": int(hits.sum()),
           "recall": round(float(hits.mean()), 4) if n else None,
           "by_class": by_class, "by_class_n": by_class_n,
           "note": "vsx recall is a lower bound (a catalogued variable need not have "
                   "changed between the two epochs); asteroids excluded from recall"}
    if n >= 2:
        rng = np.random.default_rng(config.ML_RANDOM_SEED if seed is None else seed)
        nb = int(n_boot or config.EVAL_BOOTSTRAP)
        boots = [rng.choice(hits, n).mean() for _ in range(nb)]
        out["ci95"] = [round(float(np.percentile(boots, 2.5)), 4),
                       round(float(np.percentile(boots, 97.5)), 4)]
    return out
