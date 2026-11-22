"""
spherexlens.ingest.links
"Look at it elsewhere" links for a sky position (CONTRACT.md, Phase 2):

    wiseview     byw.tools WiseView flip-book (unWISE/NEOWISE epochs)
    irsa_finder  IRSA Finder Chart (DSS/SDSS/2MASS/WISE cutouts)
    simbad       SIMBAD coordinate query
    gaia_mast    MAST portal, Gaia cone at the position

The "links" enricher (order 20) adds ``detection["links"]`` to every
detection with a sky position, and ``payload["links"]`` for the target.
Pure string building: no network, cannot slow the dashboard.
"""
import json
from urllib.parse import quote, urlencode

import config
from spherexlens.enrich import register

# A real WiseView URL; only ra / dec / size are changed, every other
# parameter (band, speed, contrast, epoch windows, synthetic-source slots...)
# is kept exactly as WiseView itself writes it.
WISEVIEW_TEMPLATE = (
    "http://byw.tools/wiseview#ra=133.786245&dec=-7.244372&size=176&band=2"
    "&speed=500&minbright=-50.0000&maxbright=500.0000&window=0.5&diff_window=1"
    "&linear=1&color=&zoom=9&border=0&gaia=0&invert=1&maxdyr=0&scandir=0"
    "&neowise=0&diff=0&outer_epochs=0&unique_window=1&smooth_scan=0&shift=0"
    "&pmra=0&pmdec=0&synth_a=0&synth_a_sub=0&synth_a_ra=&synth_a_dec="
    "&synth_a_w1=&synth_a_w2=&synth_a_pmra=0&synth_a_pmdec=0&synth_a_mjd="
    "&synth_b=0&synth_b_sub=0&synth_b_ra=&synth_b_dec=&synth_b_w1=&synth_b_w2="
    "&synth_b_pmra=0&synth_b_pmdec=0&synth_b_mjd=")

IRSA_FINDER = "https://irsa.ipac.caltech.edu/applications/finderchart/"
SIMBAD_COO = "https://simbad.cds.unistra.fr/simbad/sim-coo"
MAST_PORTAL = "https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html"


def wiseview_url(ra, dec, size_arcsec=None):
    size = int(round(size_arcsec or config.WISEVIEW_SIZE_ARCSEC))
    base, frag = WISEVIEW_TEMPLATE.split("#", 1)
    parts = []
    for item in frag.split("&"):
        key = item.split("=", 1)[0]
        if key == "ra":
            item = f"ra={ra:.6f}"
        elif key == "dec":
            item = f"dec={dec:.6f}"
        elif key == "size":
            item = f"size={size}"
        parts.append(item)
    return base + "#" + "&".join(parts)


def irsa_finder_url(ra, dec, size_deg=0.0833):
    return IRSA_FINDER + "?" + urlencode(
        {"locstr": f"{ra:.6f} {dec:+.6f}", "subsize": f"{size_deg:.4f}"})


def simbad_url(ra, dec, radius_arcsec=10):
    return SIMBAD_COO + "?" + urlencode(
        {"Coord": f"{ra:.6f} {dec:+.6f}", "CooFrame": "ICRS",
         "Radius": radius_arcsec, "Radius.unit": "arcsec"})


def gaia_mast_url(ra, dec, release=None):
    rel = (release or config.GAIA_RELEASE).upper()
    text = f"{ra:.6f} {dec:+.6f}"
    query = {"service": f"GAIA{rel}", "inputText": text,
             "paramsService": f"Mast.Catalogs.Gaia{rel}.Cone",
             "title": f"Gaia ({rel}): {text}", "columns": "*"}
    return MAST_PORTAL + "?searchQuery=" + quote(
        json.dumps(query, separators=(",", ":")), safe="")


def links_for(ra, dec, size_arcsec=None):
    if ra is None or dec is None:
        return None
    ra, dec = float(ra) % 360.0, float(dec)
    return {"wiseview": wiseview_url(ra, dec, size_arcsec),
            "irsa_finder": irsa_finder_url(ra, dec),
            "simbad": simbad_url(ra, dec),
            "gaia_mast": gaia_mast_url(ra, dec)}


def _target_position(payload, ctx):
    tp = payload.get("target_position") or {}
    if tp.get("ra") is not None and tp.get("dec") is not None:
        return tp["ra"], tp["dec"]
    mp = payload.get("map") or {}
    if mp.get("ra") is not None:
        return mp["ra"], mp.get("dec")
    return (ctx or {}).get("ra"), (ctx or {}).get("dec")


@register("links", order=20)
def add_links(payload, ctx):
    ra, dec = _target_position(payload, ctx)
    target_links = links_for(ra, dec)
    if target_links:
        payload["links"] = target_links
    for det in payload.get("detections") or []:
        got = links_for(det.get("ra"), det.get("dec"))
        if got:
            det["links"] = got
