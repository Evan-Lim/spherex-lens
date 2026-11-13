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

Run: python step1_ingest.py [--collection spherex_dr1] [--size 0.2]
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
from catalog_tools import target_position, year_to_mjd, sep_arcsec

# Epoch (Julian year) the moving targets are re-centred on at ingest: the
# middle of the QR2/QR3 coverage, so no exposure is more than ~0.6 yr off.
INGEST_EPOCH = 2026.0
# IRSA SIA2 listing: 10 s to connect, 90 s to answer.  Three attempts plus
# back-off stay near 6 minutes, inside Cloud Run's 15-minute request limit.
SIA2_TIMEOUT = (10, 90)

# ---------------------------------------------------------------------------
# Target definitions
# ---------------------------------------------------------------------------
# Fast movers carry pm_ra/pm_dec (mas/yr, mu_alpha* incl. cos dec) and
# pos_epoch (Julian year of ra/dec). Their ra/dec are the Gaia DR3 J2016.0
# values (Sirius A: Hipparcos, J2000; WISE 0855: fitted from NEOWISE), and the
# cutouts are centred on the position propagated to INGEST_EPOCH -- Barnard's
# Star alone moves ~4.4' between J2000 and the SPHEREx epochs.
TARGETS = {
    "Barnards_Star": {"ra": 269.44850252544, "dec": 4.73942005111,
                      "pm_ra": -801.551, "pm_dec": 10362.394, "pos_epoch": 2016.0,
                      "type": "Mover_M4V", "common": "Barnard's Star"},
    "Epsilon_Indi_B": {"ra": 331.07645259656, "dec": -56.79381206688,
                       "pm_ra": 3981.977, "pm_dec": -2466.832, "pos_epoch": 2016.0,
                       "type": "Mover_TDwarf", "common": "Epsilon Indi B"},
    # Not in Gaia: position and motion fitted by step7_neowise to the 23
    # AllWISE+NEOWISE visits 2010-2024 (pm -8126+-44, +665+-17 mas/yr).
    "WISE_0855": {"ra": 133.782364, "dec": -7.243937,
                  "pm_ra": -8125.8, "pm_dec": 664.6, "pos_epoch": 2016.0,
                  "type": "Mover_YDwarf", "common": "WISE 0855"},
    "RW_Aurigae": {"ra": 76.956250, "dec": 30.401389, "type": "Variable_TTauri",
                   "common": "RW Aurigae"},
    "R_Draconis": {"ra": 248.167500, "dec": 66.754722, "type": "Variable_Mira",
                   "common": "R Draconis"},

    # --- extra fields: famous / interesting objects (not in the brief) ---
    "Andromeda_M31": {"ra": 10.684700, "dec": 41.269167, "type": "Galaxy_Spiral",
                      "common": "Andromeda Galaxy (M31)"},
    "Orion_M42": {"ra": 83.822083, "dec": -5.391111, "type": "Nebula_HII",
                  "common": "Orion Nebula (M42)"},
    "Pleiades_M45": {"ra": 56.750000, "dec": 24.116667, "type": "Cluster_Open",
                     "common": "Pleiades (M45)"},
    "Crab_M1": {"ra": 83.633083, "dec": 22.014500, "type": "Supernova_Remnant",
                "common": "Crab Nebula (M1)"},
    "Whirlpool_M51": {"ra": 202.469575, "dec": 47.195258, "type": "Galaxy_Spiral",
                      "common": "Whirlpool Galaxy (M51)"},
    "SgrA_star": {"ra": 266.416817, "dec": -29.007825, "type": "Galactic_Centre",
                  "common": "Sagittarius A*"},
    "Proxima_Cen": {"ra": 217.39232147201, "dec": -62.67607511677,
                    "pm_ra": -3781.741, "pm_dec": 769.465, "pos_epoch": 2016.0,
                    "type": "Mover_M5V", "common": "Proxima Centauri"},
    "TRAPPIST_1": {"ra": 346.62652162764, "dec": -5.04352831943,
                   "pm_ra": 930.788, "pm_dec": -479.038, "pos_epoch": 2016.0,
                   "type": "Mover_M8V", "common": "TRAPPIST-1"},
    "Betelgeuse": {"ra": 88.792938, "dec": 7.407064, "type": "Variable_Supergiant",
                   "common": "Betelgeuse"},
    # Sirius A is too bright for Gaia (the Gaia high-PM source here is Sirius B)
    "Sirius": {"ra": 101.287155, "dec": -16.716116,
               "pm_ra": -546.01, "pm_dec": -1223.07, "pos_epoch": 2000.0,
               "type": "Star_A1V", "common": "Sirius"},
    "Sombrero_M104": {"ra": 189.997633, "dec": -11.623051, "type": "Galaxy_Spiral",
                      "common": "Sombrero Galaxy (M104)"},
    "Ring_M57": {"ra": 283.395833, "dec": 33.029167, "type": "Nebula_Planetary",
                 "common": "Ring Nebula (M57)"},
    "Quasar_3C273": {"ra": 187.277912, "dec": 2.052021, "type": "Quasar",
                     "common": "Quasar 3C 273"},
    # The LMC is deliberately *not* here: the QR2 rows inside a 9' box at
    # Dec -69.8 all fall in a single visit, so there is nothing to subtract.
    # step1 keeps it one line away if a later release adds a second epoch.
    # "LMC": {"ra": 80.894166, "dec": -69.756111, "type": "Galaxy_Dwarf",
    #         "common": "Large Magellanic Cloud"},
}

