"""
step1_ingest.py

Queries the IRSA SIA2 service for SPHEREx Level-2 cutouts of the five
mandatory test targets, downloads the FITS files, and queries three
catalogs (Gaia DR3, AllWISE, AAVSO VSX) for known sources within 15
arcminutes of each target.

Verified against the live service: IRSA serves SIA2 at
https://irsa.ipac.caltech.edu/SIA and requires the SIA2 "POS=CIRCLE ra dec r"
constraint.  Sending the legacy "POS=ra,dec" + "SIZE=" pair instead routes the
request to the old SIA v1 handler, which rejects COLLECTION with
"UsageFault: BAD_REQUEST: Unknown parameter: collection".

A full-frame Level-2 spectral image is ~70 MB (2040x2040, six extensions), so
each exposure is fetched through IRSA's cutout service
(https://irsa.ipac.caltech.edu/ibe/cutout?path=...&center=...&size=...), which
returns only the region around the target (~5 MB, ~117x117 px at 6.15"/px).

Output: data_cache/<target>_meta.json and data_cache/*.fits

Run: python step1_ingest.py [--collection spherex_qr2] [--size 0.2]
"""

import os
import json
import time
import argparse
import xml.etree.ElementTree as ET

import requests
import numpy as np
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astroquery.vizier import Vizier

from step2_coadd import BAD_BITS

# ---------------------------------------------------------------------------
# Target definitions
# ---------------------------------------------------------------------------
TARGETS = {
    "Barnards_Star": {"ra": 269.452083, "dec": 4.693333, "type": "Mover_M4V",
                      "common": "Barnard's Star"},
    "Epsilon_Indi_B": {"ra": 331.043750, "dec": -56.782778, "type": "Mover_TDwarf",
                       "common": "Epsilon Indi B"},
    "WISE_0855": {"ra": 133.795129, "dec": -7.245147, "type": "Mover_YDwarf",
                  "common": "WISE 0855"},
    "RW_Aurigae": {"ra": 76.956250, "dec": 30.401389, "type": "Variable_TTauri",
                   "common": "RW Aurigae"},
    "R_Draconis": {"ra": 248.167500, "dec": 66.754722, "type": "Variable_Mira",
                   "common": "R Draconis"},
}

CACHE = os.environ.get("SPHEREX_CACHE", "data_cache")
os.makedirs(CACHE, exist_ok=True)

# Live IRSA services.
IRSA_SIA2 = "https://irsa.ipac.caltech.edu/SIA"
IRSA_CUTOUT = "https://irsa.ipac.caltech.edu/ibe/cutout"
IRSA_TAP_SYNC = "https://irsa.ipac.caltech.edu/TAP/sync"

# VizieR mirrors, tried in order (the CDS host is not always reachable).
VIZIER_MIRRORS = ("vizier.cfa.harvard.edu",
                 )

# One short-wavelength and one mid-wavelength array per epoch.
DEFAULT_BANDS = ("SPHEREx-D1", "SPHEREx-D4")

VOTABLE_NS = "{http://www.ivoa.net/xml/VOTable/v1.3}"


def parse_votable(xml_bytes):
    """
    Turn an IRSA SIA2 VOTable into a list of dicts keyed by FIELD name.

    astropy's votable reader exposes these columns as col_0..col_N on this
    endpoint, so the FIELD order is mapped explicitly.
    """
    root = ET.fromstring(xml_bytes)
    for info in root.iter(VOTABLE_NS + "INFO"):
        if info.get("name") == "QUERY_STATUS" and info.get("value") != "OK":
            raise RuntimeError(f"SIA2 rejected the query: {info.text}")

    fields = [f.get("name") for f in root.iter(VOTABLE_NS + "FIELD")]
    rows = []
    for tr in root.iter(VOTABLE_NS + "TR"):
        cells = [(td.text or "").strip() for td in tr.findall(VOTABLE_NS + "TD")]
        if len(cells) == len(fields):
            rows.append(dict(zip(fields, cells)))
    return rows


