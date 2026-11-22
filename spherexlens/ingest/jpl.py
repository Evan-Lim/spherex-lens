"""
spherexlens.ingest.jpl
JPL Solar System Dynamics adapters used by the asteroid check (Phase 1.1).

JPLSbIdent  -- https://ssd-api.jpl.nasa.gov/sb_ident.api
               "which known small bodies were inside this field at this time"
               (doc: https://ssd-api.jpl.nasa.gov/doc/sb_ident.html, v1.1)
JPLSbdb     -- https://ssd-api.jpl.nasa.gov/sbdb.api
               orbit class, NEO flag, H, orbit condition code for one body
               (doc: https://ssd-api.jpl.nasa.gov/doc/sbdb.html, v1.3)

Observer location
-----------------
SPHEREx is in a ~700 km low Earth orbit, and we do not carry its state vector,
so sb_ident is asked for *geocentric* positions (``mpc-code=500``; the doc
says topocentric codes only, but the service accepts 500 and reports the
location as "Geocentric").  The observer is then at most ~7000 km from the
assumed one.  For a main-belt asteroid (>= ~1 au away) that parallax is
< 7000 km / 1.5e8 km rad ~ 10" at the very worst and typically a few arcsec,
i.e. under one 6.2" SPHEREx pixel and inside the 9" match floor.  An NEO very
close to Earth can be off by much more -- matches to NEOs carry that caveat.

Behaviour notes found by calling the live service (2026-11):
* rates come back in "/h (the doc example says deg/s): units are read from
  the column header;
* the second (numerically integrated) pass has no position-error columns;
  only the two-body first pass has "Est. error", which is the error of that
  two-body approximation, not of the orbit.  Position uncertainty is
  therefore taken from the SBDB orbit condition code (see ``u_to_arcsec``);
* ``obs-time`` given as a JD intermittently returned HTTP 502 for a field
  that worked with the equivalent calendar time, so calendar time is used;
* a two-pass query takes 1-5 minutes, hence the long timeout and caching.
"""
import datetime as _dt
import math
import re

import config

from .base import Adapter, ArchiveUnavailable, Coverage, Result

SB_IDENT_URL = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
SBDB_URL = "https://ssd-api.jpl.nasa.gov/sbdb.api"
SBDB_HUMAN_URL = "https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr={}"

_MJD0 = _dt.datetime(1858, 11, 17)


# ---- small helpers ----------------------------------------------------------
def mjd_to_datetime(mjd):
    return _MJD0 + _dt.timedelta(days=float(mjd))


def mjd_to_obs_time(mjd):
    """sb_ident obs-time: YYYY-MM-DD_hh:mm:ss (UT)."""
    t = mjd_to_datetime(mjd) + _dt.timedelta(microseconds=500000)
    return t.strftime("%Y-%m-%d_%H:%M:%S")


def ra_param(ra_deg):
    """RA in degrees -> 'hh-mm-ss.ss'."""
    s = (float(ra_deg) % 360.0) / 15.0 * 3600.0
    s = round(s, 2) % 86400.0
    h, rem = divmod(s, 3600.0)
    m, sec = divmod(rem, 60.0)
    return f"{int(h):02d}-{int(m):02d}-{sec:05.2f}"


def dec_param(dec_deg):
    """Dec in degrees -> 'dd-mm-ss.s', with 'M' in place of a minus sign."""
    d = float(dec_deg)
    sign = "M" if d < 0 else ""
    s = round(abs(d) * 3600.0, 1)
    deg, rem = divmod(s, 3600.0)
    m, sec = divmod(rem, 60.0)
    return f"{sign}{int(deg):02d}-{int(m):02d}-{sec:04.1f}"


def parse_ra(text):
    """'19:06:37.64' -> degrees."""
    parts = [float(p) for p in re.split(r"[:\s]+", text.strip()) if p]
    while len(parts) < 3:
        parts.append(0.0)
    return 15.0 * (parts[0] + parts[1] / 60.0 + parts[2] / 3600.0)


def parse_dec(text):
    """'-30 05\\'20.1"' -> degrees."""
    t = text.strip()
    sign = -1.0 if t.startswith("-") else 1.0
    nums = [float(p) for p in re.findall(r"\d+(?:\.\d*)?", t)]
    while len(nums) < 3:
        nums.append(0.0)
    return sign * (nums[0] + nums[1] / 60.0 + nums[2] / 3600.0)


