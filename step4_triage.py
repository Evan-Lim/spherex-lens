"""
step4_triage.py

Given a detection from step3_zogy and the catalogs downloaded by
step1_ingest, compute the sky position of the detection and find the
nearest source in each catalog. If any catalog has a match within
MATCH_RADIUS_ARCSEC, issue a "KNOWN" verdict; otherwise issue a
"CANDIDATE" verdict (source requires follow-up).
"""

import numpy as np

MATCH_RADIUS_ARCSEC = 30.0
TIGHT_RADIUS_ARCSEC = 5.0


def pixel_to_sky(py, px, ra_center, dec_center, npix, pix_scale_arcsec):
    """
    Convert pixel coordinates to sky coordinates using a flat-sky
    tangent projection approximation.
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


def triage(detection, ra_center, dec_center, npix, pix_scale_arcsec, catalogs):
    """
    Cross-match a detection against the catalogs and return a verdict.
    """
    pos = detection.get("pos_plus") or detection.get("pos")
    py, px = pos
    ra, dec = pixel_to_sky(py, px, ra_center, dec_center, npix, pix_scale_arcsec)

    matches = {}
    for cat_name, sources in catalogs.items():
        best = None
        best_sep = 1e9
        for s in sources:
            s_ra = s.get("ra")
            s_dec = s.get("dec")
            if s_ra is None or s_dec is None:
                continue
            sep = angular_separation_arcsec(ra, dec, s_ra, s_dec)
            if sep < best_sep:
                best_sep = sep
                best = s
        if best is not None and best_sep < MATCH_RADIUS_ARCSEC:
            matches[cat_name] = {"source": best, "sep_arcsec": float(best_sep)}

    if not matches:
        verdict = "CANDIDATE"
        confidence = "high"
    else:
        verdict = "KNOWN"
        if any(m["sep_arcsec"] < TIGHT_RADIUS_ARCSEC for m in matches.values()):
            confidence = "high"
        else:
            confidence = "medium"

    return {
        "ra": float(ra),
        "dec": float(dec),
        "matches": matches,
        "verdict": verdict,
        "confidence": confidence,
    }


if __name__ == "__main__":
    fake_catalogs = {
        "gaia": [{"ra": 133.795, "dec": -7.245}],
        "wise": [{"ra": 133.795, "dec": -7.245}],
        "vsx": [],
    }
    fake_detection = {"pos_plus": (90, 90), "snr": 28.0}
    result = triage(fake_detection, 133.795129, -7.245147, 180, 3.0, fake_catalogs)
    print(f"Verdict: {result['verdict']}, confidence: {result['confidence']}")