def query_sia2(ra, dec, radius_deg=0.1, collection="spherex_qr2", retries=3):
    """
    SIA2 cone search for SPHEREx Level-2 spectral images (obs_collection,
    bandpass, footprint centre, start time and the access URL per row).

    The VOTable response is cached under data_cache/, so re-runs and offline
    work never re-query IRSA for the same cone.
    """
    cache = os.path.join(CACHE, f"sia2_{collection}_{ra:.4f}_{dec:.4f}_{radius_deg}.xml")
    if os.path.exists(cache):
        print(f"  SIA2 {collection} (cached {os.path.basename(cache)})")
        with open(cache, "rb") as f:
            return parse_votable(f.read())

    params = {"POS": f"CIRCLE {ra} {dec} {radius_deg}",
              "COLLECTION": collection}
    print(f"  SIA2 {collection}: POS=CIRCLE {ra:.4f} {dec:.4f} {radius_deg}")
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(IRSA_SIA2, params=params, timeout=300)
            resp.raise_for_status()
            rows = parse_votable(resp.content)
            with open(cache, "wb") as f:
                f.write(resp.content)
            print(f"  -> {len(rows)} overlapping Level-2 images.")
            return rows
        except Exception as e:
            last_error = e
            print(f"  SIA2 attempt {attempt + 1} failed: {type(e).__name__}")
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"SIA2 query failed after {retries} attempts: {last_error}")


def group_epochs(rows, gap_days=30.0):
    """Split time-sorted rows into epochs separated by more than gap_days."""
    ordered = sorted(rows, key=lambda r: float(r["t_min"]))
    groups = []
    for row in ordered:
        t = float(row["t_min"])
        if not groups or t - float(groups[-1][-1]["t_min"]) > gap_days:
            groups.append([row])
        else:
            groups[-1].append(row)
    return groups


def _parse_s_region(s_region):
    """Polygon vertices [(ra, dec), ...] from an STC-S 'POLYGON ICRS ...' string."""
    if not s_region:
        return []
    parts = s_region.replace(",", " ").split()
    if not parts or parts[0].upper() != "POLYGON":
        return []
    nums = []
    for token in parts[1:]:
        try:
            nums.append(float(token))
        except ValueError:
            continue          # frame token such as ICRS
    if len(nums) < 6 or len(nums) % 2:
        return []
    return list(zip(nums[0::2], nums[1::2]))


def interior_distance_deg(ra, dec, polygon):
    """
    Distance in degrees from (ra, dec) to the footprint boundary.

    Positive inside the footprint, negative outside.  Used to keep only
    exposures where the target sits comfortably inside the detector, because
    IRSA's cutout service returns a clipped strip -- without the target in it --
    whenever the requested box runs off the edge of the array.
    """
    if len(polygon) < 3:
        return None
    cosd = np.cos(np.radians(dec))
    p = np.array([[(v[0] - ra) * cosd, v[1] - dec] for v in polygon])
    n = len(p)

    inside = False
    for i in range(n):
        x1, y1 = p[i]
        x2, y2 = p[(i + 1) % n]
        if (y1 > 0) != (y2 > 0):
            x_cross = x1 + (0 - y1) * (x2 - x1) / (y2 - y1)
            if x_cross > 0:
                inside = not inside

    dmin = np.inf
    for i in range(n):
        a = p[i]
        b = p[(i + 1) % n]
        ab = b - a
        denom = float(ab @ ab)
        t = 0.0 if denom == 0 else float(np.clip(-(a @ ab) / denom, 0, 1))
        dmin = min(dmin, float(np.hypot(*(a + t * ab))))
    return dmin if inside else -dmin


