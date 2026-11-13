"""
spherexlens.triage.asteroids  (Phase 1.1)
Is a detection just a known asteroid crossing the field?

For every distinct exposure time of the field (deduplicated, capped) JPL's
Small-Body Identification service lists the known small bodies predicted
inside the field.  Each detection is matched to the predictions for the
epoch in which it is *present*:

  step3_zogy.zogy(img_a, img_b) computes D = P_a*I_b - P_b*I_a, i.e. the
  later epoch minus the earlier one (app.py calls zogy(stacks[ea],
  stacks[eb])).  A positive monopole is therefore something present in
  epoch b only, a negative one ("monopole_negative", sign -1) something
  present in epoch a only.  An asteroid moves arcmin per hour, so it never
  forms a dipole: it is a positive blob in one epoch and absent in the other.

A match is enriched from JPL SBDB (class, NEO, H, orbit quality) and checked
against CADC SSOIS's independent ephemeris.  Matched CANDIDATEs become
KNOWN_ASTEROID; unmatched single-epoch monopoles near the ecliptic become
POSSIBLE_UNKNOWN_ASTEROID.  Detections already explained by a star or
variable are never re-labelled.

Fail, don't fake: when JPL cannot be reached every detection is marked
``asteroid.status = "unavailable"`` and keeps its category.
"""
import math

import numpy as np

import config
from spherexlens.enrich import register
from spherexlens.ingest.base import ArchiveUnavailable
from spherexlens.ingest.jpl import JPLSbdb, JPLSbIdent, mjd_to_datetime
from spherexlens.ingest import ssois as ssois_mod
from spherexlens.ingest.ssois import CadcSsois

OBLIQUITY_DEG = 23.4392911                 # J2000 mean obliquity
UNAVAILABLE_REASON = "asteroid check unavailable — treat CANDIDATE with caution"
DISAGREE_REASON = "orbit disagreement between JPL and SSOIS"
REPLACEABLE = {"CANDIDATE"}                # categories an asteroid may explain


# ---- geometry ---------------------------------------------------------------
def sep_arcsec(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    s = (math.sin((d2 - d1) / 2) ** 2
         + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2)
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(s)))) * 3600.0


def ecliptic_latitude(ra, dec):
    e, a, d = map(math.radians, (OBLIQUITY_DEG, ra, dec))
    s = math.sin(d) * math.cos(e) - math.cos(d) * math.sin(e) * math.sin(a)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def _in_box(ra, dec, ra0, dec0, half_arcsec):
    dx = (((ra - ra0 + 180.0) % 360.0) - 180.0) * math.cos(math.radians(dec0)) * 3600
    return abs(dx) <= half_arcsec and abs((dec - dec0) * 3600) <= half_arcsec