CACHE = os.environ.get("SPHEREX_CACHE", "data_cache")
os.makedirs(CACHE, exist_ok=True)

# Live IRSA services.
IRSA_SIA2 = "https://irsa.ipac.caltech.edu/SIA"
IRSA_CUTOUT = "https://irsa.ipac.caltech.edu/ibe/cutout"
IRSA_TAP_SYNC = "https://irsa.ipac.caltech.edu/TAP/sync"

# VizieR mirrors, tried in order. Harvard first: it is the one that answers
# from most networks; the CDS primary and NAOJ often time out. Note that the
# Harvard mirror does NOT carry II/328/allwise -- AllWISE comes from IRSA TAP.
VIZIER_MIRRORS = ("vizier.cfa.harvard.edu",
                  "vizier.cds.unistra.fr",
                  "vizier.nao.ac.jp")

# Row caps for the IRSA TAP cone searches. The densest preset field (Sgr A*)
# holds ~20k Gaia DR3 sources inside 15', so 50k keeps every field complete;
# ORDER BY magnitude means that if a cap is ever hit, the faintest go first.
GAIA_TAP_LIMIT = 50000
WISE_TAP_LIMIT = 50000

# Every SPHEREx detector array, so a field can be co-added in all six bands.
# Override with --bands for a quick two-band run.
DEFAULT_BANDS = ("SPHEREx-D1", "SPHEREx-D2", "SPHEREx-D3",
                 "SPHEREx-D4", "SPHEREx-D5", "SPHEREx-D6")

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


def query_sia2(ra, dec, radius_deg=0.1, collection="spherex_dr1", retries=3,
               use_cache=True):
    """
    SIA2 cone search for SPHEREx Level-2 spectral images (obs_collection,
    bandpass, footprint centre, start time and the access URL per row).

    The VOTable response is cached under data_cache/ by default. Live callers
    can set use_cache=False to fetch the current archive listing every time.
    """
    cache = os.path.join(CACHE, f"sia2_{collection}_{ra:.4f}_{dec:.4f}_{radius_deg}.xml")
    if use_cache and os.path.exists(cache):
        print(f"  SIA2 {collection} (cached {os.path.basename(cache)})")
        with open(cache, "rb") as f:
            return parse_votable(f.read())

    params = {"POS": f"CIRCLE {ra} {dec} {radius_deg}",
              "COLLECTION": collection}
    print(f"  SIA2 {collection}: POS=CIRCLE {ra:.4f} {dec:.4f} {radius_deg}")
    last_error = None
    for attempt in range(retries):
        try:
            headers = {} if use_cache else {
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
            # (connect, read): a stalled IRSA must fail well inside Cloud
            # Run's 900 s request limit, even after all retries.
            resp = requests.get(IRSA_SIA2, params=params, headers=headers,
                                timeout=SIA2_TIMEOUT)
            resp.raise_for_status()
            rows = parse_votable(resp.content)
            if use_cache:
                with open(cache, "wb") as f:
                    f.write(resp.content)
            print(f"  -> {len(rows)} overlapping Level-2 images.")
            return rows
        except Exception as e:
            last_error = e
            print(f"  SIA2 attempt {attempt + 1} failed: {type(e).__name__}")
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"SIA2 query failed after {retries} attempts: {last_error}")