def select_exposures(rows, ra, dec, bands=DEFAULT_BANDS, per_slot=3):
    """
    Order candidate images for each (epoch, band) slot.

    The first image of an epoch is not always usable: the target can sit close
    to the detector edge, so IRSA returns a clipped strip that does not contain
    it, and a whole detector can be flagged out.  Candidates are therefore
    ranked by how deep inside their footprint the target lies (from the SIA2
    s_region polygon) and the caller validates them one by one.
    """
    usable = [r for r in rows
              if r.get("dataproduct_type") == "image"
              and r.get("access_url", "").endswith(".fits")]
    groups = group_epochs(usable)
    if not groups:
        return [], 0

    picked = [groups[0]] if len(groups) == 1 else [groups[0], groups[-1]]
    slots = []
    for group in picked:
        for band in bands:
            in_band = [r for r in group if r.get("energy_bandpassname") == band]
            pool = in_band if in_band else group
            deeper = [r for r in pool
                      if (interior_distance_deg(ra, dec,
                                                _parse_s_region(r.get("s_region")))
                          or -1) > 0.05]      # target more than 3' inside
            pool = deeper or pool
            pool = sorted(pool, key=lambda r: -(interior_distance_deg(
                ra, dec, _parse_s_region(r.get("s_region"))) or -1))
            slots.append(pool[:per_slot])
    return slots, len(groups)


def _separation_deg(ra1, dec1, ra2, dec2):
    """Great-circle separation in degrees."""
    d = np.radians
    dra = (ra2 - ra1)
    return float(np.degrees(np.arccos(np.clip(
        np.sin(d(dec1)) * np.sin(d(dec2))
        + np.cos(d(dec1)) * np.cos(d(dec2)) * np.cos(d(dra)), -1, 1))))


def validate_cutout(path, ra, dec, min_margin=12, max_masked=0.5):
    """
    Accept a cutout only if the target sits well inside it and enough pixels
    survive the FLAGS mask.  Returns (ok, reason).
    """
    try:
        with fits.open(path) as hdul:
            hdu = hdul["IMAGE"] if "IMAGE" in hdul else hdul[0]
            ny, nx = hdu.data.shape
            flags = hdul["FLAGS"].data if "FLAGS" in hdul else None
            wcs = WCS(hdu.header)
            pix = wcs.all_world2pix([[ra, dec]], 0, tolerance=1e-8,
                                    maxiter=100, quiet=True)[0]
    except Exception as e:
        return False, f"unreadable ({e})"

    x, y = float(pix[0]), float(pix[1])
    if not (min_margin <= x <= nx - 1 - min_margin
            and min_margin <= y <= ny - 1 - min_margin):
        return False, f"target outside cutout ({x:.0f},{y:.0f} of {nx}x{ny})"

    if flags is not None:
        masked = float(((flags & BAD_BITS) != 0).mean())
        if masked > max_masked:
            return False, f"{100 * masked:.0f}% of pixels masked"
    return True, "ok"


def download_cutout(row, ra, dec, size_deg=0.2, overwrite=False):
    """
    Fetch a small region of one Level-2 image via IRSA's cutout service.

    The service takes the IBE path (everything after /ibe/data/), the sky
    centre and a size in degrees, and returns a normal multi-extension FITS
    with IMAGE, FLAGS, VARIANCE, ZODI, PSF and WCS-WAVE.
    """
    url = row["access_url"]
    base = os.path.basename(url)
    fname = os.path.join(CACHE, base.replace(".fits", "_cut.fits"))
    if os.path.exists(fname) and not overwrite:
        print(f"    cached {os.path.basename(fname)}")
        return fname

    params = {"path": url.split("/ibe/data/")[-1],
              "center": f"{ra},{dec}",
              "size": str(size_deg)}
    try:
        resp = requests.get(IRSA_CUTOUT, params=params, timeout=300)
    except Exception as e:
        print(f"    cutout request failed: {e}")
        return None
    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or "fits" not in ctype:
        print(f"    cutout rejected ({resp.status_code}): {resp.text[:120]}")
        return None

    with open(fname, "wb") as f:
        f.write(resp.content)
    print(f"    {os.path.basename(fname)}  {len(resp.content) / 1e6:.1f} MB")
    return fname


