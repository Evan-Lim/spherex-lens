"""
Phase 6 -- combined evidence from several sources.

For every detection this enricher writes ``detection["evidence"]``:

    {"score": 0..1 or None, "parts": [{"name", "value", "weight", "detail"}]}

``score`` answers "how believable is it that something real changed here?".
It is a weighted mean of the parts that could be evaluated (a part whose
value is None -- not applicable / data unavailable -- carries no weight, and
is still listed so the UI can say why).

Parts
-----
ml                  calibrated real/bogus probability (spherexlens.ml.serve)
signal              detection significance above the threshold
motion_consistency  movers only: does the SPHEREx motion (minus -> plus lobe
                    over the epoch baseline) agree with the motion measured by
                    NEOWISE 2010-2024 at that position, and/or the matched
                    Gaia/AllWISE proper motion?  value = chi-square(2 dof)
                    tail probability of the vector difference.
asteroid_check      a known asteroid at the right place and time explains the
                    detection: it is real (value 1) -- just not new.
neossat             visible-light confirmation from Canada's NEOSSat when the
                    CADC archive has a frame at the position near the epoch:
                    seen -> 1.0, frame exists but not seen -> 0.35 (cool
                    infrared objects are often invisible in the optical, so
                    absence is weak evidence).  Only the strongest few
                    candidates per field are checked, time-boxed.

The weights are deliberately simple and documented here; they are heuristics,
not fitted, and the UI shows every part.
"""
import math
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import numpy as np

import config
from spherexlens.enrich import register

WEIGHTS = {"ml": 0.40, "signal": 0.15, "motion_consistency": 0.25,
           "asteroid_check": 0.10, "neossat": 0.10}
NEOSSAT_MAX_CHECKS = 3
NEOSSAT_TIME_BOX_S = 8.0
NEOSSAT_WINDOW_DAYS = 30.0
INTERESTING = {"CANDIDATE", "POSSIBLE_UNKNOWN_ASTEROID"}


# --------------------------------------------------------------------------
# parts
# --------------------------------------------------------------------------
def part_ml(det):
    ml = det.get("ml") or {}
    p = ml.get("real_bogus")
    if p is None:
        return None, "no ML model loaded"
    return float(p), f"real/bogus model {ml.get('model_version', '')}: {p:.0%} real"


def part_signal(det):
    snr = det.get("snr")
    if snr is None:
        return None, "no significance"
    thr = config.DETECTION_SIGMA
    # 0.5 at the threshold, ~0.95 at threshold + 3 sigma
    value = 1.0 / (1.0 + math.exp(-(float(snr) - thr)))
    return value, f"{snr:.1f} sigma vs a {thr:.1f} sigma threshold"


def spherex_motion(det, wcs, dt_years):
    """(pm_ra*, pm_dec, err) in mas/yr from the dipole lobes, or None."""
    if det.get("kind") != "dipole" or not det.get("position_minus") or not dt_years:
        return None
    (y_new, x_new), (y_old, x_old) = det["position"], det["position_minus"]
    try:
        ra_n, dec_n = (float(v) for v in wcs.pixel_to_world_values(float(x_new), float(y_new)))
        ra_o, dec_o = (float(v) for v in wcs.pixel_to_world_values(float(x_old), float(y_old)))
    except Exception:
        return None
    cosd = math.cos(math.radians(dec_o))
    dra = ((ra_n - ra_o + 180) % 360 - 180) * cosd * 3.6e6
    ddec = (dec_n - dec_o) * 3.6e6
    # centroid error of each lobe ~ FWHM / SNR, two lobes in quadrature
    fwhm_mas = (det.get("psf_fwhm_arcsec") or 7.5) * 1e3
    err = math.sqrt(2) * fwhm_mas / max(float(det.get("snr") or 5), 1.0) / dt_years
    return dra / dt_years, ddec / dt_years, max(err, 50.0)


def reference_motions(det, ctx):
    """Independent motion measurements at this position: NEOWISE, Gaia/AllWISE."""
    refs = []
    neowise = ctx.get("neowise")
    if neowise and det.get("ra") is not None:
        try:
            from app import neowise_at          # lazy: app imports us
            n = neowise_at(neowise, det["ra"], det["dec"],
                           radius_arcsec=config.NATIVE_PIXEL_ARCSEC * 2)
        except Exception:
            n = None
        if n and n.get("pm_ra_masyr") is not None:
            refs.append(("NEOWISE 2010-2024", n["pm_ra_masyr"], n["pm_dec_masyr"],
                         math.hypot(n.get("pm_ra_err") or 50, n.get("pm_dec_err") or 50)
                         / math.sqrt(2)))
    try:
        from catalog_tools import source_pm
    except Exception:
        source_pm = None
    for cat, label in (("gaia", "Gaia DR3"), ("wise", "AllWISE")):
        m = (det.get("matches") or {}).get(cat)
        if not m or source_pm is None:
            continue
        pm = source_pm(cat, m["source"])
        if pm and math.hypot(*pm) > 0:
            err = 1.0 if cat == "gaia" else 30.0
            refs.append((label, pm[0], pm[1], err))
    return refs


