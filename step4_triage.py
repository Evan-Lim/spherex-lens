"""
step4_triage.py

Given a detection from step3_zogy and the catalogs downloaded by
step1_ingest, compute the sky position of the detection and find the
nearest source in each catalog. If any catalog has a match, issue a "KNOWN"
verdict; otherwise issue a "CANDIDATE" verdict (source requires follow-up).

On top of that yes/no answer every detection gets a *category* saying why it
changed, and how interesting that is:

    KNOWN_VARIABLE        matched to a VSX variable, a Gaia-flagged variable or
                          a NEOWISE light curve that already varied -- expected
    KNOWN_MOVER           a dipole on a star whose catalogued motion predicts
                          it -- expected (unless the motion check disagrees)
    KNOWN_STATIC_CHANGED  matched only to catalogue entries that are *not*
                          known to vary or move -- a catalogued star doing
                          something new: high priority
    BRIGHT_STAR_ARTEFACT  inside the mask of a star that saturates SPHEREx, or
                          on the bleed/crosstalk cross along the detector axes
                          of such a star (context["spike"])
    MASK_ARTEFACT         unexplained by any catalogue AND no detector band is
                          unmasked in every epoch at that spot, or it sits on
                          the edge of an exposure footprint (context["edge"])
    CANDIDATE             in no catalogue at all -- high priority

`verdict` stays KNOWN / CANDIDATE so existing callers keep working.
Catalogues passed in should already be propagated to the detection epoch
(catalog_tools.propagate_catalogs); app.py does that per epoch pair.
"""

import math

import numpy as np

from catalog_tools import (match_radius, bright_mask_radius, wise_reliable,
                           wise_colour_class, decode_vsx_type, vsx_amplitude,
                           aavso_url, gaia_distance, source_pm, source_mag,
                           chance_match_probability,
                           MAX_MATCH_ARCSEC, MAX_MASK_ARCSEC)

CHANCE_WARN = 0.3            # flag a match this likely to be a coincidence

MAX_SEARCH_ARCSEC = max(MAX_MATCH_ARCSEC, MAX_MASK_ARCSEC)

MATCH_RADIUS_ARCSEC = 30.0
TIGHT_RADIUS_ARCSEC = 5.0

# A dipole's two lobes are only separated by the true motion when the motion
# exceeds the PSF; below that the separation is set by the PSF itself.
# Fallback only: app.py passes the measured co-add PSF FWHM (from the cutouts'
# EPSF tables) as context["psf_fwhm_arcsec"].
PSF_FWHM_ARCSEC = 2.355 * 1.4 * 3.0          # psf_sigma 1.4 px on the 3" grid
MOVER_MIN_DISPLACEMENT_ARCSEC = 2.0
AMPLITUDE_SLACK_MAG = 0.3                    # V3: allowance on VSX amplitude

PRIORITY = {"CANDIDATE": 1, "KNOWN_STATIC_CHANGED": 1, "KNOWN_MOVER": 3,
            "KNOWN_VARIABLE": 3, "BRIGHT_STAR_ARTEFACT": 4, "MASK_ARTEFACT": 4}


def pixel_to_sky(py, px, ra_center, dec_center, npix, pix_scale_arcsec):
    """
    Convert pixel coordinates to sky coordinates using a flat-sky
    tangent projection approximation (fallback when no WCS is given).
    """
    ra = ra_center - (px - npix / 2.0) * pix_scale_arcsec / 3600.0
    dec = dec_center + (npix / 2.0 - py) * pix_scale_arcsec / 3600.0
    return ra, dec


def angular_separation_arcsec(ra1, dec1, ra2, dec2):
    """
    Approximate angular separation for small fields.
    """
    dra = (ra1 - ra2) * np.cos(np.radians(dec1))
    ddec = dec1 - dec2
    return np.hypot(dra, ddec) * 3600.0


def _pixel_to_world(wcs, py, px, fallback):
    if wcs is None:
        return fallback()
    try:
        ra, dec = wcs.pixel_to_world_values(float(px), float(py))
        return float(ra), float(dec)
    except Exception:
        return fallback()


