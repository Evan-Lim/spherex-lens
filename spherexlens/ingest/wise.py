"""
spherexlens.ingest.wise
AllWISE Source Catalog (IRSA TAP allwise_p3as_psd) as an Adapter.

A thin wrapper: fetch() is step1_ingest.query_irsa_tap_allwise (same query,
same row format, strict so an outage raises ArchiveUnavailable instead of
returning an empty, success-shaped list).
"""
from spherexlens.ingest.base import (Adapter, ArchiveUnavailable, Coverage,
                                     Result, provenance_record)
from spherexlens.ingest.gaia import IRSA_TAP_SYNC, _cone_adql, parse_tsv


class AllWISE(Adapter):
    name = "allwise"

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        adql = ("SELECT COUNT(*) AS n FROM allwise_p3as_psd WHERE "
                + _cone_adql(ra, dec, radius_deg))
        body, _ = self.http(IRSA_TAP_SYNC, data={
            "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "tsv", "QUERY": adql},
            ttl_s=30 * 86400)
        rows = parse_tsv(body.decode("utf-8", "replace"))
        n = int(float(rows[0]["n"])) if rows else 0
        return Coverage(self.name, True, n, "AllWISE sources (IRSA)")

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None, limit=None):
        import step1_ingest
        kw = {} if limit is None else {"limit": limit}
        try:
            rows = step1_ingest.query_irsa_tap_allwise(ra, dec, radius_deg,
                                                       strict=True, **kw)
        except Exception as e:
            raise ArchiveUnavailable(f"{self.name}: {e}")
        prov = provenance_record(self.name, IRSA_TAP_SYNC,
                                 {"table": "allwise_p3as_psd", "ra": ra, "dec": dec,
                                  "radius_deg": radius_deg}, len(rows))
        return Result(self.name, rows, [prov], {"mirror": "irsa"})
