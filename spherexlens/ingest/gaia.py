"""
spherexlens.ingest.gaia
Gaia cone searches from three independent mirrors, tried in order:

    GaiaIRSA    IRSA TAP  gaia.gaia_dr3_source       (what step1 used alone)
    GaiaMAST    MAST      Mast.Catalogs.GaiaDR3.Cone (api/v0/invoke)
    GaiaVizieR  VizieR    I/355/gaiadr3              (ASU VOTable, mirrors
                                                      from step1.VIZIER_MIRRORS)

``fetch_gaia_with_fallback()`` returns the first mirror that answers with
rows, and records which one did (``GaiaFetch.mirror``) plus every attempt.

Rows are normalised to the names step1_ingest / catalog_tools / step4 use:

    ra, dec, pm_ra, pm_de (mas/yr, mu_alpha*), gmag (99.0 if missing),
    source_id (str), parallax, parallax_error, bpmag, rpmag,
    var_flag  (= Gaia phot_variable_flag: "VARIABLE" / "NOT_AVAILABLE"),
    ruwe,
    var_class (only with classify=True and only for VARIABLE stars: the
               Gaia DR3 vari_classifier_result best_class_name, from the ESA
               Gaia archive -- no IRSA/MAST mirror carries that table)

Notes from the live checks (2026-11-13):

* astroquery 0.4.11 ``Catalogs.query_region(catalog="Gaia", version=3)``
  warns "Invalid Gaia version number, defaulting to DR2" and silently returns
  DR2.  GaiaMAST therefore calls the MAST service astroquery wraps
  (``Mast.Catalogs.GaiaDR3.Cone``) directly and *checks* that every row's
  designation starts with "Gaia DR3".
* The Harvard VizieR mirror returns I/355/gaiadr3 with misaligned columns
  (Source=0, magnitudes 999999999); the CDS mirror answered 404 from this
  network.  GaiaVizieR rejects a reply whose rows fail a sanity check
  instead of passing sentinel values on as stars ("fail, don't fake").

DR4 readiness
-------------
The release is ``config.GAIA_RELEASE`` (env SPHEREX_GAIA_RELEASE).  Moving to
DR4 means: (1) fill in the DR4 entry of RELEASES once the archives publish
their table names (the DR4 names below are the expected pattern, marked
provisional, and are refused until ``provisional`` is removed);
(2) catalog_tools.GAIA_DR3_EPOCH_MJD / CATALOG_EPOCH_MJD["gaia"] must follow
RELEASES[...]["ref_epoch"] (DR4: J2017.5) -- normalize.catalog_epoch_mjd()
already reads it from here; (3) DR4 adds epoch astrometry, so high-RUWE
sources may get proper solutions; GAIA_RUWE_MAX should be revisited;
(4) step1's IRSA path delegates to step1_ingest.query_irsa_tap_gaia, which
names gaia_dr3_source; for any other release GaiaIRSA issues its own query.
"""
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import config
from catalog_tools import sep_arcsec
from spherexlens.ingest.base import (Adapter, ArchiveUnavailable, Coverage,
                                     Result, provenance_record)

RELEASES = {
    "DR3": {"irsa_table": "gaia.gaia_dr3_source",
            "mast_service": "Mast.Catalogs.GaiaDR3.Cone",
            "vizier": "I/355/gaiadr3",
            "esa_vari": "gaiadr3.vari_classifier_result",
            "ref_epoch": 2016.0},
    "DR4": {"irsa_table": "gaia.gaia_dr4_source",
            "mast_service": "Mast.Catalogs.GaiaDR4.Cone",
            "vizier": None,
            "esa_vari": "gaiadr4.vari_classifier_result",
            "ref_epoch": 2017.5,
            "provisional": True},
}
IRSA_TAP_SYNC = "https://irsa.ipac.caltech.edu/TAP/sync"
ESA_TAP_SYNC = "https://gea.esac.esa.int/tap-server/tap/sync"
DEFAULT_LIMIT = 50000           # = step1_ingest.GAIA_TAP_LIMIT
MIN_VALID_FRACTION = 0.5        # VizieR reply sanity check