def _float(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def sbdb_key(name):
    """sb_ident 'Object name' -> a string SBDB/SSOIS resolve unambiguously.

    '1000 Piazzia (A923 PF)' -> '1000'; '(2013 UZ47)' -> '2013 UZ47';
    'C/2022 P1 (NEOWISE)' -> 'C/2022 P1'; '73P-C/Schwassmann' -> '73P-C'.
    """
    name = (name or "").strip()
    m = re.match(r"^(\d+)\s", name + " ")
    if m:
        return m.group(1)
    m = re.match(r"^\(([^)]+)\)", name)
    if m:
        return m.group(1).strip()
    m = re.match(r"^([PCDXAI]/\d{4}\s+[A-Z]{1,2}\d*)", name)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+[PDI](?:-[A-Z]+)?)", name)
    if m:
        return m.group(1)
    return name.split("(")[0].strip() or name


def sbdb_human_url(key):
    from urllib.parse import quote
    return SBDB_HUMAN_URL.format(quote(str(key)))


# MPC orbit-uncertainty parameter U: the along-track "runoff" in mean anomaly
# per decade falls in bins growing by a factor ~4.4 per step (U=0: < 1",
# U=1: < 4.4", ... U=9: worse).  Used as a conservative position uncertainty.
_U_BINS_ARCSEC = [1.0, 4.4, 19.6, 86.0, 384.0, 1690.0, 7490.0, 33100.0, 146000.0]


def u_to_arcsec(code):
    """Orbit condition code (0-9, or letter codes for comets) -> arcsec."""
    try:
        u = int(str(code).strip())
    except (TypeError, ValueError):
        return None
    if u < 0:
        return None
    return _U_BINS_ARCSEC[min(u, len(_U_BINS_ARCSEC) - 1)] * (4.4 if u >= 9 else 1.0)


class _LongTimeoutSession:
    """Wraps a requests session so every request gets at least ``timeout``.

    base.Adapter.http uses config.HTTP_TIMEOUT_S (60 s); a two-pass sb_ident
    call routinely takes longer than that.
    """

    def __init__(self, session, timeout):
        self._session = session
        self._timeout = timeout

    @property
    def headers(self):
        return self._session.headers

    def request(self, method, url, **kw):
        kw["timeout"] = max(float(kw.get("timeout") or 0), self._timeout)
        return self._session.request(method, url, **kw)

    def __getattr__(self, item):
        return getattr(self._session, item)


# ---- sb_ident ---------------------------------------------------------------
class JPLSbIdent(Adapter):
    name = "jpl_sb_ident"
    min_interval_s = 1.0            # JPL asks for one request at a time
    cache_ttl_s = 30 * 86400        # predictions for a past time barely change

    def __init__(self, session=None, use_cache=True):
        super().__init__(session=session, use_cache=use_cache)
        self.session = _LongTimeoutSession(self.session,
                                           config.JPL_SB_IDENT_TIMEOUT_S)

    @staticmethod
    def params(ra, dec, mjd, ra_hwidth_deg, dec_hwidth_deg):
        return {
            "mpc-code": "500",                      # geocentric (see module doc)
            "obs-time": mjd_to_obs_time(mjd),
            "fov-ra-center": ra_param(ra),
            "fov-dec-center": dec_param(dec),
            "fov-ra-hwidth": f"{ra_hwidth_deg:.5f}",
            "fov-dec-hwidth": f"{dec_hwidth_deg:.5f}",
            "two-pass": "true",                     # numerically integrated
            "suppress-first-pass": "true",          # two-body pass not needed
            "mag-required": "false",                # keep objects without H
            "req-elem": "false",
        }

    def query(self, ra, dec, mjd, half_width_deg):
        """Known small bodies within a square field at time ``mjd``.

        ``half_width_deg`` is on-sky; the RA half-width is widened by
        1/cos(dec) so the box always contains the requested field.
        """
        cosd = max(math.cos(math.radians(dec)), 0.05)
        params = self.params(ra, dec, mjd, min(half_width_deg / cosd, 5.0),
                             half_width_deg)
        body, prov = self.http_json(SB_IDENT_URL, params=params)
        return self.parse(body, mjd, provenance=[prov])

    def parse(self, body, mjd=None, provenance=None):
        if not isinstance(body, dict) or "signature" not in body:
            raise ArchiveUnavailable(f"{self.name}: unexpected response")
        if body.get("error") or body.get("message") and not body.get("observer"):
            raise ArchiveUnavailable(
                f"{self.name}: {body.get('error') or body.get('message')}")
        fields = body.get("fields_second") or body.get("fields_first") or []
        data = body.get("data_second_pass")
        passname = "second"
        if data is None:
            data, passname = body.get("data_first_pass") or [], "first"
        idx = _column_index(fields)
        rows = []
        for rec in data or []:
            try:
                name = rec[idx["name"]]
                row = {
                    "designation": name,
                    "key": sbdb_key(name),
                    "ra": parse_ra(rec[idx["ra"]]),
                    "dec": parse_dec(rec[idx["dec"]]),
                    "v_mag": _float(rec[idx["v"]]) if "v" in idx else None,
                    "mjd": mjd,
                    "pass": passname,
                }
            except (IndexError, KeyError, ValueError):
                continue
            scale = idx.get("rate_scale", 1.0)
            for k, out in (("ra_rate", "ra_rate_arcsec_h"),
                           ("dec_rate", "dec_rate_arcsec_h")):
                if k in idx:
                    v = _float(rec[idx[k]])
                    row[out] = v * scale if v is not None else None
            if "err_ra" in idx:          # first pass only (two-body error)
                er, ed = _float(rec[idx["err_ra"]]), _float(rec[idx["err_dec"]])
                if er is not None and ed is not None:
                    row["twobody_err_arcsec"] = math.hypot(er, ed)
            rows.append(row)
        meta = {"observer": body.get("observer"), "mjd": mjd,
                "n_first_pass": body.get("n_first_pass"),
                "n_second_pass": body.get("n_second_pass"),
                "version": (body.get("signature") or {}).get("version")}
        return Result(archive=self.name, rows=rows,
                      provenance=provenance or [], meta=meta)

    # -- Adapter interface ----------------------------------------------------
    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None):
        """``t_start`` is the MJD of the exposure (required)."""
        if t_start is None:
            raise ValueError("sb_ident needs an observation time (t_start MJD)")
        return self.query(ra, dec, t_start, radius_deg)

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        if t_start is None:
            return Coverage(self.name, True, 0,
                            "ephemeris service: queried per exposure time")
        try:
            res = self.fetch(ra, dec, radius_deg, t_start=t_start)
        except ArchiveUnavailable as e:
            return Coverage(self.name, False, error=str(e))
        return Coverage(self.name, True, len(res.rows),
                        f"{len(res.rows)} known small bodies in field")


