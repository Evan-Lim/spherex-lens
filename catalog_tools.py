"""
catalog_tools.py

Shared catalogue science used by step1_ingest (what to fetch), step4_triage
(how to judge a detection), app.py (field-level checks) and step7_neowise
(infrared history):

* epochs and proper-motion propagation  (Gaia DR3 = 2016.0, AllWISE ~ 2010.5)
* magnitude-dependent match radius and bright-star masking
* AllWISE reliability (cc_flags / ph_qual) and W1-W2 colour classes
* VSX variability-type decoding, amplitude and AAVSO links
* Gaia parallax -> distance / absolute magnitude
* magnitude -> flux density for the SED panel

Every threshold here is a heuristic constant at the top of its section, so it
can be tuned in one place.
"""

import math

import numpy as np

# ---------------------------------------------------------------------------
# Epochs
# ---------------------------------------------------------------------------
MJD_J2000 = 51544.5
GAIA_DR3_EPOCH_MJD = 57388.5        # J2016.0, the Gaia DR3 reference epoch
ALLWISE_EPOCH_MJD = 55400.0         # ~2010.56, mean AllWISE observation epoch


def mjd_to_year(mjd):
    return 2000.0 + (float(mjd) - MJD_J2000) / 365.25


def year_to_mjd(year):
    return MJD_J2000 + (float(year) - 2000.0) * 365.25


def propagate(ra, dec, pm_ra_masyr, pm_dec_masyr, dt_years):
    """
    Linear proper-motion propagation on the tangent plane.

    pm_ra is mu_alpha* (already multiplied by cos dec), as in Gaia and AllWISE.
    Good to well under an arcsecond for |dt| < 30 yr away from the poles.
    """
    cosd = max(math.cos(math.radians(dec)), 1e-6)
    return (ra + pm_ra_masyr * dt_years / 3.6e6 / cosd,
            dec + pm_dec_masyr * dt_years / 3.6e6)


def sep_arcsec(ra1, dec1, ra2, dec2):
    """Small-angle separation in arcsec (fine below a degree)."""
    cosd = math.cos(math.radians(0.5 * (dec1 + dec2)))
    return math.hypot((ra1 - ra2) * cosd, dec1 - dec2) * 3600.0


GAIA_TWIN_ARCSEC = 3.0             # AllWISE row = Gaia star at the AllWISE epoch
GAIA_TWIN_MIN_PM = 50.0            # only bother for stars that actually move


def harmonise_motions(catalogs):
    """
    Give each AllWISE row that is the same star as a moving Gaia source the
    Gaia motion (far more precise, and right for saturated stars).

    Matching is done at the AllWISE epoch, where both positions refer to the
    same moment. Returns a new dict; rows are copied, never mutated.
    """
    gaia = [s for s in (catalogs or {}).get("gaia") or []
            if math.hypot(s.get("pm_ra") or 0, s.get("pm_de") or 0) >= GAIA_TWIN_MIN_PM]
    wise = (catalogs or {}).get("wise") or []
    if not gaia or not wise:
        return catalogs
    dt = (ALLWISE_EPOCH_MJD - GAIA_DR3_EPOCH_MJD) / 365.25
    out_wise = list(wise)
    index = {}
    for i, w in enumerate(wise):                      # coarse 10" grid
        index.setdefault((int(w["ra"] * 360), int(w["dec"] * 360)), []).append(i)
    for g in gaia:
        ra, dec = propagate(g["ra"], g["dec"], g["pm_ra"], g["pm_de"], dt)
        ci, cj = int(ra * 360), int(dec * 360)
        best, best_sep = None, GAIA_TWIN_ARCSEC
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in index.get((ci + di, cj + dj), ()):
                    s = sep_arcsec(ra, dec, wise[i]["ra"], wise[i]["dec"])
                    if s < best_sep:
                        best, best_sep = i, s
        if best is not None:
            row = dict(out_wise[best])
            row.update({"pm_ra": g["pm_ra"], "pm_de": g["pm_de"], "pm_from": "gaia",
                        "pm_ra_allwise": wise[best].get("pm_ra"),
                        "pm_de_allwise": wise[best].get("pm_de")})
            out_wise[best] = row
    return {**catalogs, "wise": out_wise}