def release_info(release=None):
    rel = (release or config.GAIA_RELEASE).upper()
    info = RELEASES.get(rel)
    if info is None:
        raise ArchiveUnavailable(f"gaia: release {rel} is not configured")
    if info.get("provisional"):
        raise ArchiveUnavailable(
            f"gaia: {rel} table names are provisional -- confirm them in "
            "spherexlens/ingest/gaia.py RELEASES before use")
    return rel, info


def _f(value, default=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def normalise_row(ra, dec, pmra=None, pmdec=None, gmag=None, source_id=None,
                  parallax=None, parallax_error=None, bpmag=None, rpmag=None,
                  var_flag=None, ruwe=None):
    """One Gaia row in step1's format (same defaults as step1)."""
    sid = None if source_id in (None, "") else str(source_id).strip()
    return {"ra": float(ra), "dec": float(dec),
            "pm_ra": _f(pmra, 0.0), "pm_de": _f(pmdec, 0.0),
            "gmag": _f(gmag, 99.0),
            "source_id": sid or None,
            "parallax": _f(parallax), "parallax_error": _f(parallax_error),
            "bpmag": _f(bpmag), "rpmag": _f(rpmag),
            "var_flag": (str(var_flag).strip() or None) if var_flag is not None else None,
            "ruwe": _f(ruwe)}


def parse_tsv(text):
    text = (text or "").strip()
    if not text or text.startswith("<"):
        raise ArchiveUnavailable(f"TAP returned no table: {text[:200]!r}")
    lines = text.splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, ln.split("\t"))) for ln in lines[1:]]


def parse_votable_any(body):
    """VOTable (any 1.x namespace) -> list of {FIELD name: text}."""
    root = ET.fromstring(body)
    ns = root.tag[:root.tag.index("}") + 1] if root.tag.startswith("{") else ""
    error = None
    for info in root.iter(ns + "INFO"):
        if info.get("name") == "QUERY_STATUS" and info.get("value") == "ERROR":
            error = (info.text or info.get("value") or "").strip()
    names = [f.get("name") for f in root.iter(ns + "FIELD")]
    rows = []
    for tr in root.iter(ns + "TR"):
        cells = [(td.text or "").strip() for td in tr.findall(ns + "TD")]
        if len(cells) == len(names):
            rows.append(dict(zip(names, cells)))
    # VizieR ends a table cut by -out.max with "(pipe) Request has been
    # interrupted": rows before it are fine.  An error with no rows is not.
    if error and not rows:
        raise ArchiveUnavailable(f"VOTable error: {error}")
    return rows


def _cone_adql(ra, dec, radius_deg, ra_col="ra", dec_col="dec"):
    return (f"1=CONTAINS(POINT('ICRS',{ra_col},{dec_col}), "
            f"CIRCLE('ICRS',{ra:.6f},{dec:.6f},{radius_deg:.6f}))")