def target_coordinates(target):
    """Position used for a registered field's IRSA query (proper motion applied).

    Shared by the 12-hour refresh job and the IRSA-fresh views so both ask
    IRSA about exactly the same patch of sky.
    """
    metadata = TARGETS[target]
    if metadata.get("pm_ra") or metadata.get("pm_dec"):
        return target_position(metadata, year_to_mjd(INGEST_EPOCH))
    return float(metadata["ra"]), float(metadata["dec"])


def source_fingerprint(rows, ra, dec):
    """Hash of the SPHEREx images a full refresh would process for a field.

    Two equal fingerprints mean IRSA lists exactly the same products for the
    same exposure selection, so reprocessing would reproduce the published
    result.  The refresh job stores it per field in the snapshot manifest.
    """
    import hashlib
    slots, n_epochs = select_exposures(
        rows, ra, dec, bands=DEFAULT_BANDS, epoch_mode="all")
    selected = []
    for epoch, candidates in slots:
        candidates = sorted(
            candidates,
            key=lambda row: (
                str(row.get("obs_id", "")),
                str(row.get("energy_bandpassname", "")),
                str(row.get("t_min", "")),
            ),
        )
        selected.append({
            "epoch": epoch,
            "products": [{
                key: row.get(key) for key in (
                    "obs_id", "energy_bandpassname", "t_min", "s_ra", "s_dec",
                    "access_url", "s_region")
            } for row in candidates],
        })
    encoded = json.dumps({"n_epochs": n_epochs, "slots": selected},
                         sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def select_exposures(rows, ra, dec, bands=DEFAULT_BANDS,
                     epoch_mode="all", per_slot=2):
    """
    Build one candidate list per (epoch, band) slot.

    epoch_mode="all"       -> every observing epoch present in the archive
    epoch_mode="firstlast" -> only the first and the last epoch (the quick mode)

    Candidates inside a slot are ranked by how deep inside their footprint the
    target lies (from the SIA2 s_region polygon), because IRSA returns a clipped
    strip without the target in it whenever the requested box runs off the edge
    of the detector array.  The caller validates each candidate and moves on to
    the next one if it is unusable.

    Returns (slots, n_epochs) where each slot is (epoch_number, [rows]).
    """
    usable = [r for r in rows
              if r.get("dataproduct_type") == "image"
              and r.get("access_url", "").endswith(".fits")]
    groups = group_epochs(usable)
    if not groups:
        return [], 0

    if epoch_mode == "firstlast" and len(groups) > 1:
        groups = [groups[0], groups[-1]]

    slots = []
    for epoch_index, group in enumerate(groups, start=1):
        for band in bands:
            pool = [r for r in group if r.get("energy_bandpassname") == band]
            deeper = [r for r in pool
                      if (interior_distance_deg(ra, dec,
                                                _parse_s_region(r.get("s_region")))
                          or -1) > 0.05]      # target more than 3' inside
            pool = deeper or pool
            pool = sorted(pool, key=lambda r: -(interior_distance_deg(
                ra, dec, _parse_s_region(r.get("s_region"))) or -1))
            if pool:
                slots.append((epoch_index, pool[:per_slot]))
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


def describe_sources(paths, epochs, rows, collection, size_deg):
    """
    Attach provenance to each downloaded cutout.

    The SIA2 filename encodes the observation id and the detector
    (`level2_<obs_id>D<det>_spx_...`), so every local file can be matched back
    to the catalogue row it came from: that row carries the archive URL, the
    filter, and the start time used for the epoch split.
    """
    by_fragment = {}
    for row in rows:
        obs_id = row.get("obs_id")
        band = row.get("energy_bandpassname", "")
        detector = band.replace("SPHEREx-", "")
        if obs_id and detector:
            by_fragment[f"{obs_id}{detector}"] = row

    sources = []
    for path, epoch in zip(paths, epochs):
        base = os.path.basename(path)
        match = None
        for fragment, row in by_fragment.items():
            if fragment in base:
                match = row
                break
        band = (match or {}).get("energy_bandpassname", "")
        try:
            t_min = float(match["t_min"]) if match and match.get("t_min") else None
        except (TypeError, ValueError):
            t_min = None
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            size_bytes = None
        sources.append({
            "path": path,
            "file": base,
            "epoch": epoch,
            "band": band,
            "detector": band.replace("SPHEREx-", ""),
            "obs_id": (match or {}).get("obs_id"),
            "mjd": t_min,
            "url": (match or {}).get("access_url"),
            "collection": collection,
            "cutout_size_deg": size_deg,
            "bytes": size_bytes,
        })
    return sources


def download_spherex_cutouts(ra, dec, radius_deg=0.1, collection="spherex_dr1",
                             size_deg=0.2, bands=DEFAULT_BANDS,
                             epoch_mode="all", overwrite=False, rows=None):
    """
    Query SIA2 for SPHEREx Level-2 images and download validated cutouts.

    For every (epoch, band) slot the candidate images are tried in order of
    footprint-centre distance until one yields a cutout that contains the target
    and is not mostly flagged.

    Returns (paths, epochs, n_sia2_rows, n_epochs); epochs[i] is the 1-based
    epoch of paths[i], so a ZOGY split stays exact even with partial epochs.
    """
    if rows is None:
        rows = query_sia2(ra, dec, radius_deg=radius_deg, collection=collection)
    slots, n_epochs = select_exposures(rows, ra, dec, bands=bands,
                                       epoch_mode=epoch_mode)
    print(f"  {n_epochs} epoch(s), {len(slots)} (epoch, band) slot(s).")

    paths = []
    epochs = []
    for epoch, candidates in slots:
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
                print(f"    accepted epoch {epoch} {band} "
                      f"({os.path.basename(fname)})")
                paths.append(fname)
                epochs.append(epoch)
                break
            print(f"    rejected epoch {epoch} {band}: {reason}")
        time.sleep(1)
    return paths, epochs, len(rows), n_epochs


# ---------------------------------------------------------------------------
# Download catalogs via VizieR
# ---------------------------------------------------------------------------
def query_irsa_tap(adql, label, retries=3, strict=False):
    """
    Run one synchronous ADQL query on IRSA TAP and return the rows as a list
    of {column: string} dicts (empty strings for nulls). Returns [] on failure.
    """
    resp = None
    for attempt in range(retries):
        try:
            resp = requests.post(IRSA_TAP_SYNC,
                                 data={"REQUEST": "doQuery", "LANG": "ADQL",
                                       "FORMAT": "tsv", "QUERY": adql},
                                 timeout=300)
            resp.raise_for_status()
            break
        except Exception as e:
            print(f"    IRSA TAP {label} attempt {attempt + 1} failed: "
                  f"{type(e).__name__}")
            resp = None
            time.sleep(10)
    if resp is None:
        if strict:
            raise RuntimeError(f"IRSA TAP {label} query failed")
        return []

    text = resp.text.strip()
    if not text or text.startswith("<"):       # VOTable error document
        if strict:
            raise RuntimeError(f"IRSA TAP {label} returned an invalid response")
        return []
    lines = text.splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


def _num(value, default):
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _cell(row, col, default=None, text=False):
    """A VizieR table cell as float/str, or `default` if missing or masked."""
    try:
        value = row[col]
    except (KeyError, IndexError):
        return default
    if value is None or np.ma.is_masked(value):
        return default
    if text:
        value = str(value).strip()
        return value or default
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if np.isfinite(value) else default


def _cone(ra, dec, radius_deg):
    return ("1=CONTAINS(POINT('ICRS',ra,dec), CIRCLE('ICRS',%.6f,%.6f,%.6f))"
            % (ra, dec, radius_deg))


def query_irsa_tap_gaia(ra, dec, radius_deg, limit=GAIA_TAP_LIMIT,
                        strict=False):
    """
    Gaia DR3 cone search through IRSA's TAP service (primary Gaia source).

    IRSA is the same host that serves the SPHEREx images, so it is the most
    reliable path; VizieR I/355/gaiadr3 is the fallback. Sorted brightest
    first so a hit row cap drops the faintest stars, never a random subset.
    """
    adql = ("SELECT TOP %d source_id, ra, dec, pmra, pmdec, parallax, "
            "parallax_error, phot_g_mean_mag, phot_bp_mean_mag, "
            "phot_rp_mean_mag, phot_variable_flag, ruwe "
            "FROM gaia.gaia_dr3_source WHERE %s ORDER BY phot_g_mean_mag ASC"
            % (limit, _cone(ra, dec, radius_deg)))
    rows = []
    for r in query_irsa_tap(adql, "gaia", strict=strict):
        if r.get("ra") in (None, "") or r.get("dec") in (None, ""):
            continue
        rows.append({
            "ra": float(r["ra"]),
            "dec": float(r["dec"]),
            "pm_ra": _num(r.get("pmra"), 0.0),
            "pm_de": _num(r.get("pmdec"), 0.0),
            "gmag": _num(r.get("phot_g_mean_mag"), 99.0),
            # G6 distance, W6 SED colours, G5 variability, astrometric quality
            "source_id": r.get("source_id") or None,
            "parallax": _num(r.get("parallax"), None),
            "parallax_error": _num(r.get("parallax_error"), None),
            "bpmag": _num(r.get("phot_bp_mean_mag"), None),
            "rpmag": _num(r.get("phot_rp_mean_mag"), None),
            "var_flag": r.get("phot_variable_flag") or None,
            "ruwe": _num(r.get("ruwe"), None),
        })
    if len(rows) >= limit:
        print(f"    WARNING: gaia hit the {limit}-row cap; faintest stars dropped")
    return rows


def query_irsa_tap_allwise(ra, dec, radius_deg, limit=WISE_TAP_LIMIT,
                           strict=False):
    """
    AllWISE Source Catalog (allwise_p3as_psd) cone search through IRSA TAP.

    IRSA is the home archive of WISE; the Harvard VizieR mirror does not carry
    II/328/allwise, which is why every cached field had zero AllWISE rows.
    """
    adql = ("SELECT TOP %d designation, ra, dec, w1mpro, w2mpro, w3mpro, "
            "w4mpro, w1sigmpro, w2sigmpro, pmra, pmdec, sigpmra, sigpmdec, "
            "ph_qual, cc_flags, ext_flg FROM allwise_p3as_psd WHERE %s "
            "ORDER BY w1mpro ASC" % (limit, _cone(ra, dec, radius_deg)))
    rows = []
    for r in query_irsa_tap(adql, "allwise", strict=strict):
        if r.get("ra") in (None, "") or r.get("dec") in (None, ""):
            continue
        rows.append({
            "ra": float(r["ra"]),
            "dec": float(r["dec"]),
            "w1": _num(r.get("w1mpro"), 99.0),
            "w2": _num(r.get("w2mpro"), 99.0),
            "w3": _num(r.get("w3mpro"), 99.0),
            "w4": _num(r.get("w4mpro"), 99.0),
            "w1sig": _num(r.get("w1sigmpro"), None),
            "w2sig": _num(r.get("w2sigmpro"), None),
            # W4: AllWISE motion -- only applied when significant (see
            # catalog_tools.source_pm), it finds faint movers Gaia misses
            "pm_ra": _num(r.get("pmra"), None),
            "pm_de": _num(r.get("pmdec"), None),
            "sig_pm_ra": _num(r.get("sigpmra"), None),
            "sig_pm_de": _num(r.get("sigpmdec"), None),
            "ext_flg": r.get("ext_flg") or None,
            # "designation", not "name": app.py merges duplicate rows keeping
            # the first "name", and a WISEA id must not hide a VSX star name.
            "designation": r.get("designation") or None,
            "ph_qual": r.get("ph_qual") or None,
            "cc_flags": r.get("cc_flags") or None,
        })
    if len(rows) >= limit:
        print(f"    WARNING: allwise hit the {limit}-row cap; faintest dropped")
    return rows


VIZIER_QUERIES = {
    "gaia": ("I/355/gaiadr3", ["RA_ICRS", "DE_ICRS", "pmRA", "pmDE",
                               "Gmag", "Plx", "e_Plx", "BPmag", "RPmag"]),
    "wise": ("II/328/allwise", ["RAJ2000", "DEJ2000", "W1mag", "W2mag"]),
    "vsx": ("B/vsx/vsx", ["RAJ2000", "DEJ2000", "Name", "Type", "OID",
                          "max", "n_max", "f_min", "min", "n_min",
                          "Period"]),
}
# The first mirror answers from most networks; the others are reachable only
# from some (never from Google Cloud), so they get a short limit instead of
# holding a request for 30 s each.
VIZIER_TIMEOUT_FIRST = 30
VIZIER_TIMEOUT_FALLBACK = 10


def _vizier_row(key, row):
    if key == "gaia":
        return {
            "ra": float(row["RA_ICRS"]),
            "dec": float(row["DE_ICRS"]),
            "pm_ra": _cell(row, "pmRA", 0.0),
            "pm_de": _cell(row, "pmDE", 0.0),
            "gmag": _cell(row, "Gmag", 99.0),
            "parallax": _cell(row, "Plx"),
            "parallax_error": _cell(row, "e_Plx"),
            "bpmag": _cell(row, "BPmag"),
            "rpmag": _cell(row, "RPmag"),
        }
    if key == "wise":
        return {
            "ra": float(row["RAJ2000"]),
            "dec": float(row["DEJ2000"]),
            "w1": _cell(row, "W1mag", 99.0),
            "w2": _cell(row, "W2mag", 99.0),
        }
    oid = _cell(row, "OID")
    return {
        "ra": float(row["RAJ2000"]),
        "dec": float(row["DEJ2000"]),
        "name": str(row["Name"]).strip(),
        "type": str(row["Type"]).strip(),
        # V3 amplitude/period, V4 AAVSO link
        "oid": int(oid) if oid is not None else None,
        "max": _cell(row, "max"),
        "min": _cell(row, "min"),
        "f_min": _cell(row, "f_min", text=True),
        "n_max": _cell(row, "n_max", text=True),
        "n_min": _cell(row, "n_min", text=True),
        "period_d": _cell(row, "Period"),
    }


def _query_vizier(key, coord, radius_arcmin, strict):
    """
    One catalogue from the VizieR mirrors, in order.

    Returns (rows, answered): ``answered`` is True when a mirror replied with
    a table, "empty" when a mirror replied but had no table (VizieR's reply
    for a cone with no sources -- or a transient glitch, hence the retry),
    and False when no mirror replied at all.
    """
    catalog, cols = VIZIER_QUERIES[key]
    # +Gmag / +W1mag sort brightest-first, like the TAP path, so a capped
    # fallback drops the faintest stars rather than a random slice of the cone.
    sort_cols = {"gaia": ["+Gmag"], "wise": ["+W1mag"]}.get(key, [])
    answered = False
    for index, mirror in enumerate(VIZIER_MIRRORS):
        # VSX exists only on VizieR: give the mirror a second chance.
        attempts = 2 if key == "vsx" else 1
        for attempt in range(attempts):
            try:
                viz = Vizier(
                    row_limit=GAIA_TAP_LIMIT,
                    columns=[c for c in cols if "+" + c not in sort_cols] + sort_cols)
                viz.VIZIER_SERVER = mirror
                viz.TIMEOUT = (VIZIER_TIMEOUT_FIRST if index == 0
                               else VIZIER_TIMEOUT_FALLBACK)
                res = viz.query_region(coord, radius=radius_arcmin * u.arcmin,
                                       catalog=catalog)
                if len(res) == 0:
                    answered = answered or "empty"
                    if attempt + 1 < attempts:
                        print(f"    {key}: no table from {mirror}; retrying")
                        time.sleep(2)
                        continue
                    print(f"    {key}: no table from {mirror}")
                    break
                rows = []
                for row in res[0]:
                    try:
                        rows.append(_vizier_row(key, row))
                    except Exception:
                        continue
                print(f"    {key}: {len(rows)} sources ({mirror})")
                return rows, True
            except Exception as e:
                print(f"    {key}: {mirror} attempt {attempt + 1} "
                      f"failed ({type(e).__name__})")
                if attempt + 1 < attempts:
                    time.sleep(2)
        # A mirror that answered "no sources" twice is believed, except by
        # the strict refresh job, which keeps its previous catalogue instead.
        if answered == "empty" and not strict:
            return [], "empty"
    return [], answered


def download_catalogs_with_status(ra, dec, radius_arcmin=15, strict=False,
                                  progress=None):
    """
    Gaia DR3, AllWISE and VSX for sources within radius, queried in parallel.

    Returns (catalogs, status). ``catalogs`` is {"gaia": [...], "wise": [...],
    "vsx": [...]}; ``status`` maps each to "ok", "none" (the archive answered
    with no sources) or "unavailable" (no archive answered), so an empty list
    is never mistaken for "no catalogue stars here".

    Only the few columns the triage step needs are requested (asking for "**"
    pulls every column of a billion-row catalogue and takes minutes). Gaia and
    AllWISE come from IRSA TAP first -- the host that serves the images -- with
    VizieR as the fallback; VSX lives only on VizieR.

    ``strict`` (the refresh job) raises if any catalogue gets no valid
    response, so the job keeps its previous catalogue rather than publishing
    an empty one.
    """
    print(f"  Querying catalogs within {radius_arcmin}'...")
    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    radius_deg = radius_arcmin / 60.0
    tap = {"gaia": query_irsa_tap_gaia, "wise": query_irsa_tap_allwise}

    def one(key):
        tap_ok = False
        rows = []
        if key in tap:
            try:
                rows = tap[key](ra, dec, radius_deg, strict=strict)
                tap_ok = True
            except Exception as exc:
                print(f"    {key} IRSA TAP failed; trying VizieR: {exc}")
            if rows:
                print(f"    {key}: {len(rows)} sources (IRSA TAP)")
        answered = False
        if not rows:
            rows, answered = _query_vizier(key, coord, radius_arcmin, strict)
        if strict and not rows and not tap_ok and answered is not True:
            raise RuntimeError(f"{key} catalog query failed without a valid response")
        if rows:
            state = "ok"
        elif tap_ok or answered:
            state = "none"
        else:
            state = "unavailable"
        if progress:
            progress(key, len(rows), state)
        return rows, state

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {key: pool.submit(one, key) for key in ("gaia", "wise", "vsx")}
        results = {key: job.result() for key, job in jobs.items()}

    out = {key: rows for key, (rows, _) in results.items()}
    status = {key: state for key, (_, state) in results.items()}
    return out, status


def download_catalogs(ra, dec, radius_arcmin=15, strict=False):
    """Catalogues only; see download_catalogs_with_status."""
    out, _ = download_catalogs_with_status(ra, dec, radius_arcmin, strict)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def ingest_target(name, collection="spherex_dr1", radius_deg=0.1,
                  size_deg=0.2, bands=DEFAULT_BANDS, epoch_mode="all",
                  overwrite=False, skip_catalogs=False, rows=None,
                  catalogs=None, strict_catalogs=False):
    """Ingest one registered target, optionally using an already-fresh SIA2 listing."""
    meta = dict(TARGETS[name])
    if meta.get("pm_ra") or meta.get("pm_dec"):
        c_ra, c_dec = target_position(meta, year_to_mjd(INGEST_EPOCH))
        print(f"  {name}: moved "
              f"{sep_arcsec(meta['ra'], meta['dec'], c_ra, c_dec):.0f}\" "
              f"from its J{meta.get('pos_epoch', 2000.0):g} position")
        meta.update({"ra_ref": meta["ra"], "dec_ref": meta["dec"],
                     "pos_epoch_ref": meta.get("pos_epoch", 2000.0),
                     "ra": round(c_ra, 7), "dec": round(c_dec, 7),
                     "pos_epoch": INGEST_EPOCH})
    print(f"\n=== {name} ({meta['common']}) ===")
    paths, epochs, n_rows, n_epochs = download_spherex_cutouts(
        meta["ra"], meta["dec"],
        radius_deg=radius_deg,
        collection=collection,
        size_deg=size_deg,
        bands=tuple(bands),
        epoch_mode=epoch_mode,
        overwrite=overwrite,
        rows=rows,
    )
    if catalogs is not None:
        cats = catalogs
    elif skip_catalogs:
        cats = {"gaia": [], "wise": [], "vsx": []}
    else:
        cats = download_catalogs(
            meta["ra"], meta["dec"], strict=strict_catalogs)

    if not paths:
        return None

    sia2_rows = rows
    if sia2_rows is None:
        sia2_rows = query_sia2(meta["ra"], meta["dec"], radius_deg=radius_deg,
                               collection=collection)
    sources = describe_sources(paths, epochs, sia2_rows, collection, size_deg)
    record = {
        "target": name,
        "meta": meta,
        "paths": paths,
        "epochs": epochs,
        "sources": sources,
        "catalogs": cats,
        "collection": collection,
        "cutout_size_deg": size_deg,
        "sia2_rows": n_rows,
        "n_epochs": n_epochs,
    }
    with open(os.path.join(CACHE, f"{name}_meta.json"), "w") as f:
        json.dump(record, f, indent=2)

    total_cat = sum(len(v) for v in cats.values())
    print(f"  saved {len(paths)} cutouts and {total_cat} catalog sources.")
    return record


def main():
    parser = argparse.ArgumentParser(description="Ingest real SPHEREx data.")
    parser.add_argument("--collection", default="spherex_dr1",
                        help="SIA2 collection (spherex_dr1 / spherex_qr3 / spherex_dr1_deep)")
    parser.add_argument("--radius", type=float, default=0.1,
                        help="SIA2 cone radius in degrees")
    parser.add_argument("--size", type=float, default=0.2,
                        help="cutout width/height in degrees")
    parser.add_argument("--bands", nargs="*", default=list(DEFAULT_BANDS))
    parser.add_argument("--epochs", choices=["all", "firstlast"],
                        default="all",
                        help="use every epoch, or only first and last")
    parser.add_argument("--targets", nargs="*", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-catalogs", action="store_true")
    args = parser.parse_args()

    names = args.targets or list(TARGETS)
    for name in names:
        record = ingest_target(
            name, collection=args.collection, radius_deg=args.radius,
            size_deg=args.size, bands=tuple(args.bands),
            epoch_mode=args.epochs, overwrite=args.overwrite,
            skip_catalogs=args.skip_catalogs)
        if record is None:
            print("  nothing downloaded -- skipping this target.")
            continue
        time.sleep(1)

    print("\nAll targets processed. Ready for step2.")


if __name__ == "__main__":
    main()