def source_pm(cat, src):
    """(pm_ra, pm_dec) in mas/yr if this catalogue row has a usable motion."""
    pmra, pmde = src.get("pm_ra"), src.get("pm_de")
    if pmra is None or pmde is None or (pmra == 0 and pmde == 0):
        return None
    if cat == "wise":
        # AllWISE's own motions are not usable for this: Orion alone has 627
        # rows "moving" > 1.5"/yr (up to 140"/yr) at formal SNR in the
        # hundreds, and saturated Barnard's Star gets 13.8"/yr instead of
        # Gaia's 10.4"/yr. An AllWISE row moves only with its Gaia twin's
        # motion; faint movers Gaia misses are what step7 (NEOWISE) is for.
        if src.get("pm_from") != "gaia":
            return None
    return float(pmra), float(pmde)


CATALOG_EPOCH_MJD = {"gaia": GAIA_DR3_EPOCH_MJD, "wise": ALLWISE_EPOCH_MJD}


def propagate_catalogs(catalogs, mjd):
    """
    Copy of `catalogs` with every movable row moved to `mjd`.

    Each moved row keeps its catalogue position as ra_cat/dec_cat and records
    moved_arcsec, so the UI can say how far the epoch correction shifted it.
    VSX rows (J2000, no motions) are passed through unchanged.
    """
    if mjd is None:
        return catalogs
    catalogs = harmonise_motions(catalogs)
    out = {}
    for cat, sources in (catalogs or {}).items():
        ref = CATALOG_EPOCH_MJD.get(cat)
        if ref is None:
            out[cat] = sources
            continue
        dt = (float(mjd) - ref) / 365.25
        moved = []
        for s in sources or []:
            pm = source_pm(cat, s)
            if pm is None or s.get("ra") is None:
                moved.append(s)
                continue
            ra, dec = propagate(s["ra"], s["dec"], pm[0], pm[1], dt)
            row = dict(s)
            row.update({"ra": ra, "dec": dec, "ra_cat": s["ra"],
                        "dec_cat": s["dec"],
                        "moved_arcsec": round(math.hypot(*pm) * abs(dt) / 1e3, 2)})
            moved.append(row)
        out[cat] = moved
    return out


def target_position(meta_block, mjd):
    """
    Where the *target itself* is at `mjd`.

    TARGETS entries may carry pm_ra / pm_dec (mas/yr) and pos_epoch (Julian
    year of ra/dec). Without them the target is treated as fixed.
    """
    ra, dec = meta_block["ra"], meta_block["dec"]
    pmra, pmde = meta_block.get("pm_ra"), meta_block.get("pm_dec")
    if mjd is None or not (pmra or pmde):
        return ra, dec
    dt = mjd_to_year(mjd) - float(meta_block.get("pos_epoch", 2000.0))
    return propagate(ra, dec, pmra or 0.0, pmde or 0.0, dt)


# ---------------------------------------------------------------------------
# Match radius and bright-star masking (G4, G8)
# ---------------------------------------------------------------------------
BASE_MATCH_ARCSEC = 30.0          # a G = 9 star; also VSX (coarse positions)
FAINT_MATCH_ARCSEC = 10.0         # G >= 16: ~1.5 native SPHEREx pixels
MAX_MATCH_ARCSEC = 120.0
CATALOG_CONE_ARCMIN = 15.0        # step1_ingest queries 15' cones
BRIGHT_G = 7.0            # Gaia G brighter than this saturates SPHEREx
BRIGHT_W1 = 5.5           # same for AllWISE W1 (stars are ~1.5 mag brighter in W1)
MAX_MASK_ARCSEC = 180.0