# ---- exposure times ---------------------------------------------------------
def query_times(ctx, wanted_epochs=None, cap=None):
    """{epoch: [mjd, ...]} -- deduplicated, at most ``cap`` in total.

    Each epoch gets an equal share of the cap; within an epoch the times are
    sampled evenly across its span (the co-add mixes all of them, and a
    moving object is only in the exposures taken while it crossed).
    """
    cap = config.ASTEROID_MAX_QUERIES_PER_FIELD if cap is None else cap
    mjds = list(ctx.get("mjds") or [])
    epochs = list(ctx.get("epochs") or [])
    by_epoch = {}
    if mjds and len(epochs) == len(mjds):
        for e, m in zip(epochs, mjds):
            if m:
                by_epoch.setdefault(e, []).append(float(m))
    else:                                  # fall back to epoch mean times
        for e, m in (ctx.get("epoch_mjd") or {}).items():
            if m:
                by_epoch.setdefault(e, []).append(float(m))
    if wanted_epochs is not None:
        by_epoch = {e: v for e, v in by_epoch.items() if e in wanted_epochs}
    if not by_epoch:
        return {}
    tol = config.ASTEROID_TIME_DEDUPE_DAYS
    share = max(1, cap // len(by_epoch))
    out = {}
    for e in sorted(by_epoch, key=str):
        uniq = []
        for m in sorted(by_epoch[e]):
            if not uniq or m - uniq[-1] > tol:
                uniq.append(m)
        if len(uniq) > share:
            pick = np.linspace(0, len(uniq) - 1, share).round().astype(int)
            uniq = [uniq[i] for i in sorted(set(pick.tolist()))]
        out[e] = uniq
    # trim to the cap overall (only when there are more epochs than the cap)
    total = sum(len(v) for v in out.values())
    while total > cap:
        e = max(out, key=lambda k: len(out[k]))
        if len(out[e]) <= 1:
            break
        out[e].pop()
        total -= 1
    if total > cap:
        keep = sorted(out, key=str)[:cap]
        out = {e: out[e] for e in keep}
    return out


# ---- single-epoch test --------------------------------------------------------
def _psf_excess(img, noise, py, px, sigma_px=1.4, half=3):
    """PSF-weighted flux excess at (py, px) in units of its noise."""
    ny, nx = img.shape
    y0, y1 = max(0, py - half), min(ny, py + half + 1)
    x0, x1 = max(0, px - half), min(nx, px + half + 1)
    if y1 <= y0 or x1 <= x0 or not noise or not np.isfinite(noise):
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    w = np.exp(-((yy - py) ** 2 + (xx - px) ** 2) / (2 * sigma_px ** 2))
    cut = img[y0:y1, x0:x1]
    ok = np.isfinite(cut)
    if ok.sum() < 5:
        return None
    w = w * ok
    return float(np.nansum(w * cut) / (noise * math.sqrt(float(np.sum(w * w)))))


def seen_in_one_epoch(det, ctx):
    """True when the source is bright in exactly one epoch's co-add.

    With two epochs a monopole is single-epoch by construction.  With more,
    each epoch is compared with the median of the others (PSF-weighted, in
    units of the epoch noise); a variable star brightens in several, a
    passing asteroid in one.  None when it cannot be tested.
    """
    kind = det.get("kind") or ""
    if not kind.startswith("monopole"):
        return False
    stacks, noises = ctx.get("stacks") or {}, ctx.get("noises") or {}
    order = [e for e in (ctx.get("epoch_order") or list(stacks)) if e in stacks]
    pos = det.get("position")
    if len(order) <= 2 or not pos:
        return True
    # The source is a positive excess in the epoch that holds it, whichever
    # sign it has in the difference image.
    py, px = int(round(pos[0])), int(round(pos[1]))
    vals = {}
    for e in order:
        v = _psf_excess(np.asarray(stacks[e], dtype=float), noises.get(e), py, px)
        if v is not None:
            vals[e] = v
    if len(vals) < 3:
        return True
    hits = 0
    for e, v in vals.items():
        others = np.median([u for k, u in vals.items() if k != e])
        if (v - others) > config.ASTEROID_ONE_EPOCH_SIGMA:
            hits += 1
    return hits <= 1


def _is_negative(det):
    return det.get("kind") == "monopole_negative" or det.get("sign") == -1


def present_epoch(det):
    """Which epoch of the pair holds the source: 'a', 'b' or None (either)."""
    kind = det.get("kind") or ""
    if _is_negative(det):
        return "a"                     # D = b - a < 0: present in the earlier
    if kind in ("monopole", "dipole"):
        return "b"                     # positive lobe: present in the later
    return None


# ---- the enricher ---------------------------------------------------------------
def _adapters(ctx):
    given = ctx.get("asteroid_adapters") or {}
    return (given.get("sb_ident") or JPLSbIdent(),
            given.get("sbdb") or JPLSbdb(),
            given.get("ssois") or CadcSsois())


def _date(mjd):
    return mjd_to_datetime(mjd).strftime("%Y-%m-%d")


def _mark_unavailable(dets, error):
    for d in dets:
        d["asteroid"] = {"status": "unavailable", "checked": [],
                         "n_in_field": None, "error": error}
        reasons = d.setdefault("reasons", [])
        if UNAVAILABLE_REASON not in reasons:
            reasons.append(UNAVAILABLE_REASON)


def _refresh_stats(payload):
    stats = payload.get("stats")
    if not isinstance(stats, dict):
        return
    cats = {}
    for t in payload.get("detections") or []:
        cats[t.get("category")] = cats.get(t.get("category"), 0) + 1
    stats["categories"] = cats
    stats["n_priority"] = sum(1 for t in payload.get("detections") or []
                              if (t.get("priority") or 99) <= 2)


@register("asteroids", order=10)
def check_asteroids(payload, ctx):
    dets = payload.get("detections") or []
    field = {"status": "skipped", "n_queries": 0, "n_in_field": 0, "errors": []}
    payload["asteroid_check"] = field
    if not dets:
        field["reason"] = "no detections"
        return

    ra0 = ctx.get("ra", payload.get("ra"))
    dec0 = ctx.get("dec", payload.get("dec"))
    npix = ctx.get("npix") or (payload.get("map") or {}).get("npix") or config.NPIX
    pixscale = (ctx.get("pixscale_arcsec")
                or (payload.get("map") or {}).get("pixscale_arcsec")
                or config.PIXSCALE_ARCSEC)
    half_field = npix * pixscale / 2.0                       # arcsec
    half_query = (half_field + config.ASTEROID_MATCH_MAX_ARCSEC) / 3600.0

    wanted = set()
    for d in dets:
        for e in d.get("epochs") or []:
            wanted.add(e)
    times = query_times(ctx, wanted_epochs=wanted or None)
    if ra0 is None or dec0 is None or not times:
        field["reason"] = "no exposure times" if ra0 is not None else "no field centre"
        _mark_unavailable(dets, "asteroid check skipped: " + field["reason"])
        return

    sb_ident, sbdb, ssois = _adapters(ctx)
    beta = ecliptic_latitude(ra0, dec0)
    field["ecliptic_latitude_deg"] = round(beta, 2)

    # -- 1. predictions per exposure time ---------------------------------
    predictions = {}                 # epoch -> [row, ...]
    n_ok = 0
    for e, mjds in times.items():
        for m in mjds:
            field["n_queries"] += 1
            try:
                res = sb_ident.query(ra0, dec0, m, half_query)
            except ArchiveUnavailable as ex:
                field["errors"].append(str(ex))
                continue
            n_ok += 1
            predictions.setdefault(e, []).extend(res.rows)
    if n_ok == 0:
        field["status"] = "unavailable"
        _mark_unavailable(dets, field["errors"][0] if field["errors"]
                          else "JPL sb_ident unavailable")
        return
    field["status"] = "ok" if not field["errors"] else "partial"
    field["query_mjds"] = {str(e): v for e, v in times.items()}
    in_field = {r["designation"] for rows in predictions.values() for r in rows
                if _in_box(r["ra"], r["dec"], ra0, dec0, half_field)}
    field["n_in_field"] = len(in_field)
    field["objects"] = sorted(in_field)

    sbdb_cache, ssois_cache = {}, {}

    def sbdb_info(key):
        if key not in sbdb_cache:
            try:
                sbdb_cache[key] = sbdb.lookup(key)
            except ArchiveUnavailable as ex:
                sbdb_cache[key] = None
                field["errors"].append(str(ex))
        return sbdb_cache[key]

    # -- 2. per detection -----------------------------------------------------
    for d in dets:
        epochs = list(d.get("epochs") or [])
        ra, dec = d.get("ra"), d.get("dec")
        rec = {"status": "no_match", "checked": ["jpl_sb_ident"],
               "n_in_field": 0}
        d["asteroid"] = rec
        if ra is None or dec is None or len(epochs) != 2:
            rec.update(status="unavailable", error="detection has no sky position or epoch pair")
            continue
        ea, eb = epochs
        side = present_epoch(d)
        sides = [side] if side else ["a", "b"]
        checked_any = False
        cands = []
        names = set()
        for s in sides:
            e = ea if s == "a" else eb
            if e not in times:
                continue
            if e in predictions:
                checked_any = True
            for r in predictions.get(e, []):
                if _in_box(r["ra"], r["dec"], ra0, dec0, half_field):
                    names.add(r["designation"])
                sep = sep_arcsec(ra, dec, r["ra"], r["dec"])
                if sep <= config.ASTEROID_MATCH_MAX_ARCSEC:
                    cands.append((sep, s, r))
        rec["n_in_field"] = len(names)
        if not checked_any:
            rec.update(status="unavailable",
                       error="no JPL prediction for the epoch this source is in")
            if d.get("category") == "CANDIDATE":
                d.setdefault("reasons", []).append(UNAVAILABLE_REASON)
            continue

        match = None
        for sep, s, r in sorted(cands, key=lambda c: c[0]):
            info = sbdb_info(r["key"])
            unc = (info or {}).get("pos_unc_arcsec")
            radius = config.ASTEROID_MATCH_FLOOR_ARCSEC
            if unc:
                radius = max(radius, config.ASTEROID_MATCH_UNC_K * unc)
            radius = min(radius, config.ASTEROID_MATCH_MAX_ARCSEC)
            if sep <= radius:
                match = (sep, s, r, info, unc, radius)
                break

        if match is None:
            _no_match(d, rec, beta, ctx)
            continue
        _apply_match(d, rec, match, ssois, ssois_cache, field)

    _refresh_stats(payload)


def _no_match(d, rec, beta, ctx):
    if d.get("category") != "CANDIDATE":
        return
    kind = d.get("kind") or ""
    if not kind.startswith("monopole"):
        return
    if abs(beta) >= config.ECLIPTIC_LATITUDE_WARN_DEG:
        return
    if seen_in_one_epoch(d, ctx) is False:
        return
    d["category"] = "POSSIBLE_UNKNOWN_ASTEROID"
    d["priority"] = config.ASTEROID_POSSIBLE_PRIORITY
    d.setdefault("reasons", []).insert(
        0, f"seen in one epoch only, {abs(beta):.0f}° from the ecliptic, and "
           "no known asteroid predicted here — possibly an uncatalogued or "
           "poorly-placed asteroid")


def _apply_match(d, rec, match, ssois, ssois_cache, field):
    sep, side, r, info, unc, radius = match
    info = info or {}
    key = r["key"]
    rec["status"] = "match"
    if info:
        rec["checked"].append("jpl_sbdb")
    designation = info.get("designation") or r["designation"]
    m = {
        "designation": designation,
        "orbit_class": info.get("orbit_class"),
        "neo": info.get("neo"),
        "h_mag": info.get("h_mag"),
        "v_mag": r.get("v_mag"),
        "sep_arcsec": round(sep, 2),
        "match_radius_arcsec": round(radius, 1),
        "epoch": side, "mjd": r.get("mjd"),
        "jpl_ra": r["ra"], "jpl_dec": r["dec"],
        "pos_unc_arcsec": unc,
        "condition_code": info.get("condition_code"),
        "ssois_agrees": None, "ssois_sep_arcsec": None,
        "sbdb_url": info.get("sbdb_url") or _sbdb_url(key),
        "ssois_url": None,
    }
    rec["match"] = m

    # -- SSOIS cross-check ------------------------------------------------
    mjd = r.get("mjd")
    gap = config.SSOIS_MAX_BRACKET_DAYS
    ssois_note = None
    if mjd is not None:
        m["ssois_url"] = ssois_mod.human_url(key, mjd - gap, mjd + gap)
        ck = (key, round(mjd, 2))
        if ck not in ssois_cache:
            try:
                ssois_cache[ck] = ssois.search(key, mjd - gap, mjd + gap)
            except ArchiveUnavailable as ex:
                ssois_cache[ck] = ex
        res = ssois_cache[ck]
        if isinstance(res, Exception):
            ssois_note = "SSOIS cross-check unavailable"
            field["errors"].append(str(res))
            m["ssois_error"] = str(res)
        else:
            rec["checked"].append("ssois")
            m["ssois_n_images"] = len(res.rows)
            m["ssois_neossat"] = res.meta.get("n_neossat", 0)
            pos = ssois_mod.position_at(res.rows, mjd, gap)
            if pos is None:
                ssois_note = "no SSOIS images close enough in time to cross-check"
            else:
                s = sep_arcsec(pos["ra"], pos["dec"], r["ra"], r["dec"])
                m["ssois_sep_arcsec"] = round(s, 2)
                m["ssois_agrees"] = bool(s <= config.SSOIS_AGREEMENT_ARCSEC)

    # -- reasons / category ---------------------------------------------
    cls = info.get("orbit_class_name") or info.get("orbit_class")
    when = _date(mjd) if mjd is not None else "the exposure time"
    text = f"known asteroid {designation}" + (f" ({cls.lower()})" if cls else "") \
        + f" was here on {when} — JPL"
    if m["ssois_agrees"] is True:
        text += "; CADC SSOIS agrees"
    elif m["ssois_agrees"] is False:
        text += f"; CADC SSOIS differs by {m['ssois_sep_arcsec']:.0f}\""
    reasons = d.setdefault("reasons", [])
    extra = []
    if m["ssois_agrees"] is False:
        extra.append(DISAGREE_REASON)
    elif ssois_note:
        extra.append(ssois_note)
    if info.get("neo"):
        extra.append("near-Earth object: the geocentric prediction can be off by "
                     "more than a pixel for a low-orbit observatory")
    if not info:
        extra.append("JPL SBDB details unavailable for this object")

    if d.get("category") in REPLACEABLE:
        d["category"] = "KNOWN_ASTEROID"
        d["verdict"] = "KNOWN"
        d["priority"] = config.ASTEROID_KNOWN_PRIORITY
        d["confidence"] = ("high" if sep <= config.ASTEROID_MATCH_FLOOR_ARCSEC
                           and m["ssois_agrees"] is not False else "medium")
        reasons.insert(0, text)
    else:
        # already explained by a catalogued star / variable: leave it be
        reasons.append(text + " (may contribute)")
    reasons.extend(extra)


def _sbdb_url(key):
    from spherexlens.ingest.jpl import sbdb_human_url
    return sbdb_human_url(key)