def _column_index(fields):
    """Map sb_ident column headers to roles; read rate units from headers."""
    idx = {}
    for i, f in enumerate(fields):
        fl = f.lower()
        if fl.startswith("object name"):
            idx["name"] = i
        elif fl.startswith("astrometric ra"):
            idx["ra"] = i
        elif fl.startswith("astrometric dec"):
            idx["dec"] = i
        elif fl.startswith("visual magnitude"):
            idx["v"] = i
        elif fl.startswith("ra rate"):
            idx["ra_rate"] = i
            if "deg/s" in fl:
                idx["rate_scale"] = 3600.0 * 3600.0
            elif "/s" in fl:
                idx["rate_scale"] = 3600.0
        elif fl.startswith("dec rate"):
            idx["dec_rate"] = i
        elif fl.startswith("est. error ra"):
            idx["err_ra"] = i
        elif fl.startswith("est. error dec"):
            idx["err_dec"] = i
    if not {"name", "ra", "dec"} <= set(idx):
        raise ArchiveUnavailable(f"jpl_sb_ident: unrecognised columns {fields}")
    return idx


# ---- SBDB -------------------------------------------------------------------
class JPLSbdb(Adapter):
    name = "jpl_sbdb"
    min_interval_s = 0.5
    cache_ttl_s = 30 * 86400

    def lookup(self, key):
        """Orbit summary for one body, or None when SBDB does not know it.

        Raises ArchiveUnavailable when SBDB cannot be reached.
        """
        params = {"sstr": str(key), "phys-par": "true"}
        body, prov = self.http_json(SBDB_URL, params=params)
        return self.parse(body, key, prov)

    @staticmethod
    def parse(body, key=None, provenance=None):
        if not isinstance(body, dict):
            raise ArchiveUnavailable("jpl_sbdb: unexpected response")
        obj = body.get("object")
        if not obj:
            # {"code":"200","message":"specified object was not found"} or a
            # code-300 list of several matches: an answer, not an outage.
            return None
        orbit = body.get("orbit") or {}
        phys = {p.get("name"): p.get("value") for p in body.get("phys_par") or []}
        oc = obj.get("orbit_class") or {}
        u = orbit.get("condition_code")
        des = obj.get("des") or key
        return {
            "key": key,
            "designation": obj.get("shortname") or obj.get("fullname") or des,
            "fullname": obj.get("fullname"),
            "des": des,
            "spkid": obj.get("spkid"),
            "kind": obj.get("kind"),
            "orbit_class": oc.get("code"),
            "orbit_class_name": oc.get("name"),
            "neo": bool(obj.get("neo")),
            "pha": bool(obj.get("pha")),
            "h_mag": _float(phys.get("H")),
            "condition_code": u,
            "pos_unc_arcsec": u_to_arcsec(u),
            "rms": _float(orbit.get("rms")),
            "data_arc_days": _float(orbit.get("data_arc")),
            "first_obs": orbit.get("first_obs"),
            "last_obs": orbit.get("last_obs"),
            "sbdb_url": sbdb_human_url(des),
            "provenance": provenance,
        }

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None):
        raise NotImplementedError("SBDB is looked up by designation: lookup()")

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        return Coverage(self.name, True, 0, "looked up per matched designation")