def download_spherex_cutouts(ra, dec, radius_deg=0.1, collection="spherex_qr2",
                             size_deg=0.2, bands=DEFAULT_BANDS,
                             overwrite=False):
    """
    Query SIA2 for SPHEREx Level-2 images and download validated cutouts.

    For every (epoch, band) slot the candidate images are tried in order of
    footprint-centre distance until one yields a cutout that contains the
    target and is not mostly flagged.

    Returns (local paths, number of SIA2 rows, number of epochs found).
    """
    rows = query_sia2(ra, dec, radius_deg=radius_deg, collection=collection)
    slots, n_epochs = select_exposures(rows, ra, dec, bands=bands)
    print(f"  {n_epochs} epoch(s), {len(slots)} (epoch, band) slot(s).")

    paths = []
    epochs = []
    for slot_index, candidates in enumerate(slots):
        epoch = slot_index // max(1, len(bands))
        ordered = sorted(
            candidates,
            key=lambda r: _separation_deg(ra, dec,
                                          float(r.get("s_ra", ra)),
                                          float(r.get("s_dec", dec))))
        for row in ordered:
            fname = download_cutout(row, ra, dec, size_deg=size_deg,
                                    overwrite=overwrite)
            if not fname:
                continue
            ok, reason = validate_cutout(fname, ra, dec)
            band = row.get("energy_bandpassname", "?")
            if ok:
                print(f"    accepted epoch {epoch + 1} {band} "
                      f"({os.path.basename(fname)})")
                paths.append(fname)
                epochs.append(epoch + 1)
                break
            print(f"    rejected epoch {epoch + 1} {band}: {reason}")
        time.sleep(1)
    return paths, epochs, len(rows), n_epochs


# ---------------------------------------------------------------------------
# Download catalogs via VizieR
# ---------------------------------------------------------------------------
def query_irsa_tap_gaia(ra, dec, radius_deg, limit=300):
    """
    Gaia DR3 cone search through IRSA's TAP service.

    Used as a fallback when the VizieR mirrors are unreachable; IRSA is the
    same host that serves the images, so it is usually up when VizieR is not.
    """
    adql = ("SELECT TOP %d ra, dec, pmra, pmdec, phot_g_mean_mag "
            "FROM gaia.gaia_dr3_source WHERE 1=CONTAINS(POINT('ICRS',ra,dec), "
            "CIRCLE('ICRS',%.6f,%.6f,%.6f))" % (limit, ra, dec, radius_deg))
    resp = None
    for attempt in range(3):
        try:
            resp = requests.post(IRSA_TAP_SYNC,
                                 data={"REQUEST": "doQuery", "LANG": "ADQL",
                                       "FORMAT": "tsv", "QUERY": adql},
                                 timeout=300)
            resp.raise_for_status()
            break
        except Exception as e:
            print(f"    IRSA TAP attempt {attempt + 1} failed: {type(e).__name__}")
            time.sleep(10)
    if resp is None:
        return []

    text = resp.text.strip()
    if not text or text.startswith("<"):
        return []
    rows = []
    for line in text.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        try:
            rows.append({
                "ra": float(parts[0]),
                "dec": float(parts[1]),
                "pm_ra": float(parts[2]) if parts[2] else 0.0,
                "pm_de": float(parts[3]) if parts[3] else 0.0,
                "gmag": float(parts[4]) if parts[4] else 99.0,
            })
        except ValueError:
            continue
    return rows