def triage(detection, ra_center, dec_center, npix, pix_scale_arcsec, catalogs,
           wcs=None, context=None):
    """
    Cross-match a detection against the catalogs and return a verdict.

    wcs      -- target-plane WCS; uses the exact projection instead of the
                flat-sky approximation when given.
    context  -- optional extras: baseline_years (of this detection's epoch
                pair), delta_mag (SPHEREx brightness change), neowise (the
                step7 summary at this position), psf_fwhm_arcsec (measured
                PSF), spike (spherexlens.imaging.spike_hit result), edge
                ({"n_exposures", "edge_px"}: on an exposure footprint edge).
    """
    ctx = context or {}
    psf_fwhm = ctx.get("psf_fwhm_arcsec") or PSF_FWHM_ARCSEC
    pos = detection.get("pos_plus") or detection.get("pos")
    py, px = pos
    ra, dec = _pixel_to_world(
        wcs, py, px,
        lambda: pixel_to_sky(py, px, ra_center, dec_center, npix, pix_scale_arcsec))

    matches, rejected = {}, {}
    bright = None
    movers_near = []
    for cat_name, sources in (catalogs or {}).items():
        # Rank by separation / that source's own radius, not raw separation:
        # a G=8 star 12" away (radius 42") must beat a G=21 star 11" away
        # (radius 30") -- the bright one is what changes at SNR 50.
        best, best_sep, best_score = None, 1e9, 1e9
        for s in sources or []:
            s_ra, s_dec = s.get("ra"), s.get("dec")
            if s_ra is None or s_dec is None:
                continue
            sep = angular_separation_arcsec(ra, dec, s_ra, s_dec)
            if sep > MAX_SEARCH_ARCSEC:          # beyond every radius below
                continue

            mask = bright_mask_radius(cat_name, s)                    # G4
            if mask is not None and sep < mask:
                if bright is None or (source_mag(cat_name, s) or 99) < bright["mag"]:
                    bright = {"cat": cat_name, "sep_arcsec": round(float(sep), 2),
                              "mag": source_mag(cat_name, s) or 99,
                              "mask_arcsec": round(mask, 1), "source": s}

            radius = match_radius(cat_name, s)                       # G8
            if sep >= radius:
                continue
            pm = source_pm(cat_name, s)
            if pm:
                # every moving star in range, not just the best positional
                # match: a saturated mover's dipole lobes sit out in its
                # wings, where a faint static star can be "closer"
                movers_near.append((cat_name, s, math.hypot(*pm)))
            if cat_name == "wise" and not wise_reliable(s):           # W3
                if cat_name not in rejected or sep < rejected[cat_name]["sep_arcsec"]:
                    rejected[cat_name] = {"sep_arcsec": round(float(sep), 2),
                                          "cc_flags": s.get("cc_flags"),
                                          "ph_qual": s.get("ph_qual")}
                continue
            if sep / radius < best_score:
                best, best_sep, best_score = s, sep, sep / radius
        if best is not None:
            radius = match_radius(cat_name, best)
            matches[cat_name] = {
                "source": best, "sep_arcsec": float(best_sep),
                "radius_arcsec": round(radius, 1),
                "p_chance": round(chance_match_probability(
                    cat_name, best, sources, radius), 3)}

    result = {
        "ra": float(ra),
        "dec": float(dec),
        "matches": matches,
    }
    reasons = []

    # ---- per-catalogue detail ------------------------------------------------
    vsx_m, gaia_m, wise_m = matches.get("vsx"), matches.get("gaia"), matches.get("wise")
    baseline_yr = ctx.get("baseline_years")
    delta_mag = ctx.get("delta_mag")

    vsx_info = None
    if vsx_m:                                                          # V2-V4
        src = vsx_m["source"]
        label, family = decode_vsx_type(src.get("type"))
        amp, band = vsx_amplitude(src)
        period = src.get("period_d")
        vsx_info = {"name": src.get("name"), "type": src.get("type"),
                    "label": label, "family": family,
                    "amplitude_mag": amp, "passband": band,
                    "period_d": period, "url": aavso_url(src.get("oid"))}
        if period and baseline_yr:
            vsx_info["cycles_in_baseline"] = round(baseline_yr * 365.25 / period, 2)
        if amp is not None and delta_mag is not None:
            exceeds = abs(delta_mag) > amp + AMPLITUDE_SLACK_MAG
            vsx_info["amplitude_check"] = "exceeds" if exceeds else "within"
            if exceeds:
                reasons.append(f"change {abs(delta_mag):.2f} mag exceeds the "
                               f"catalogued {amp:.2f} mag amplitude ({band or '?'}-band)")
        result["vsx"] = vsx_info

    if gaia_m:                                                         # G5, G6
        src = gaia_m["source"]
        info = {"source_id": src.get("source_id"), "gmag": src.get("gmag"),
                "var_flag": src.get("var_flag"), "ruwe": src.get("ruwe"),
                "moved_arcsec": src.get("moved_arcsec")}
        dist = gaia_distance(src)
        if dist:
            info.update(dist)
        result["gaia"] = info

    if wise_m:                                                         # W2
        src = wise_m["source"]
        colour, cls = wise_colour_class(src.get("w1"), src.get("w2"))
        result["wise"] = {"designation": src.get("designation"),
                          "w1": src.get("w1"), "w2": src.get("w2"),
                          "w1_w2": colour, "colour_class": cls,
                          "ph_qual": src.get("ph_qual"), "cc_flags": src.get("cc_flags")}
    if rejected:
        result["rejected_matches"] = rejected
        reasons.append("nearby AllWISE entry ignored: flagged as a likely "
                       "artefact (cc_flags/ph_qual)")

    # ---- motion check for dipoles (G2/G3, W4, N3) ----------------------------
    mover = None
    mover_src = None
    if detection.get("kind") == "dipole":
        if movers_near:
            # the fastest moving star within range explains the dipole best
            cat_name, mover_src, pm_tot = max(movers_near, key=lambda m: m[2])
            mover = (cat_name, pm_tot)
        neo = ctx.get("neowise") or {}
        if mover is None and neo.get("pm_significant"):
            mover = ("neowise", neo.get("pm_total_masyr"))
        if mover is not None and baseline_yr:
            predicted = mover[1] * baseline_yr / 1e3
            measured = detection.get("separation_arcsec")
            check = {"catalogue": mover[0], "pm_masyr": round(mover[1], 1),
                     "baseline_years": round(baseline_yr, 3),
                     "predicted_arcsec": round(predicted, 2),
                     "measured_arcsec": None if measured is None else round(measured, 2)}
            if predicted < MOVER_MIN_DISPLACEMENT_ARCSEC:
                check["status"] = "too slow to explain a dipole"
                mover = None
            elif predicted < psf_fwhm:
                check["status"] = ("unresolved: motion is below the PSF, so the "
                                   "lobe separation reflects the PSF, not the motion")
            elif measured is not None:
                ratio = measured / predicted
                check["ratio"] = round(ratio, 2)
                check["status"] = "consistent" if 0.7 <= ratio <= 1.3 else "inconsistent"
                if check["status"] == "inconsistent":
                    reasons.append(f"dipole separation {measured:.1f}\" vs predicted "
                                   f"{predicted:.1f}\" from {mover[0]} motion")
            result["motion_check"] = check

    # ---- brightening / fading (monopoles) ------------------------------------
    kind = detection.get("kind")
    change = None
    if kind == "monopole_negative":
        change = "got fainter between the epochs"
    elif kind == "monopole":
        change = "got brighter between the epochs"

    # ---- NEOWISE history (N1/N2) ---------------------------------------------
    neo = ctx.get("neowise")
    if neo:
        result["neowise"] = neo
        if neo.get("variable"):
            reasons.append("already varied in NEOWISE (2014-2024)")
        elif neo.get("n_visits"):
            reasons.append(f"stable in NEOWISE over {neo['n_visits']} visits")

    # ---- category --------------------------------------------------------------
    # The co-add uses matched masks (step2_coadd.coadd_matched): a band masked
    # in any epoch is dropped from all epochs at that pixel, so the comparison
    # stays like-for-like. Only when *no* band survives is it an artefact.
    mask = ctx.get("mask") or {}
    lost, n_bands = mask.get("bands_excluded", 0), mask.get("n_bands", 0)
    mask_holes = bool(n_bands) and lost >= n_bands
    mask_text = None
    if lost:
        result["mask"] = mask
        mask_text = (f"no band is unmasked in every epoch here ({lost}/{n_bands} dropped)"
                     if mask_holes else
                     f"{lost} of {n_bands} bands dropped here (masked in some epoch); "
                     "compared on the remaining bands only")
    gaia_var = bool(gaia_m and gaia_m["source"].get("var_flag") == "VARIABLE")
    vsx_close = bool(vsx_m and vsx_m["sep_arcsec"] < 10.0)
    # The saturating star may BE the mover (Barnard's Star: W1 ~ 4.5) -- then
    # its own motion explains the dipole; the mask must not swallow it.
    bright_is_mover = False
    if bright and mover_src is not None:
        ms = mover_src
        bright_is_mover = angular_separation_arcsec(
            ms["ra"], ms["dec"], bright["source"]["ra"], bright["source"]["dec"]) < 6.0
    # Bright-star cross (bleed / readout crosstalk along the detector axes):
    # handled exactly like the circular bright-star mask.
    spike = ctx.get("spike")
    spike_is_mover = False
    if spike and mover_src is not None:
        spike_is_mover = angular_separation_arcsec(
            mover_src["ra"], mover_src["dec"], spike["ra"], spike["dec"]) < 6.0
    on_spike = bool(spike) and not bright and not spike_is_mover
    if on_spike:
        result["spike"] = {k: v for k, v in spike.items() if k not in ("ra", "dec")}
    edge = ctx.get("edge") or {}
    on_edge = bool(edge.get("n_exposures"))
    edge_text = None
    if on_edge:
        result["edge"] = edge
        edge_text = (f"within {edge.get('edge_px', '?')} px of the edge of "
                     f"{edge['n_exposures']} exposure footprint(s): the co-add "
                     "weight jumps there")
    if bright and not vsx_close and not bright_is_mover:
        category = "BRIGHT_STAR_ARTEFACT"
        reasons.insert(0, f"{bright['sep_arcsec']:.0f}\" from a saturating "
                          f"{bright['cat']} star (mag {bright['mag']:.1f}, "
                          f"mask {bright['mask_arcsec']:.0f}\")")
    elif on_spike and not vsx_close:
        category = "BRIGHT_STAR_ARTEFACT"
        reasons.insert(0, f"on the detector-axis cross of a saturating {spike['cat']} "
                          f"star (mag {spike['mag']:.1f}, {spike['sep_arcsec']:.0f}\" "
                          f"away along {spike['angle_deg']:.0f} deg)")
    elif vsx_m or gaia_var or (neo and neo.get("variable")):
        category = "KNOWN_VARIABLE"
        if vsx_info:
            reasons.insert(0, f"VSX {vsx_info['label']}"
                              + (f", P = {vsx_info['period_d']:.1f} d"
                                 if vsx_info.get("period_d") else ""))
        elif gaia_var:
            reasons.insert(0, "Gaia DR3 flags it as photometrically VARIABLE")
        if bright:
            reasons.append("saturated in SPHEREx: photometry unreliable")
    elif mover is not None:
        category = "KNOWN_MOVER"
        reasons.insert(0, f"catalogued motion {mover[1] / 1e3:.2f}\"/yr ({mover[0]})")
        if bright_is_mover:
            reasons.append("the mover saturates SPHEREx: its core is masked, the "
                           "dipole comes from the PSF wings")
    elif mask_holes or on_edge:
        # Nothing in the catalogues explains it and no band is clean in every
        # epoch at this spot (or it sits where an exposure ends): there is
        # nothing like-for-like left to compare.
        category = "MASK_ARTEFACT"
        reasons.insert(0, mask_text if mask_holes else edge_text)
    elif matches:
        category = "KNOWN_STATIC_CHANGED"
        only_ir = set(matches) == {"wise"}
        reasons.insert(0, "catalogued as non-variable, non-moving -- yet it changed"
                          + (" (seen only in the infrared: AllWISE)" if only_ir else ""))
    else:
        category = "CANDIDATE"
        reasons.insert(0, "no catalogue entry within the match radius")

    if mask_text and category != "MASK_ARTEFACT":
        reasons.append(mask_text)
    if edge_text and edge_text not in reasons:
        reasons.append(edge_text)
    if change:
        reasons.insert(1 if reasons else 0, change)

    priority = PRIORITY[category]
    if category == "KNOWN_VARIABLE" and vsx_info and vsx_info.get("amplitude_check") == "exceeds":
        priority = 2
    if category == "KNOWN_MOVER" and (result.get("motion_check") or {}).get("status") == "inconsistent":
        priority = 2

    if category == "CANDIDATE":
        verdict, confidence = "CANDIDATE", "high"
    else:
        verdict = "KNOWN"
        if category in ("BRIGHT_STAR_ARTEFACT", "MASK_ARTEFACT"):
            confidence = "low"
        elif any(m["sep_arcsec"] < TIGHT_RADIUS_ARCSEC for m in matches.values()):
            confidence = "high"
        else:
            confidence = "medium"

    if matches and category != "BRIGHT_STAR_ARTEFACT":
        p_best = min(m["p_chance"] for m in matches.values())
        result["p_chance"] = p_best
        if p_best >= CHANCE_WARN:
            reasons.append(f"match could be a chance alignment (p = {p_best:.2f})")
            confidence = "low"

    if bright:
        result["bright_star"] = {k: v for k, v in bright.items() if k != "source"}
    result.update({"verdict": verdict, "confidence": confidence,
                   "category": category, "priority": priority,
                   "reasons": reasons})
    return result


if __name__ == "__main__":
    fake_catalogs = {
        "gaia": [{"ra": 133.795, "dec": -7.245}],
        "wise": [{"ra": 133.795, "dec": -7.245}],
        "vsx": [],
    }
    fake_detection = {"pos_plus": (90, 90), "snr": 28.0}
    result = triage(fake_detection, 133.795129, -7.245147, 180, 3.0, fake_catalogs)
    print(f"Verdict: {result['verdict']}, confidence: {result['confidence']}, "
          f"category: {result['category']}")