def source_mag(cat, src):
    """The magnitude used for radii and sorting: Gaia G, else W1, else VSX max."""
    if cat == "gaia":
        m = src.get("gmag")
    elif cat == "wise":
        m = src.get("w1")
        m = None if m is None else m + 1.5      # rough W1 -> G for a K/M star
    else:
        m = src.get("max")
    return None if m is None or m >= 90 else float(m)


def match_radius(cat, src):
    """
    Match radius in arcsec, by brightness.

    A flat 30" radius is far too generous at Gaia depth: with ~2000 Gaia
    sources in a 15' cone, a random 30" circle holds ~2 of them, so almost
    every detection "matched". Faint sources (which cannot wash out a large
    patch anyway) get 10"; the radius rises to 30" at G = 9 and keeps growing
    for bright stars whose PSF wings spread the change.
    """
    m = source_mag(cat, src)
    if cat == "vsx":
        return BASE_MATCH_ARCSEC
    if m is None or m >= 16.0:
        return FAINT_MATCH_ARCSEC
    if m >= 9.0:
        return FAINT_MATCH_ARCSEC + (16.0 - m) * (BASE_MATCH_ARCSEC - FAINT_MATCH_ARCSEC) / 7.0
    return min(MAX_MATCH_ARCSEC, BASE_MATCH_ARCSEC + 15.0 * (9.0 - m))


def chance_match_probability(cat, src, sources, radius_arcsec):
    """
    Probability that a source at least this bright falls inside the match
    radius by chance, from the catalogue's own density in the queried cone:
    p = 1 - exp(-n(<=m) / A_cone * pi r^2).
    """
    m = source_mag(cat, src)
    if m is None:
        n = len(sources or [])
    else:
        n = sum(1 for s in sources or []
                if (source_mag(cat, s) if source_mag(cat, s) is not None else 99) <= m)
    area = math.pi * (CATALOG_CONE_ARCMIN * 60.0) ** 2
    return 1.0 - math.exp(-n / area * math.pi * radius_arcsec ** 2)


def bright_mask_radius(cat, src):
    """Radius around a saturating star inside which detections are suspect."""
    if cat == "gaia":
        m = src.get("gmag")
        lim = BRIGHT_G
    elif cat == "wise":
        m = src.get("w1")
        lim = BRIGHT_W1
    else:
        return None
    if m is None or m >= lim:
        return None
    return min(MAX_MASK_ARCSEC, 30.0 + 25.0 * (lim - m))


# ---------------------------------------------------------------------------
# AllWISE quality and colour (W2, W3)
# ---------------------------------------------------------------------------
def wise_reliable(src):
    """
    False for AllWISE rows that are probably artefacts.

    cc_flags: upper-case D/P/H/O in W1 or W2 means the source is *likely
    spurious* (diffraction spike, persistence, halo, optical ghost); lower case
    only means contaminated photometry and is kept. ph_qual 'U'/'X' in both W1
    and W2 means no real detection in either band.
    """
    cc = (src.get("cc_flags") or "0000")[:2]
    if any(ch in "DPHO" for ch in cc):
        return False
    ph = (src.get("ph_qual") or "AA")[:2]
    if len(ph) == 2 and ph[0] in "UX" and ph[1] in "UX":
        return False
    return True


def wise_colour_class(w1, w2):
    """
    Coarse class from W1-W2 (Vega).

    Stars sit near 0 (Rayleigh-Jeans); W1-W2 >= 0.8 is the Stern et al. (2012)
    AGN cut, which cool brown dwarfs also pass; >= 2 is only reached by late-T
    and Y dwarfs (and a few extreme dusty objects).
    """
    if w1 is None or w2 is None or w1 >= 90 or w2 >= 90:
        return None, None
    c = round(float(w1) - float(w2), 2)
    if c >= 2.0:
        label = "very red: late-T / Y dwarf likely"
    elif c >= 0.8:
        label = "AGN-like or cool brown dwarf (W1-W2 >= 0.8)"
    elif c >= 0.4:
        label = "red: dusty star / YSO / L-T dwarf"
    else:
        label = "stellar colours"
    return c, label


