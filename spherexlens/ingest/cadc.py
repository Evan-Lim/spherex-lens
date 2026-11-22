"""
spherexlens.ingest.cadc
NEOSSat frames from the Canadian Astronomy Data Centre.

Endpoint (from CADC's registry https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/
reg/resource-caps, ivo://cadc.nrc.ca/argus):

    TAP sync  https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/sync
              (answers 303 -> result URL; requests follows it)
    table     ivoa.ObsCore, obs_collection = 'NEOSSAT'

The old ws-cadc.canfar.net/argus host answers 404.  Each ObsCore row's
access_url is a DataLink document; its "#this" link is the FITS file
(NEOS_SCI_<obs_id>.fits, ~0.9 MB raw, 856x512 int16, overscan + image).
Raw frames are calibration level 1 (see neossat_clean for the cleaning).

Data: NEOSSat, Canadian Space Agency, Open Government Licence - Canada
(https://data.asc-csa.gc.ca/dataset/neossat); served by CADC.
"""
import io
import os
import xml.etree.ElementTree as ET

import config
from spherexlens.ingest.base import (Adapter, ArchiveUnavailable, Coverage,
                                     Result)
from spherexlens.ingest.gaia import parse_tsv, parse_votable_any

COLUMNS = ("obs_id", "t_min", "t_max", "t_exptime", "access_url", "s_ra",
           "s_dec", "s_fov", "calib_level", "target_name", "obs_publisher_did")
MAX_ROWS = 2000


def _where(ra, dec, radius_deg, t_start=None, t_end=None,
           collection=None):
    w = [f"obs_collection = '{collection or config.NEOSSAT_COLLECTION}'",
         "dataproduct_type = 'image'",
         f"1=INTERSECTS(s_region, CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, "
         f"{radius_deg:.6f}))"]
    if t_start is not None:
        w.append(f"t_max >= {float(t_start)}")
    if t_end is not None:
        w.append(f"t_min <= {float(t_end)}")
    return " AND ".join(w)


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class CADCNeossat(Adapter):
    name = "neossat"
    cache_ttl_s = 86400

    def __init__(self, tap_url=None, collection=None, **kw):
        super().__init__(**kw)
        self.tap_url = tap_url or config.CADC_TAP_SYNC
        self.collection = collection or config.NEOSSAT_COLLECTION

    def _tap(self, adql, ttl_s=None):
        body, prov = self.http(self.tap_url, data={
            "LANG": "ADQL", "FORMAT": "tsv", "QUERY": adql}, ttl_s=ttl_s)
        return parse_tsv(body.decode("utf-8", "replace")), prov

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        rows, _ = self._tap(
            "SELECT COUNT(*) AS n, MIN(t_min) AS t0, MAX(t_min) AS t1 "
            "FROM ivoa.ObsCore WHERE "
            + _where(ra, dec, radius_deg, t_start, t_end, self.collection))
        n = int(float(rows[0]["n"])) if rows else 0
        detail = "no NEOSSat frames"
        if n:
            t0, t1 = _float(rows[0].get("t0")), _float(rows[0].get("t1"))
            detail = f"{n} NEOSSat frames, MJD {t0:.1f}-{t1:.1f}"
        return Coverage(self.name, True, n, detail)

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None,
              limit=MAX_ROWS):
        """Frames overlapping a cone (+ time range), earliest first."""
        rows, prov = self._tap(
            f"SELECT TOP {int(limit)} {', '.join(COLUMNS)} FROM ivoa.ObsCore "
            f"WHERE {_where(ra, dec, radius_deg, t_start, t_end, self.collection)} "
            "ORDER BY t_min")
        out = [{"obs_id": r.get("obs_id"),
                "mjd": _float(r.get("t_min")),
                "mjd_end": _float(r.get("t_max")),
                "exposure_s": _float(r.get("t_exptime")),
                "access_url": r.get("access_url"),     # DataLink document
                "url": r.get("access_url"),
                "ra": _float(r.get("s_ra")), "dec": _float(r.get("s_dec")),
                "fov_deg": _float(r.get("s_fov")),
                "calib_level": r.get("calib_level"),
                "target": r.get("target_name"),
                "publisher_did": r.get("obs_publisher_did")} for r in rows]
        prov["n_rows"] = len(out)
        return Result(self.name, out, [prov], {"collection": self.collection})

    def file_url(self, frame):
        """The FITS URL behind a frame's DataLink access_url ("#this")."""
        url = (frame.get("access_url") or frame.get("url")) if isinstance(frame, dict) else frame
        body, _ = self.http(url, ttl_s=30 * 86400)
        try:
            links = parse_votable_any(body)
        except ET.ParseError as e:
            raise ArchiveUnavailable(f"{self.name}: bad DataLink: {e}")
        for link in links:
            if link.get("semantics") == "#this" and link.get("access_url"):
                return link["access_url"]
        errs = [l.get("error_message") for l in links if l.get("error_message")]
        raise ArchiveUnavailable(f"{self.name}: no #this link "
                                 f"({errs[0] if errs else 'empty DataLink'})")

    def frame_bytes(self, frame):
        """Raw FITS bytes of one frame (via its DataLink "#this" link)."""
        url = self.file_url(frame)
        body, _ = self.http(url, ttl_s=0)     # ~0.9 MB: not kept in the cache
        if not body.startswith(b"SIMPLE"):
            raise ArchiveUnavailable(f"{self.name}: {url} is not a FITS file")
        return url, body

    def download_frame(self, frame, dest_dir=None):
        """One frame as an open astropy HDUList (in memory).

        With ``dest_dir`` the file is also written there (``<name>.fits``,
        via a .part file) and reused on the next call.  Close the HDUList
        when done.
        """
        from astropy.io import fits
        if dest_dir:
            return fits.open(self.save_frame(frame, dest_dir))
        _, body = self.frame_bytes(frame)
        return fits.open(io.BytesIO(body))

    def save_frame(self, frame, dest_dir):
        """Download one frame's FITS to dest_dir; returns the local path."""
        obs_id = frame.get("obs_id") if isinstance(frame, dict) else None
        os.makedirs(dest_dir, exist_ok=True)
        if obs_id:
            path = os.path.join(dest_dir, f"NEOS_SCI_{obs_id}.fits")
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path
        url, body = self.frame_bytes(frame)
        path = os.path.join(dest_dir, os.path.basename(url.split("?")[0]).split(":")[-1])
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, path)
        return path
