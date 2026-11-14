"""
spherexlens.ingest.vsx
AAVSO VSX (VizieR B/vsx/vsx) as an Adapter.

A thin wrapper around step1_ingest._query_vizier("vsx", ...): same mirrors,
same retry, same row format.  When no mirror answers, fetch() raises
ArchiveUnavailable; a mirror that answers with no table is a genuine
"no variables here" and returns zero rows.
"""
import astropy.units as u
from astropy.coordinates import SkyCoord

from spherexlens.ingest.base import (Adapter, ArchiveUnavailable, Coverage,
                                     Result, provenance_record)


class VSX(Adapter):
    name = "vsx"

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None, strict=False):
        import step1_ingest
        coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
        rows, answered = step1_ingest._query_vizier(
            "vsx", coord, radius_deg * 60.0, strict)
        if not answered:
            raise ArchiveUnavailable(f"{self.name}: no VizieR mirror answered")
        prov = provenance_record(self.name, "B/vsx/vsx",
                                 {"ra": ra, "dec": dec, "radius_deg": radius_deg},
                                 len(rows))
        return Result(self.name, rows, [prov],
                      {"mirror": "vizier", "answered": answered})

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        res = self.fetch(ra, dec, radius_deg)
        return Coverage(self.name, True, len(res.rows), "known variables (VSX)")
