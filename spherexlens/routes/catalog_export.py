"""
Candidate catalogue exports:

    GET /api/candidates.csv       all triaged detections, flat CSV
    GET /api/candidates.votable   the same as a VOTable
    GET /api/cone?RA=&DEC=&SR=    IVOA Simple Cone Search 1.03 over the same rows

Provenance (pipeline version, config hash, snapshot, generation time) is in
'#' comment lines at the top of the CSV, in X-Pipeline-* response headers and
in VOTable PARAMs.
"""
import csv
import io
import math

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import Response

import config
from spherexlens.http_guard import RateLimiter
from spherexlens.logging_setup import logged_route
from spherexlens.routes import _detections
from version import pipeline_stamp

router = APIRouter()

EXPORT_LIMIT = RateLimiter(20, 60.0)
CONE_LIMIT = RateLimiter(60, 60.0)

VOTABLE_TYPE = "application/x-votable+xml"

# column -> (VOTable datatype, unit, UCD1+ for the plain catalogue)
_FIELDS = {
    "target": ("char", None, "meta.id;obs.field"),
    "detection_id": ("char", None, "meta.id;meta.main"),
    "ra": ("double", "deg", "pos.eq.ra;meta.main"),
    "dec": ("double", "deg", "pos.eq.dec;meta.main"),
    "mjd_a": ("double", "d", "time.epoch"),
    "mjd_b": ("double", "d", "time.epoch"),
    "kind": ("char", None, "meta.code.class"),
    "snr": ("double", None, "stat.snr"),
    "category": ("char", None, "meta.code.class"),
    "verdict": ("char", None, "meta.note"),
    "priority": ("int", None, "meta.code"),
    "ml_real_bogus": ("double", None, "stat.probability"),
    "evidence_score": ("double", None, "stat.value"),
    "asteroid_status": ("char", None, "meta.code"),
    "pipeline_version": ("char", None, "meta.version"),
    "config_hash": ("char", None, "meta.id"),
    "model_version": ("char", None, "meta.version"),
}
# Simple Cone Search 1.03 requires the (UCD1) ID_MAIN, POS_EQ_RA_MAIN and
# POS_EQ_DEC_MAIN columns.
_SCS_UCD = {"detection_id": "ID_MAIN", "ra": "POS_EQ_RA_MAIN", "dec": "POS_EQ_DEC_MAIN"}


def _provenance(data):
    stamp = pipeline_stamp()
    return {
        "server_pipeline_version": stamp["pipeline_version"],
        "server_config_hash": stamp["config_hash"],
        "source": str(data.get("source")),
        "n_fields": str(data.get("n_fields")),
        "generated_utc": str(data.get("generated_utc")),
    }


def _headers(prov, filename=None):
    headers = {"X-Pipeline-Version": prov["server_pipeline_version"],
               "X-Config-Hash": prov["server_config_hash"],
               "Cache-Control": "public, max-age=300"}
    if filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return headers


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


@router.get("/api/candidates.csv")
@logged_route("/api/candidates.csv")
def candidates_csv(request: Request):
    EXPORT_LIMIT.check(request)
    data = _detections.all_detections()
    prov = _provenance(data)
    buf = io.StringIO()
    buf.write("# SPHEREx-Lens candidate catalogue (all triaged detections)\n")
    for key, value in prov.items():
        buf.write(f"# {key}: {value}\n")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_detections.COLUMNS)
    for row in data["rows"]:
        writer.writerow([_csv_value(row.get(col)) for col in _detections.COLUMNS])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers=_headers(prov, "spherexlens_candidates.csv"))