# ---------------------------------------------------------------------------
# VSX (V1-V4)
# ---------------------------------------------------------------------------
# Prefix -> (description, family). Longest prefix wins, so "SRA" beats "SR".
VSX_TYPES = {
    "M": ("Mira long-period variable", "pulsating-LPV"),
    "MIRA": ("Mira long-period variable", "pulsating-LPV"),
    "LPV": ("long-period variable", "pulsating-LPV"),
    "SR": ("semiregular variable", "pulsating-LPV"),
    "SRA": ("semiregular (SRa)", "pulsating-LPV"),
    "SRB": ("semiregular (SRb)", "pulsating-LPV"),
    "SRC": ("semiregular supergiant (SRc)", "pulsating-LPV"),
    "L": ("slow irregular variable", "pulsating-LPV"),
    "LB": ("slow irregular giant", "pulsating-LPV"),
    "LC": ("slow irregular supergiant", "pulsating-LPV"),
    "DCEP": ("classical Cepheid", "pulsating"),
    "CEP": ("Cepheid", "pulsating"),
    "CW": ("type II Cepheid", "pulsating"),
    "RR": ("RR Lyrae", "pulsating"),
    "RRAB": ("RR Lyrae (ab)", "pulsating"),
    "RRC": ("RR Lyrae (c)", "pulsating"),
    "DSCT": ("delta Scuti", "pulsating"),
    "GDOR": ("gamma Doradus", "pulsating"),
    "BCEP": ("beta Cephei", "pulsating"),
    "E": ("eclipsing binary", "eclipsing"),
    "EA": ("Algol-type eclipsing binary", "eclipsing"),
    "EB": ("beta Lyrae eclipsing binary", "eclipsing"),
    "EW": ("W UMa contact binary", "eclipsing"),
    "ELL": ("ellipsoidal variable", "eclipsing"),
    "ROT": ("rotational (spotted) variable", "rotating"),
    "BY": ("BY Dra spotted star", "rotating"),
    "RS": ("RS CVn binary", "rotating"),
    "ACV": ("alpha2 CVn chemically peculiar", "rotating"),
    "UV": ("UV Ceti flare star", "eruptive"),
    "FLARE": ("flare star", "eruptive"),
    "INS": ("Orion variable (YSO)", "eruptive-YSO"),
    "IN": ("Orion variable (YSO)", "eruptive-YSO"),
    "ISA": ("Orion variable (YSO)", "eruptive-YSO"),
    "ORION": ("Orion variable (YSO)", "eruptive-YSO"),
    "YSO": ("young stellar object", "eruptive-YSO"),
    "TTS": ("T Tauri star", "eruptive-YSO"),
    "CTTS": ("classical T Tauri star", "eruptive-YSO"),
    "WTTS": ("weak-line T Tauri star", "eruptive-YSO"),
    "FU": ("FU Orionis outburster", "eruptive-YSO"),
    "EXOR": ("EX Lupi outburster", "eruptive-YSO"),
    "UXOR": ("UX Ori (dust-obscured YSO)", "eruptive-YSO"),
    "GCAS": ("gamma Cas Be star", "eruptive"),
    "BE": ("Be star", "eruptive"),
    "RCB": ("R CrB star (dust fading)", "eruptive"),
    "WR": ("Wolf-Rayet star", "eruptive"),
    "CV": ("cataclysmic variable", "cataclysmic"),
    "UG": ("dwarf nova", "cataclysmic"),
    "N": ("nova", "cataclysmic"),
    "NL": ("nova-like", "cataclysmic"),
    "SN": ("supernova", "cataclysmic"),
    "QSO": ("quasar", "AGN"),
    "AGN": ("active galactic nucleus", "AGN"),
    "BLLAC": ("BL Lac object", "AGN"),
    "BLAZAR": ("blazar", "AGN"),
    "VAR": ("variable, type unknown", "unknown"),
    "MISC": ("miscellaneous variable", "unknown"),
}


