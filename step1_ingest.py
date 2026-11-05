"""
step1_ingest.py

Queries the IRSA SIA2 service for SPHEREx Level-2 cutouts of the five
mandatory test targets, downloads the FITS files, and queries four
catalogs (Gaia DR3, AllWISE, AAVSO VSX, JPL SBDB) for known sources
within 15 arcminutes of each target.

Output: data_cache/<target>_meta.json and data_cache/*.fits

Run: python step1_ingest.py
"""

import os
import json
import time
import requests
import numpy as np
import astropy.units as u
from astropy.coordinates import SkyCoord
from astroquery.vizier import Vizier

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

CACHE = "data_cache"
os.makedirs(CACHE, exist_ok=True)

# IRSA SIA2 endpoint
IRSA_SIA2 = "https://irsa.ipac.caltech.edu/SIA"

# ---------------------------------------------------------------------------
# Download SPHEREx cutouts
# ---------------------------------------------------------------------------
def download_spherex_cutouts(ra, dec, radius_deg=0.1, collection="spherex_qr3"):
    """
    Query IRSA SIA2 for SPHEREx QR3 cutouts and download each FITS file.

    Returns a list of local paths to downloaded FITS files.
    """
    print(f" Querying IRSA SIA2 for {collection} near ({ra:.4f}, {dec:.4f})...")

    params = {
        "POS": f"{ra},{dec}",
        "SIZE": str(radius_deg),
        "COLLECTION": collection,
        "RESPONSEFORMAT": "votable",
    }

    try:
        r = requests.get(IRSA_SIA2, params=params, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f" SIA2 query failed: {e}")
        return []

    import re
    text = r.text
    urls = re.findall(r'<uri[^>]*>([^<]+\.fits[^<]*)</uri>', text)
    urls += re.findall(r'<access_url[^>]*>([^<]+)</access_url>', text)
    seen = set()
    urls = [u for u in urls if not (u in seen or seen.add(u))]

    print(f" Found {len(urls)} cutout URLs.")

    paths = []
    for i, url in enumerate(urls[:20]):
        fname = os.path.join(CACHE, f"{collection}_{i:03d}.fits")
        if os.path.exists(fname):
            paths.append(fname)
            continue
        try:
            rr = requests.get(url, timeout=120, stream=True)
            rr.raise_for_status()
            with open(fname, "wb") as f:
                for chunk in rr.iter_content(chunk_size=8192):
                    f.write(chunk)
            paths.append(fname)
            print(f" Downloaded {fname} ({os.path.getsize(fname)//1024} KB)")
        except Exception as e:
            print(f" Failed to download {url}: {e}")
            continue

    return paths

# ---------------------------------------------------------------------------
# Download catalogs via VizieR
# ---------------------------------------------------------------------------
def download_catalogs(ra, dec, radius_arcmin=15):
    """
    Query Gaia DR3, AllWISE, and VSX for sources within radius.

    Returns a dict: {"gaia": [...], "wise": [...], "vsx": [...]}
    """
    print(f" Querying catalogs within {radius_arcmin}'...")
    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    out = {"gaia": [], "wise": [], "vsx": []}

    viz = Vizier(row_limit=1000, columns=["**"])

    # Gaia DR3
    try:
        res = viz.query_region(coord, radius=radius_arcmin * u.arcmin,
                               catalog="I/355/gaiadr3")
        if len(res) > 0:
            tab = res[0]
            for row in tab:
                try:
                    out["gaia"].append({
                        "ra": float(row["RA_ICRS"]),
                        "dec": float(row["DE_ICRS"]),
                        "pm_ra": float(row["pmRA"]) if row["pmRA"] is not None else 0.0,
                        "pm_de": float(row["pmDE"]) if row["pmDE"] is not None else 0.0,
                        "gmag": float(row["Gmag"]) if "Gmag" in tab.colnames and row["Gmag"] is not None else 99.0,
                    })
                except Exception:
                    continue
        print(f" Gaia: {len(out['gaia'])} sources.")
    except Exception as e:
        print(f" Gaia query failed: {e}")

    # AllWISE
    try:
        res = viz.query_region(coord, radius=radius_arcmin * u.arcmin,
                               catalog="II/328/allwise")
        if len(res) > 0:
            tab = res[0]
            for row in tab:
                try:
                    out["wise"].append({
                        "ra": float(row["RAJ2000"]),
                        "dec": float(row["DEJ2000"]),
                        "w1": float(row["W1mag"]) if row["W1mag"] is not None else 99.0,
                        "w2": float(row["W2mag"]) if row["W2mag"] is not None else 99.0,
                    })
                except Exception:
                    continue
        print(f" AllWISE: {len(out['wise'])} sources.")
    except Exception as e:
        print(f" AllWISE query failed: {e}")

    # AAVSO VSX
    try:
        res = viz.query_region(coord, radius=radius_arcmin * u.arcmin,
                               catalog="B/vsx/vsx")
        if len(res) > 0:
            tab = res[0]
            for row in tab:
                try:
                    out["vsx"].append({
                        "ra": float(row["RAJ2000"]),
                        "dec": float(row["DEJ2000"]),
                        "name": str(row["Name"]),
                        "type": str(row["Type"]),
                    })
                except Exception:
                    continue
        print(f" VSX: {len(out['vsx'])} sources.")
    except Exception as e:
        print(f" VSX query failed: {e}")

    return out

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    for name, meta in TARGETS.items():
        print(f"\n=== {name} ({meta['common']}) ===")
        paths = download_spherex_cutouts(meta["ra"], meta["dec"])
        cats = download_catalogs(meta["ra"], meta["dec"])

        record = {
            "target": name,
            "meta": meta,
            "paths": paths,
            "catalogs": cats,
        }
        with open(os.path.join(CACHE, f"{name}_meta.json"), "w") as f:
            json.dump(record, f, indent=2)

        total_cat = sum(len(v) for v in cats.values())
        print(f" Saved {len(paths)} FITS files and {total_cat} catalog sources.")
        time.sleep(1)

    print("\nAll targets processed. Ready for step2.")