def _votable_bytes(rows, params, scs=False, extra_cols=(), error=None):
    """Serialise rows with astropy's VOTable writer."""
    from astropy.io.votable.tree import (Field, Info, Param, Resource,
                                         TableElement, VOTableFile)

    votable = VOTableFile()
    resource = Resource()
    votable.resources.append(resource)
    if error is not None:
        # SCS 1.03 sec. 3: an error is a VOTable with an INFO named "Error";
        # QUERY_STATUS=ERROR is the DALI form read by newer clients.
        resource.infos.append(Info(name="QUERY_STATUS", value="ERROR"))
        votable.infos.append(Info(name="Error", value=error))
        out = io.BytesIO()
        votable.to_xml(out)
        return out.getvalue()
    resource.infos.append(Info(name="QUERY_STATUS", value="OK"))
    table = TableElement(votable, name="candidates")
    resource.tables.append(table)
    for name, value in params.items():
        table.params.append(Param(votable, name=name, datatype="char",
                                  arraysize="*", value=str(value)))
    columns = list(_detections.COLUMNS) + [c[0] for c in extra_cols]
    specs = dict(_FIELDS)
    for name, datatype, unit, ucd in extra_cols:
        specs[name] = (datatype, unit, ucd)
    for name in columns:
        datatype, unit, ucd = specs[name]
        if scs and name in _SCS_UCD:
            ucd = _SCS_UCD[name]
        kwargs = {"name": name, "datatype": datatype, "ucd": ucd}
        if datatype == "char":
            kwargs["arraysize"] = "*"
        if unit:
            kwargs["unit"] = unit
        table.fields.append(Field(votable, **kwargs))
    table.create_arrays(len(rows))
    for i, row in enumerate(rows):
        values, mask = [], []
        for name in columns:
            datatype = specs[name][0]
            value = row.get(name)
            if datatype == "char":
                values.append("" if value is None else str(value))
                mask.append(value is None)
            elif datatype == "int":
                ok = isinstance(value, (int, np.integer)) and not isinstance(value, bool)
                values.append(int(value) if ok else 0)
                mask.append(not ok)
            else:
                ok = value is not None and math.isfinite(float(value))
                values.append(float(value) if ok else np.nan)
                mask.append(not ok)
        table.array[i] = tuple(values)
        table.array.mask[i] = tuple(mask)
    out = io.BytesIO()
    import warnings
    with warnings.catch_warnings():
        # UCD1 words (ID_MAIN...) are what SCS 1.03 mandates; astropy warns
        # that they are not UCD1+.
        warnings.simplefilter("ignore")
        votable.to_xml(out)
    return out.getvalue()


@router.get("/api/candidates.votable")
@logged_route("/api/candidates.votable")
def candidates_votable(request: Request):
    EXPORT_LIMIT.check(request)
    data = _detections.all_detections()
    prov = _provenance(data)
    body = _votable_bytes(data["rows"], prov)
    return Response(body, media_type=VOTABLE_TYPE,
                    headers=_headers(prov, "spherexlens_candidates.vot"))


def _scs_error(message):
    # SCS errors are returned with HTTP 200 and the message in the VOTable.
    return Response(_votable_bytes([], {}, error=message), media_type=VOTABLE_TYPE,
                    headers={"Cache-Control": "no-store"})


def _parse_float(raw, name):
    if raw is None or str(raw).strip() == "":
        raise ValueError(f"Missing required parameter {name}.")
    if len(str(raw)) > 32:
        raise ValueError(f"Parameter {name} is too long.")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"Parameter {name} must be a number in decimal degrees.")
    if not math.isfinite(value):
        raise ValueError(f"Parameter {name} must be finite.")
    return value


def _sep_deg(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    s = (math.sin((d2 - d1) / 2) ** 2
         + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2)
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(s))))


@router.get("/api/cone")
@logged_route("/api/cone")
def cone_search(request: Request):
    """IVOA Simple Cone Search 1.03 (RA, DEC, SR in decimal ICRS degrees)."""
    CONE_LIMIT.check(request)
    # SCS parameter names are case-insensitive in practice; accept any case.
    params = {k.upper(): v for k, v in request.query_params.items()}
    try:
        ra = _parse_float(params.get("RA"), "RA")
        dec = _parse_float(params.get("DEC"), "DEC")
        sr = _parse_float(params.get("SR"), "SR")
        if not 0.0 <= ra <= 360.0:
            raise ValueError("RA must be between 0 and 360 degrees.")
        if not -90.0 <= dec <= 90.0:
            raise ValueError("DEC must be between -90 and 90 degrees.")
        if not 0.0 <= sr <= config.Production.CONE_MAX_SR_DEG:
            raise ValueError(
                f"SR must be between 0 and {config.Production.CONE_MAX_SR_DEG:g} degrees.")
        verb = params.get("VERB")
        if verb not in (None, "", "1", "2", "3"):
            raise ValueError("VERB must be 1, 2 or 3.")
    except ValueError as error:
        return _scs_error(str(error))
    data = _detections.all_detections()
    hits = []
    if sr > 0:
        for row in data["rows"]:
            if row.get("ra") is None or row.get("dec") is None:
                continue
            distance = _sep_deg(ra, dec, row["ra"], row["dec"])
            if distance <= sr:
                hits.append({**row, "dist_deg": distance})
        hits.sort(key=lambda r: r["dist_deg"])
    prov = _provenance(data)
    prov.update({"query_ra": f"{ra:g}", "query_dec": f"{dec:g}", "query_sr": f"{sr:g}"})
    body = _votable_bytes(hits, prov, scs=True,
                          extra_cols=[("dist_deg", "double", "deg", "pos.angDistance")])
    return Response(body, media_type=VOTABLE_TYPE, headers=_headers(prov))