class GaiaIRSA(Adapter):
    name = "gaia_irsa"
    mirror = "irsa"

    def __init__(self, release=None, **kw):
        super().__init__(**kw)
        self.release = release

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        rel, info = release_info(self.release)
        adql = (f"SELECT COUNT(*) AS n FROM {info['irsa_table']} WHERE "
                + _cone_adql(ra, dec, radius_deg))
        body, _ = self.http(IRSA_TAP_SYNC, data={
            "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "tsv", "QUERY": adql},
            ttl_s=30 * 86400)
        rows = parse_tsv(body.decode("utf-8", "replace"))
        n = int(float(rows[0]["n"])) if rows else 0
        return Coverage("gaia", True, n, f"Gaia {rel} sources (IRSA)")

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None,
              limit=DEFAULT_LIMIT):
        rel, info = release_info(self.release)
        if rel == "DR3":
            # Exactly step1's query (looked up at call time, so tests and
            # step1 share one implementation); strict -> failures raise.
            import step1_ingest
            try:
                rows = step1_ingest.query_irsa_tap_gaia(ra, dec, radius_deg,
                                                        limit=limit, strict=True)
            except Exception as e:
                raise ArchiveUnavailable(f"{self.name}: {e}")
        else:
            adql = (f"SELECT TOP {int(limit)} source_id, ra, dec, pmra, pmdec, "
                    "parallax, parallax_error, phot_g_mean_mag, phot_bp_mean_mag, "
                    "phot_rp_mean_mag, phot_variable_flag, ruwe FROM "
                    f"{info['irsa_table']} WHERE {_cone_adql(ra, dec, radius_deg)} "
                    "ORDER BY phot_g_mean_mag ASC")
            body, _ = self.http(IRSA_TAP_SYNC, data={
                "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "tsv", "QUERY": adql})
            rows = [normalise_row(r["ra"], r["dec"], r.get("pmra"), r.get("pmdec"),
                                  r.get("phot_g_mean_mag"), r.get("source_id"),
                                  r.get("parallax"), r.get("parallax_error"),
                                  r.get("phot_bp_mean_mag"), r.get("phot_rp_mean_mag"),
                                  r.get("phot_variable_flag"), r.get("ruwe"))
                    for r in parse_tsv(body.decode("utf-8", "replace"))
                    if r.get("ra") and r.get("dec")]
        prov = provenance_record(self.name, IRSA_TAP_SYNC,
                                 {"table": info["irsa_table"], "ra": ra, "dec": dec,
                                  "radius_deg": radius_deg}, len(rows))
        return Result(self.name, rows, [prov], {"mirror": self.mirror, "release": rel})


class GaiaMAST(Adapter):
    name = "gaia_mast"
    mirror = "mast"

    def __init__(self, release=None, **kw):
        super().__init__(**kw)
        self.release = release

    def _invoke(self, ra, dec, radius_deg, pagesize, ttl_s=None):
        rel, info = release_info(self.release)
        req = {"service": info["mast_service"],
               "params": {"ra": ra, "dec": dec, "radius": radius_deg},
               "format": "json", "pagesize": int(pagesize), "page": 1}
        payload, prov = self.http_json(config.MAST_INVOKE_URL,
                                       data={"request": json.dumps(req)}, ttl_s=ttl_s)
        if payload.get("status") not in ("COMPLETE", None):
            raise ArchiveUnavailable(f"{self.name}: status {payload.get('status')}: "
                                     f"{payload.get('msg')}")
        bad = [r.get("designation") for r in payload.get("data") or []
               if not str(r.get("designation", "")).startswith(f"Gaia {rel} ")]
        if bad:
            raise ArchiveUnavailable(f"{self.name}: asked for Gaia {rel}, got "
                                     f"{bad[0]!r} -- wrong release served")
        return rel, payload, prov

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        rel, payload, _ = self._invoke(ra, dec, radius_deg, pagesize=1,
                                       ttl_s=30 * 86400)
        n = int((payload.get("paging") or {}).get("rowsTotal") or 0)
        return Coverage("gaia", True, n, f"Gaia {rel} sources (MAST)")

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None,
              limit=DEFAULT_LIMIT):
        rel, payload, prov = self._invoke(ra, dec, radius_deg, pagesize=limit)
        rows = [normalise_row(r["ra"], r["dec"], r.get("pmra"), r.get("pmdec"),
                              r.get("phot_g_mean_mag"), r.get("source_id"),
                              r.get("parallax"), r.get("parallax_error"),
                              r.get("phot_bp_mean_mag"), r.get("phot_rp_mean_mag"),
                              r.get("phot_variable_flag"), r.get("ruwe"))
                for r in payload.get("data") or []
                if r.get("ra") is not None and r.get("dec") is not None]
        rows.sort(key=lambda s: s["gmag"])         # brightest first, like step1
        paging = payload.get("paging") or {}
        if (paging.get("rowsTotal") or 0) > len(rows):
            print(f"    WARNING: gaia (MAST) {paging.get('rowsTotal')} rows, "
                  f"kept the first {len(rows)}")
        prov["n_rows"] = len(rows)
        return Result(self.name, rows, [prov], {"mirror": self.mirror, "release": rel})


VIZIER_COLUMNS = ("Source", "RA_ICRS", "DE_ICRS", "pmRA", "pmDE", "Gmag", "Plx",
                  "e_Plx", "BPmag", "RPmag", "RUWE", "VarFlag")


def vizier_rows_valid(rows, ra, dec, radius_deg):
    """True when most rows look like real Gaia stars inside the cone."""
    if not rows:
        return True
    ok = 0
    for r in rows:
        g = r.get("gmag")
        if (r.get("source_id") not in (None, "0") and g is not None and 0 < g < 30
                and sep_arcsec(ra, dec, r["ra"], r["dec"]) <= radius_deg * 3600 * 1.5):
            ok += 1
    return ok / len(rows) >= MIN_VALID_FRACTION


class GaiaVizieR(Adapter):
    name = "gaia_vizier"
    mirror = "vizier"

    def __init__(self, release=None, mirrors=None, **kw):
        super().__init__(**kw)
        self.release = release
        self.mirrors = mirrors

    def _mirrors(self):
        if self.mirrors is not None:
            return self.mirrors
        import step1_ingest
        return step1_ingest.VIZIER_MIRRORS

    def _query(self, ra, dec, radius_deg, limit, columns=VIZIER_COLUMNS):
        rel, info = release_info(self.release)
        if not info.get("vizier"):
            raise ArchiveUnavailable(f"{self.name}: no VizieR table for Gaia {rel}")
        errors = []
        for host in self._mirrors():
            url = f"https://{host}/viz-bin/votable"
            params = {"-source": info["vizier"], "-c": f"{ra:.6f} {dec:+.6f}",
                      "-c.rd": f"{radius_deg:.6f}", "-out": ",".join(columns),
                      "-out.max": str(int(limit)), "-sort": "Gmag"}
            try:
                body, prov = self.http(url, params=params)
                raw = parse_votable_any(body)
                rows = [normalise_row(r["RA_ICRS"], r["DE_ICRS"], r.get("pmRA"),
                                      r.get("pmDE"), r.get("Gmag"), r.get("Source"),
                                      r.get("Plx"), r.get("e_Plx"), r.get("BPmag"),
                                      r.get("RPmag"), r.get("VarFlag"), r.get("RUWE"))
                        for r in raw if r.get("RA_ICRS") and r.get("DE_ICRS")]
            except ArchiveUnavailable as e:
                errors.append(f"{host}: {e}")
                continue
            except (ET.ParseError, ValueError, KeyError) as e:
                errors.append(f"{host}: unreadable reply ({type(e).__name__})")
                continue
            if not vizier_rows_valid(rows, ra, dec, radius_deg):
                errors.append(f"{host}: corrupt rows (sentinel ids/magnitudes)")
                continue
            prov["n_rows"] = len(rows)
            prov["mirror_host"] = host
            return rel, host, rows, prov
        raise ArchiveUnavailable(f"{self.name}: " + "; ".join(errors or ["no mirror"]))

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        rel, host, rows, _ = self._query(ra, dec, radius_deg, DEFAULT_LIMIT,
                                         columns=("Source", "RA_ICRS", "DE_ICRS", "Gmag"))
        return Coverage("gaia", True, len(rows), f"Gaia {rel} sources (VizieR {host})")

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None,
              limit=DEFAULT_LIMIT):
        rel, host, rows, prov = self._query(ra, dec, radius_deg, limit)
        return Result(self.name, rows, [prov],
                      {"mirror": f"vizier:{host}", "release": rel})