def part_motion(det, ctx):
    dt = None
    if det.get("mjd_a") and det.get("mjd_b"):
        dt = (det["mjd_b"] - det["mjd_a"]) / 365.25
    sx = spherex_motion(det, ctx.get("wcs"), dt) if ctx.get("wcs") is not None else None
    if sx is None:
        return None, "not a mover (no dipole)" if det.get("kind") != "dipole" \
            else "epoch dates unavailable"
    refs = reference_motions(det, ctx)
    if not refs:
        return None, (f"SPHEREx motion {math.hypot(sx[0], sx[1]):.0f} mas/yr; "
                      "no independent motion measured here (new mover?)")
    best = None
    for name, pra, pdec, perr in refs:
        sig = math.hypot(sx[2], perr)
        chi2 = ((sx[0] - pra) ** 2 + (sx[1] - pdec) ** 2) / sig ** 2
        p = math.exp(-chi2 / 2)                        # chi-square tail, 2 dof
        if best is None or p > best[0]:
            best = (p, name, math.sqrt(chi2), math.hypot(pra, pdec))
    p, name, nsig, ref_total = best
    return p, (f"SPHEREx {math.hypot(sx[0], sx[1]):.0f} mas/yr vs {name} "
               f"{ref_total:.0f} mas/yr: agree within {nsig:.1f} sigma")


def part_asteroid(det):
    a = det.get("asteroid") or {}
    st = a.get("status")
    if st == "match":
        m = a.get("match") or {}
        return 1.0, f"explained by known asteroid {m.get('designation', '')}"
    if st == "unavailable":
        return None, "asteroid check unavailable"
    if st == "no_match":
        return None, "no known asteroid here (neither for nor against)"
    return None, "asteroid check not run"


def neossat_check(det):
    """Coverage + (if possible) a stamp check; returns the detection['neossat'] block."""
    try:
        from spherexlens.ingest.cadc import CADCNeossat
    except Exception:
        return {"status": "unavailable", "n_frames": 0, "frames": [],
                "detail": "NEOSSat adapter not installed"}
    t_mid = det.get("mjd_b") or det.get("mjd_a")
    if t_mid is None or det.get("ra") is None:
        return {"status": "none", "n_frames": 0, "frames": []}
    adapter = CADCNeossat()
    try:
        res = adapter.fetch(det["ra"], det["dec"], 0.25,
                            t_start=t_mid - NEOSSAT_WINDOW_DAYS,
                            t_end=t_mid + NEOSSAT_WINDOW_DAYS)
    except Exception as e:
        return {"status": "unavailable", "n_frames": 0, "frames": [], "error": str(e)}
    frames = [{"obs_id": r.get("obs_id"), "mjd": r.get("mjd"),
               "url": r.get("access_url") or r.get("url")} for r in res.rows][:10]
    if not frames:
        return {"status": "none", "n_frames": 0, "frames": []}
    block = {"status": "frames", "n_frames": len(res.rows), "frames": frames}
    try:
        from spherexlens.ingest import neossat_clean
        hdul = adapter.download_frame(res.rows[0])
        image, mask, meta = neossat_clean.clean_frame(hdul)
        st = neossat_clean.stamp(image, meta.get("wcs"), det["ra"], det["dec"], 15)
        detected = bool(st.get("detected")) if isinstance(st, dict) else bool(st[1])
        block["status"] = "confirmed" if detected else "not_seen"
    except Exception as e:
        block["detail"] = f"frame not checked: {type(e).__name__}"
    return block


def part_neossat(block):
    st = (block or {}).get("status")
    if st == "confirmed":
        return 1.0, "seen in visible light by NEOSSat"
    if st == "not_seen":
        return 0.35, "NEOSSat frame exists but nothing visible (weak evidence)"
    if st == "frames":
        return None, f"{block.get('n_frames')} NEOSSat frame(s) nearby, not checked"
    if st == "unavailable":
        return None, "NEOSSat check unavailable"
    return None, "no NEOSSat coverage"


def combine(parts):
    num = den = 0.0
    for p in parts:
        if p["value"] is None:
            continue
        num += p["weight"] * p["value"]
        den += p["weight"]
    return round(num / den, 3) if den > 0 else None


# --------------------------------------------------------------------------
# enricher
# --------------------------------------------------------------------------
@register("evidence", order=60)
def enrich_evidence(payload, ctx):
    dets = payload.get("detections") or []
    fwhm = (payload.get("psf") or {}).get("fwhm_arcsec")

    # NEOSSat: only the strongest few interesting detections, time-boxed.
    todo = sorted((d for d in dets if d.get("category") in INTERESTING),
                  key=lambda d: -(d.get("snr") or 0))[:NEOSSAT_MAX_CHECKS]
    if todo and not ctx.get("skip_neossat"):
        deadline = time.time() + NEOSSAT_TIME_BOX_S
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            futs = {id(d): (d, pool.submit(neossat_check, d)) for d in todo}
            for d, fut in futs.values():
                try:
                    d["neossat"] = fut.result(timeout=max(0.1, deadline - time.time()))
                except FutureTimeout:
                    d["neossat"] = {"status": "unavailable", "n_frames": 0,
                                    "frames": [], "error": "time-boxed"}
                except Exception as e:
                    d["neossat"] = {"status": "unavailable", "n_frames": 0,
                                    "frames": [], "error": str(e)}

    for d in dets:
        if fwhm and "psf_fwhm_arcsec" not in d:
            d["psf_fwhm_arcsec"] = fwhm
        parts = []
        for name, (value, detail) in (
                ("ml", part_ml(d)),
                ("signal", part_signal(d)),
                ("motion_consistency", part_motion(d, ctx)),
                ("asteroid_check", part_asteroid(d)),
                ("neossat", part_neossat(d.get("neossat")))):
            parts.append({"name": name,
                          "value": None if value is None else round(float(value), 3),
                          "weight": WEIGHTS[name], "detail": detail})
        d["evidence"] = {"score": combine(parts), "parts": parts}
