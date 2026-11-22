"""
spherexlens.ingest.neowise
NEOWISE-R single exposures (neowiser_p1bs_psd) and AllWISE multi-epoch
photometry (allwise_p3as_mep) from IRSA TAP, as an Adapter.

fetch_positions() is step7_neowise.query_positions unchanged: ONE upload-join
query per table for any number of positions (a NEOWISE query costs about the
same for 1 cone or 30).  Rows come back exactly as step7 returns them, each
tagged with "table".  strict=True so an outage raises ArchiveUnavailable.

coverage() counts NEOWISE exposures in a small cone (capped at
COVERAGE_RADIUS_ARCSEC: the table is ~200 billion rows deep, and a count over
a 0.1 deg cone takes minutes).
"""
from spherexlens.ingest.base import (Adapter, ArchiveUnavailable, Coverage,
                                     Result, provenance_record)
from spherexlens.ingest.gaia import IRSA_TAP_SYNC, _cone_adql, parse_tsv

COVERAGE_RADIUS_ARCSEC = 10.0


class NEOWISE(Adapter):
    name = "neowise"

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        r = min(radius_deg, COVERAGE_RADIUS_ARCSEC / 3600.0)
        where = _cone_adql(ra, dec, r)
        if t_start is not None:
            where += f" AND mjd >= {float(t_start)}"
        if t_end is not None:
            where += f" AND mjd <= {float(t_end)}"
        adql = ("SELECT COUNT(*) AS n, MIN(mjd) AS t0, MAX(mjd) AS t1 "
                f"FROM neowiser_p1bs_psd WHERE {where}")
        body, _ = self.http(IRSA_TAP_SYNC, data={
            "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "tsv", "QUERY": adql},
            ttl_s=30 * 86400)
        rows = parse_tsv(body.decode("utf-8", "replace"))
        n = int(float(rows[0]["n"])) if rows else 0
        return Coverage(self.name, True, n,
                        f"NEOWISE-R exposures within {r * 3600:.0f}\"")

    def fetch_positions(self, positions, tables=("neowise", "allwise_mep")):
        """positions: [{"pid", "ra", "dec", "radius_arcsec"}] -> Result."""
        import step7_neowise
        rows, prov = [], []
        for key in tables:
            try:
                got = step7_neowise.query_positions(positions, key, strict=True)
            except Exception as e:
                raise ArchiveUnavailable(f"{self.name}: {e}")
            rows += [{**r, "table": key} for r in got]
            prov.append(provenance_record(
                self.name, IRSA_TAP_SYNC,
                {"table": step7_neowise.TABLES[key]["table"],
                 "n_positions": len(positions)}, len(got)))
        return Result(self.name, rows, prov)

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None,
              tables=("neowise", "allwise_mep")):
        res = self.fetch_positions(
            [{"pid": 0, "ra": ra, "dec": dec, "radius_arcsec": radius_deg * 3600.0}],
            tables)
        if t_start is not None or t_end is not None:
            def keep(r):
                try:
                    t = float(r.get("mjd"))
                except (TypeError, ValueError):
                    return False
                return ((t_start is None or t >= t_start)
                        and (t_end is None or t <= t_end))
            res.rows = [r for r in res.rows if keep(r)]
        return res
