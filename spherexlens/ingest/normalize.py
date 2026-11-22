"""
spherexlens.ingest.normalize
One set of units across every archive (Phase 3.1):

    time  -> MJD (UTC)          to_mjd(value, fmt=None)
    flux  -> mJy                flux_to_mjy(value, unit, band=None, pix_sr=None)
    sky   -> one epoch          propagate_to_epoch(catalogs, epoch)

Time: ISO strings / datetimes / astropy Time are converted with astropy in
the UTC scale.  Bare numbers are read as JD when >= 2_400_000.5, else MJD
(override with fmt="jd"|"mjd"|"iso"|"year").  A number is assumed to be UTC
already: the archives used here (SPHEREx t_min, NEOWISE mjd, NEOSSat JD-OBS)
all state UTC; TT-UTC is 69 s, far below anything the pipeline resolves.

Flux: Vega magnitudes use catalog_tools.ZERO_POINTS (Gaia G/BP/RP, WISE
W1-W4) through catalog_tools.mag_to_mjy; AB magnitudes use 3631 Jy; surface
brightness (MJy/sr) summed over pixels uses the pixel solid angle via
catalog_tools.surface_sum_to_mjy.
"""
import math
from datetime import date, datetime

import catalog_tools
import config

JD_MJD_OFFSET = 2400000.5
AB_ZERO_POINT_JY = 3631.0


def to_mjd(value, fmt=None):
    """Any common time representation -> MJD (UTC) as float, None for None."""
    if value is None or value == "":
        return None
    from astropy.time import Time
    if isinstance(value, Time):
        return float(value.utc.mjd)
    if isinstance(value, (datetime, date)):
        return float(Time(value, scale="utc").mjd)
    fmt = (fmt or "").lower() or None
    if fmt in ("iso", "isot", "fits") or (fmt is None and isinstance(value, str)
                                         and not _is_number(value)):
        return float(Time(str(value).strip(), scale="utc").utc.mjd)
    x = float(value)
    if not math.isfinite(x):
        raise ValueError(f"time is not finite: {value!r}")
    if fmt == "jd" or (fmt is None and x >= JD_MJD_OFFSET):
        return x - JD_MJD_OFFSET
    if fmt in (None, "mjd"):
        return x
    if fmt in ("year", "jyear"):
        return catalog_tools.year_to_mjd(x)
    raise ValueError(f"unknown time format {fmt!r}")


def _is_number(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


_UNITS = {
    "mjy": 1.0, "millijansky": 1.0,
    "jy": 1e3, "jansky": 1e3,
    "ujy": 1e-3, "microjansky": 1e-3, "µjy": 1e-3,
    "njy": 1e-6,
}


def flux_to_mjy(value, unit, band=None, pix_sr=None):
    """Flux in mJy from Vega/AB magnitude, Jy-family, or summed MJy/sr pixels.

    unit: "vega_mag" (needs band: G BP RP W1..W4), "ab_mag", "Jy", "mJy",
    "uJy", "nJy", or "MJy/sr" (an aperture *sum*; needs pix_sr, the pixel
    solid angle in steradian).  Missing / sentinel inputs give None.
    """
    if value is None:
        return None
    u = str(unit).strip().lower().replace(" ", "")
    if u in ("vega_mag", "vega", "mag", "mag_vega"):
        got = catalog_tools.mag_to_mjy(str(band).upper() if band else None, value)
        return None if got is None else got[1]
    v = float(value)
    if not math.isfinite(v):
        return None
    if u in ("ab_mag", "ab", "mag_ab"):
        return None if v >= 90 else AB_ZERO_POINT_JY * 10 ** (-0.4 * v) * 1e3
    if u in _UNITS:
        return v * _UNITS[u]
    if u in ("mjy/sr", "mjy/sr*pixel", "mjy/sr·pixel", "mjy_sr_pixel", "mjysr"):
        if not pix_sr:
            raise ValueError("MJy/sr needs pix_sr (pixel solid angle, sr)")
        return catalog_tools.surface_sum_to_mjy(v, pix_sr)
    raise ValueError(f"unknown flux unit {unit!r}")


def catalog_epoch_mjd(cat, release=None):
    """Reference epoch (MJD) of a catalogue's positions; None = no motion."""
    if cat == "gaia":
        from spherexlens.ingest.gaia import RELEASES
        rel = (release or config.GAIA_RELEASE).upper()
        year = RELEASES.get(rel, RELEASES["DR3"])["ref_epoch"]
        return catalog_tools.year_to_mjd(year)
    return catalog_tools.CATALOG_EPOCH_MJD.get(cat)


def propagate_to_epoch(catalogs, epoch, fmt=None):
    """Every catalogue moved to ``epoch`` (any to_mjd() input).

    Wraps catalog_tools.propagate_catalogs (Gaia from its release epoch,
    AllWISE rows that are Gaia twins, VSX untouched).  Gaia rows whose RUWE
    exceeds config.GAIA_RUWE_MAX get ``propagation: "approximate"`` -- their
    proper motion may be biased by binarity, so the moved position is a
    best guess, not a precise one.
    """
    mjd = to_mjd(epoch, fmt)
    out = catalog_tools.propagate_catalogs(catalogs, mjd)
    if mjd is None or not out:
        return out
    gaia = out.get("gaia")
    if gaia:
        moved = []
        for s in gaia:
            if "moved_arcsec" in s and not catalog_tools.gaia_reliable(s):
                s = {**s, "propagation": "approximate"}
            moved.append(s)
        out = {**out, "gaia": moved}
    return out