def download_catalogs(ra, dec, radius_arcmin=15):
    """
    Query Gaia DR3, AllWISE and VSX for sources within radius.

    Only the few columns the triage step needs are requested (asking for
    "**" pulls every column of a billion-row catalogue and takes minutes), and
    the VizieR mirrors are tried in order because the default CDS host is not
    reachable from every network.

    Returns a dict: {"gaia": [...], "wise": [...], "vsx": [...]}
    """
    print(f"  Querying catalogs within {radius_arcmin}'...")
    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    out = {"gaia": [], "wise": [], "vsx": []}

    # Gaia comes from IRSA TAP first: it is the same host that serves the
    # images, so it is the most reliable path; VizieR is the fallback.
    out["gaia"] = query_irsa_tap_gaia(ra, dec, radius_arcmin / 60.0)
    if out["gaia"]:
        print(f"    gaia: {len(out['gaia'])} sources (IRSA TAP)")

    queries = {
        "gaia": ("I/355/gaiadr3", ["RA_ICRS", "DE_ICRS", "pmRA", "pmDE",
                                   "Gmag"]),
        "wise": ("II/328/allwise", ["RAJ2000", "DEJ2000", "W1mag", "W2mag"]),
        "vsx": ("B/vsx/vsx", ["RAJ2000", "DEJ2000", "Name", "Type"]),
    }

    for key, (catalog, cols) in queries.items():
        if key == "gaia" and out["gaia"]:
            continue
        for mirror in VIZIER_MIRRORS:
            try:
                viz = Vizier(row_limit=300, columns=cols)
                viz.VIZIER_SERVER = mirror
                viz.TIMEOUT = 30
                res = viz.query_region(coord, radius=radius_arcmin * u.arcmin,
                                       catalog=catalog)
                if len(res) == 0:
                    print(f"    {key}: no sources ({mirror})")
                    break
                tab = res[0]
                for row in tab:
                    try:
                        if key == "gaia":
                            out["gaia"].append({
                                "ra": float(row["RA_ICRS"]),
                                "dec": float(row["DE_ICRS"]),
                                "pm_ra": float(row["pmRA"]) if row["pmRA"] is not None else 0.0,
                                "pm_de": float(row["pmDE"]) if row["pmDE"] is not None else 0.0,
                                "gmag": float(row["Gmag"]) if row["Gmag"] is not None else 99.0,
                            })
                        elif key == "wise":
                            out["wise"].append({
                                "ra": float(row["RAJ2000"]),
                                "dec": float(row["DEJ2000"]),
                                "w1": float(row["W1mag"]) if row["W1mag"] is not None else 99.0,
                                "w2": float(row["W2mag"]) if row["W2mag"] is not None else 99.0,
                            })
                        else:
                            out["vsx"].append({
                                "ra": float(row["RAJ2000"]),
                                "dec": float(row["DEJ2000"]),
                                "name": str(row["Name"]),
                                "type": str(row["Type"]),
                            })
                    except Exception:
                        continue
                print(f"    {key}: {len(out[key])} sources ({mirror})")
                break
            except Exception as e:
                print(f"    {key}: {mirror} failed ({type(e).__name__})")
                continue

    if not out["gaia"]:
        out["gaia"] = query_irsa_tap_gaia(ra, dec, radius_arcmin / 60.0)
        print(f"    gaia: {len(out['gaia'])} sources (IRSA TAP fallback)")

    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Ingest real SPHEREx data.")
    parser.add_argument("--collection", default="spherex_qr2",
                        help="SIA2 collection (spherex_qr2 / spherex_qr3)")
    parser.add_argument("--radius", type=float, default=0.1,
                        help="SIA2 cone radius in degrees")
    parser.add_argument("--size", type=float, default=0.2,
                        help="cutout width/height in degrees")
    parser.add_argument("--bands", nargs="*", default=list(DEFAULT_BANDS))
    parser.add_argument("--targets", nargs="*", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-catalogs", action="store_true")
    args = parser.parse_args()

    names = args.targets or list(TARGETS)
    for name in names:
        meta = TARGETS[name]
        print(f"\n=== {name} ({meta['common']}) ===")
        paths, epochs, n_rows, n_epochs = download_spherex_cutouts(
            meta["ra"], meta["dec"],
            radius_deg=args.radius,
            collection=args.collection,
            size_deg=args.size,
            bands=tuple(args.bands),
            overwrite=args.overwrite,
        )
        cats = ({"gaia": [], "wise": [], "vsx": []} if args.skip_catalogs
                else download_catalogs(meta["ra"], meta["dec"]))

        if not paths:
            print("  nothing downloaded -- skipping this target.")
            continue

        record = {
            "target": name,
            "meta": meta,
            "paths": paths,
            "epochs": epochs,
            "catalogs": cats,
            "collection": args.collection,
            "cutout_size_deg": args.size,
            "sia2_rows": n_rows,
            "n_epochs": n_epochs,
        }
        with open(os.path.join(CACHE, f"{name}_meta.json"), "w") as f:
            json.dump(record, f, indent=2)

        total_cat = sum(len(v) for v in cats.values())
        print(f"  saved {len(paths)} cutouts and {total_cat} catalog sources.")
        time.sleep(1)

    print("\nAll targets processed. Ready for step2.")


if __name__ == "__main__":
    main()
