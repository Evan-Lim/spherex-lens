"""
spherexlens.ingest.ssois
CADC Solar System Object Image Search (SSOIS), Gwyn, Hill & Kavelaars (2012).

Given a small-body designation and a time window, SSOIS lists the archive
images (CFHT, Subaru, Pan-STARRS, DECam, ESO, NEOSSat, WISE, ... -- any
telescope it indexes) that contain the object, with *its own* predicted RA/Dec
at each image's mid-exposure.  SPHEREx-Lens uses those positions as an
independent check of the JPL prediction, and the NEOSSat rows as a pointer to
Canadian follow-up frames.

Interface (verified 2026-11 from the SSOIS web form, en/ssois/index.html, and
its results page script, which fetches ``/cadcbin/ssos/ssosclf.pl`` with the
form's query string):

    GET https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/cadcbin/ssos/ssosclf.pl
        ?lang=en&object=<designation>&search=bynameCADC
        &epoch1=YYYY MM DD&epoch2=YYYY MM DD
        &eellipse=&eunits=arcseconds&extres=no&xyres=no&format=tsv

``search=bynameCADC`` makes SSOIS propagate MPC orbital elements itself
(orbfit), so the comparison is not JPL-vs-JPL (``bynameHorizons`` would be).
The TSV header is
``Image MJD Filter Exptime Object_RA Object_Dec Image_target
Telescope/Instrument Datalink``.  A header with no rows means "no images";
an unknown object gives the header followed by an HTML "not found" note.
Dates are rounded to whole days by the service.  SSOIS's per-image positions
are themselves linear interpolations of a daily ephemeris (see the SSOIS
documentation), so only images within a couple of days are used here.
"""
import datetime as _dt
import math
from urllib.parse import urlencode

import config

from .base import Adapter, ArchiveUnavailable, Coverage, Result

SSOIS_URL = "https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/cadcbin/ssos/ssosclf.pl"
SSOIS_HUMAN_URL = "https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/ssois/results.html"
TSV_COLUMNS = ["Image", "MJD", "Filter", "Exptime", "Object_RA", "Object_Dec",
               "Image_target", "Telescope/Instrument", "Datalink"]

_MJD0 = _dt.datetime(1858, 11, 17)


def _date(mjd):
    return (_MJD0 + _dt.timedelta(days=float(mjd))).strftime("%Y %m %d")


def search_params(designation, mjd_start, mjd_end, fmt="tsv"):
    p = {"lang": "en", "object": str(designation), "search": "bynameCADC",
         "epoch1": _date(math.floor(mjd_start)),
         "epoch2": _date(math.ceil(mjd_end)),
         "eellipse": "", "eunits": "arcseconds", "extres": "no", "xyres": "no"}
    if fmt:
        p["format"] = fmt
    return p


def human_url(designation, mjd_start, mjd_end):
    """The SSOIS results page a person can open for the same search."""
    return SSOIS_HUMAN_URL + "?" + urlencode(
        search_params(designation, mjd_start, mjd_end, fmt=None))


def _float(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def parse_tsv(text):
    """SSOIS TSV -> (rows, found).  found=False when SSOIS does not know the
    object.  Raises ArchiveUnavailable for anything that is not SSOIS TSV."""
    lines = [ln for ln in text.replace("\r", "").split("\n")]
    if not lines or not lines[0].startswith("Image\tMJD"):
        raise ArchiveUnavailable(f"ssois: unexpected response: {text[:200]!r}")
    header = lines[0].split("\t")
    rows, found = [], True
    for ln in lines[1:]:
        if not ln.strip():
            continue
        if "not found" in ln.lower() and "\t" not in ln:
            found = False
            break
        parts = ln.split("\t")
        if len(parts) < 6:
            continue                    # trailing HTML notes etc.
        rec = dict(zip(header, parts))
        mjd = _float(rec.get("MJD"))
        ra, dec = _float(rec.get("Object_RA")), _float(rec.get("Object_Dec"))
        if mjd is None or ra is None or dec is None:
            continue
        inst = rec.get("Telescope/Instrument", "")
        rows.append({
            "image": rec.get("Image", "").split(" ")[0],
            "mjd": mjd, "ra": ra, "dec": dec,
            "filter": rec.get("Filter"), "exptime": _float(rec.get("Exptime")),
            "target": rec.get("Image_target"),
            "instrument": inst,
            "url": rec.get("Datalink"),
            "neossat": "neossat" in inst.lower(),
        })
    return rows, found


def _sep_arcsec(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    s = (math.sin((d2 - d1) / 2) ** 2
         + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2)
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(s)))) * 3600.0


def position_at(rows, mjd, max_gap_days=None, exact_days=0.002):
    """SSOIS's predicted position at ``mjd`` from the images around it.

    Uses an image within ``exact_days`` directly, else linearly interpolates
    the closest images before and after (both within ``max_gap_days``).
    Returns {"ra","dec","mjd_before","mjd_after","method"} or None when no
    images bracket the time.
    """
    if max_gap_days is None:
        max_gap_days = config.SSOIS_MAX_BRACKET_DAYS
    pts = sorted({(r["mjd"], r["ra"], r["dec"]) for r in rows})
    if not pts:
        return None
    nearest = min(pts, key=lambda p: abs(p[0] - mjd))
    if abs(nearest[0] - mjd) <= exact_days:
        return {"ra": nearest[1], "dec": nearest[2], "mjd_before": nearest[0],
                "mjd_after": nearest[0], "method": "nearest"}
    before = [p for p in pts if p[0] <= mjd and mjd - p[0] <= max_gap_days]
    after = [p for p in pts if p[0] >= mjd and p[0] - mjd <= max_gap_days]
    if not before or not after:
        return None
    b, a = before[-1], after[0]
    f = (mjd - b[0]) / (a[0] - b[0]) if a[0] > b[0] else 0.0
    dra = ((a[1] - b[1] + 180.0) % 360.0) - 180.0      # across RA = 0
    return {"ra": (b[1] + f * dra) % 360.0, "dec": b[2] + f * (a[2] - b[2]),
            "mjd_before": b[0], "mjd_after": a[0], "method": "interpolated"}


class CadcSsois(Adapter):
    name = "ssois"
    min_interval_s = 1.0
    cache_ttl_s = 7 * 86400

    def search(self, designation, mjd_start, mjd_end):
        """Images containing ``designation`` between two MJDs."""
        params = search_params(designation, mjd_start, mjd_end)
        body, prov = self.http(SSOIS_URL, params=params)
        text = body.decode("utf-8", errors="replace")
        rows, found = parse_tsv(text)
        rows = [r for r in rows if mjd_start - 1 <= r["mjd"] <= mjd_end + 1]
        return Result(archive=self.name, rows=rows, provenance=[prov],
                      meta={"found": found, "designation": str(designation),
                            "n_neossat": sum(r["neossat"] for r in rows),
                            "url": human_url(designation, mjd_start, mjd_end)})

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None):
        raise NotImplementedError("SSOIS is searched by designation: search()")

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        return Coverage(self.name, True, 0, "searched per matched designation")