class GaiaESAVariability(Adapter):
    """Gaia variability classes from the ESA Gaia archive (by source_id)."""
    name = "gaia_esa"

    def classes(self, source_ids, release=None, chunk=500):
        rel, info = release_info(release)
        ids = sorted({str(s) for s in source_ids if s not in (None, "", "0")})
        out = {}
        for i in range(0, len(ids), chunk):
            part = ",".join(ids[i:i + chunk])
            adql = (f"SELECT source_id, best_class_name, best_class_score FROM "
                    f"{info['esa_vari']} WHERE source_id IN ({part})")
            body, _ = self.http(ESA_TAP_SYNC, data={
                "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql},
                ttl_s=90 * 86400)
            lines = body.decode("utf-8", "replace").strip().splitlines()
            if not lines or not lines[0].startswith("source_id"):
                raise ArchiveUnavailable(f"{self.name}: unexpected reply {lines[:1]}")
            for ln in lines[1:]:
                sid, cls, score = (ln.split(",") + ["", ""])[:3]
                out[sid] = {"class": cls, "score": _f(score)}
        return out


def mirror_adapters(names=None, release=None, session=None, use_cache=True):
    table = {"irsa": GaiaIRSA, "mast": GaiaMAST, "vizier": GaiaVizieR}
    return [table[n](release=release, session=session, use_cache=use_cache)
            for n in (names or config.GAIA_MIRRORS)]


@dataclass
class GaiaFetch:
    rows: list
    mirror: str = None              # which mirror answered (None: none did)
    answered: bool = False          # some mirror replied (maybe with 0 rows)
    release: str = None
    attempts: list = field(default_factory=list)
    provenance: list = field(default_factory=list)

    @property
    def state(self):
        """step1 catalog_status value: ok / none / unavailable."""
        return "ok" if self.rows else ("none" if self.answered else "unavailable")


def add_variability_classes(rows, release=None, session=None):
    """Adds var_class to VARIABLE rows (in place); returns how many got one."""
    ids = [r["source_id"] for r in rows if r.get("var_flag") == "VARIABLE"]
    if not ids:
        return 0
    classes = GaiaESAVariability(session=session).classes(ids, release)
    n = 0
    for r in rows:
        c = classes.get(str(r.get("source_id")))
        if c:
            r["var_class"] = c["class"]
            n += 1
    return n


def fetch_gaia_with_fallback(ra, dec, radius_deg, mirrors=None, limit=DEFAULT_LIMIT,
                             classify=False, session=None, log=print):
    """Gaia cone from the first mirror that answers with rows.

    ``mirrors``: adapter instances or names (default config.GAIA_MIRRORS).
    A mirror that answers with zero rows is remembered (``answered``) and the
    next is tried, as step1 always did for IRSA -> VizieR.  Never raises.
    """
    if mirrors is None or (mirrors and isinstance(mirrors[0], str)):
        mirrors = mirror_adapters(mirrors, session=session)
    out = GaiaFetch(rows=[])
    for m in mirrors:
        label = getattr(m, "mirror", m.name)
        try:
            res = m.fetch(ra, dec, radius_deg, limit=limit)
        except Exception as e:          # ArchiveUnavailable or anything else
            out.attempts.append({"mirror": label, "status": "unavailable",
                                 "error": f"{type(e).__name__}: {e}"[:300]})
            if log:
                log(f"    gaia: {label} unavailable ({type(e).__name__}); next mirror")
            continue
        out.provenance += res.provenance
        out.answered = True
        out.release = res.meta.get("release")
        out.attempts.append({"mirror": res.meta.get("mirror", label),
                             "status": "ok" if res.rows else "none",
                             "n": len(res.rows)})
        if res.rows:
            out.rows = res.rows
            out.mirror = res.meta.get("mirror", label)
            if log:
                log(f"    gaia: {len(res.rows)} sources ({out.mirror})")
            break
    if classify and out.rows:
        try:
            add_variability_classes(out.rows, out.release, session=session)
        except Exception as e:          # optional extra: never fails the fetch
            out.attempts.append({"mirror": "esa_variability", "status": "unavailable",
                                 "error": str(e)[:300]})
    return out


class GaiaFallback(Adapter):
    """The 'gaia' archive as the coverage report sees it: first mirror wins."""
    name = "gaia"

    def coverage(self, ra, dec, radius_deg, t_start=None, t_end=None):
        errors = []
        for m in mirror_adapters(session=self.session, use_cache=self.use_cache):
            try:
                return m.coverage(ra, dec, radius_deg)
            except Exception as e:
                errors.append(f"{m.mirror}: {e}"[:200])
        raise ArchiveUnavailable("; ".join(errors))

    def fetch(self, ra, dec, radius_deg, t_start=None, t_end=None,
              limit=DEFAULT_LIMIT, classify=False):
        got = fetch_gaia_with_fallback(ra, dec, radius_deg, limit=limit,
                                       classify=classify, session=self.session)
        if not got.answered:
            raise ArchiveUnavailable("gaia: " + "; ".join(
                a.get("error", "") for a in got.attempts))
        return Result(self.name, got.rows, got.provenance,
                      {"mirror": got.mirror, "release": got.release,
                       "attempts": got.attempts})