def decode_vsx_type(vtype):
    """('Mira long-period variable', 'pulsating-LPV') from 'M', 'M:', 'SRB+E'..."""
    if not vtype:
        return None, None
    # VSX composites: 'EA/RS', 'SRB+E', 'M|SR' -> classify by the first code;
    # ':' marks an uncertain type.
    primary = str(vtype).strip().upper()
    for sep in "+|/":
        primary = primary.split(sep)[0]
    primary = primary.rstrip(":").strip()
    best = None
    for key in VSX_TYPES:          # longest prefix wins: MISC > M, ELL > E
        if primary.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    if best is None:
        return f"variable ({vtype.strip()})", "unknown"
    return VSX_TYPES[best]


def vsx_amplitude(src):
    """(amplitude mag, passband) or (None, None)."""
    mx, mn = src.get("max"), src.get("min")
    if mn is None:
        return None, None
    if (src.get("f_min") or "").strip() == "(":     # 'min' is itself an amplitude
        return abs(float(mn)), src.get("n_min") or src.get("n_max")
    if mx is None:
        return None, None
    return round(abs(float(mn) - float(mx)), 3), src.get("n_max")


def aavso_url(oid):
    return (f"https://www.aavso.org/vsx/index.php?view=detail.top&oid={int(oid)}"
            if oid not in (None, "") else None)


# ---------------------------------------------------------------------------
# Gaia astrophysics (G6)
# ---------------------------------------------------------------------------
PARALLAX_MIN_SNR = 5.0
NEARBY_PC = 25.0


def gaia_distance(src):
    """{'distance_pc', 'abs_g', 'nearby'} from a well-measured parallax."""
    plx, err, g = src.get("parallax"), src.get("parallax_error"), src.get("gmag")
    if not plx or plx <= 0 or not err or plx / err < PARALLAX_MIN_SNR:
        return None
    d = 1000.0 / plx
    out = {"distance_pc": round(d, 2), "parallax_mas": round(plx, 3),
           "nearby": d < NEARBY_PC}
    if g is not None and g < 90:
        out["abs_g"] = round(g + 5.0 * math.log10(plx) - 10.0, 2)
    return out


# ---------------------------------------------------------------------------
# Photometric zero points for the SED (W6)
# ---------------------------------------------------------------------------
# Vega-system zero points. Gaia: SVO Filter Profile Service (GAIA3 G/BP/RP);
# WISE: Jarrett et al. (2011). Wavelengths are the reference/isophotal ones.
ZERO_POINTS = {
    "G": (0.6218, 3228.75),
    "BP": (0.5110, 3552.01),
    "RP": (0.7769, 2554.95),
    "W1": (3.3526, 309.540),
    "W2": (4.6028, 171.787),
    "W3": (11.5608, 31.674),
    "W4": (22.0883, 8.363),
}


def mag_to_mjy(band, mag):
    if mag is None or mag >= 90 or band not in ZERO_POINTS:
        return None
    lam, zp_jy = ZERO_POINTS[band]
    return lam, zp_jy * 10 ** (-0.4 * float(mag)) * 1e3


def surface_sum_to_mjy(flux_mjy_sr_sum, pix_sr):
    """Aperture sum of MJy/sr pixels -> mJy (1 MJy = 1e9 mJy)."""
    if flux_mjy_sr_sum is None or not pix_sr:
        return None
    return float(flux_mjy_sr_sum) * float(pix_sr) * 1e9


# ---------------------------------------------------------------------------
# Small numerics shared by app.py and step7_neowise.py
# ---------------------------------------------------------------------------
def robust_std(x):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 2:
        return None
    return float(1.4826 * np.median(np.abs(x - np.median(x))))
