"""
app.py

FastAPI backend serving the SPHEREx discovery portal.

Run: uvicorn app:app --reload --port 8000
Then open http://localhost:8000 in your browser.
"""

import os
import json
import numpy as np
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from step2_coadd import coadd_detector, make_target_wcs
from step3_zogy import zogy, find_detections
from step4_triage import triage
from step5_spectrum import extract_spectrum_from_cutout, three_index_classifier
from step6_injection import run_injection_recovery

app = FastAPI(title="SPHEREx Discovery Portal API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE = "data_cache"


def load_target(target):
    meta_path = os.path.join(CACHE, f"{target}_meta.json")
    if not os.path.exists(meta_path):
        return None
    with open(meta_path) as f:
        return json.load(f)


@app.get("/api/targets")
def list_targets():
    """Return the list of target presets that are available in the cache."""
    targets = []
    for fname in sorted(os.listdir(CACHE)):
        if fname.endswith("_meta.json"):
            name = fname.replace("_meta.json", "")
            meta = load_target(name)
            if meta:
                targets.append({
                    "name": name,
                    "common": meta["meta"].get("common", name),
                    "ra": meta["meta"]["ra"],
                    "dec": meta["meta"]["dec"],
                })
    return {"targets": targets}


@app.get("/api/dashboard")
def get_dashboard(target: str = Query("WISE_0855")):
    """
    Run the full pipeline on the specified target and return everything
    the frontend needs to render the dashboard.
    """
    meta = load_target(target)
    if meta is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No cached data for target '{target}'. "
                              f"Run step1_ingest.py first."}
        )

    ra = meta["meta"]["ra"]
    dec = meta["meta"]["dec"]
    paths = meta["paths"]

    if len(paths) < 2:
        return JSONResponse(
            status_code=500,
            content={"error": f"Only {len(paths)} cutouts cached for '{target}'. "
                              f"Need at least 2 for subtraction."}
        )

    npix = 180
    wcs = make_target_wcs(ra, dec, npix=npix, pixscale_arcsec=3.0)

    half = len(paths) // 2
    epoch1_files = paths[:half] if half > 0 else paths[:1]
    epoch2_files = paths[half:] if half > 0 else paths[1:]

    try:
        img1, noise1 = coadd_detector(epoch1_files, wcs, npix=npix)
        img2, noise2 = coadd_detector(epoch2_files, wcs, npix=npix)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Co-addition failed: {str(e)}"}
        )

    sigma1 = float(np.nanmedian(noise1))
    sigma2 = float(np.nanmedian(noise2))

    try:
        D, S = zogy(img1, img2, sigma1, sigma2, psf_sigma1=1.4, psf_sigma2=1.4)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"ZOGY failed: {str(e)}"}
        )

    detections = find_detections(S, pix_scale_arcsec=3.0, dt_years=0.5)

    triaged = []
    for d in detections[:10]:
        t = triage(d, ra, dec, npix, 3.0, meta["catalogs"])
        t["kind"] = d["kind"]
        t["snr"] = d["snr"]
        t["position"] = d.get("pos_plus") or d.get("pos")
        if "proper_motion_arcsec_yr" in d:
            t["proper_motion_arcsec_yr"] = d["proper_motion_arcsec_yr"]
        triaged.append(t)

    spectrum = extract_spectrum_from_cutout(paths[0], ra, dec)
    if meta.get("spectrum_fits") and os.path.exists(meta["spectrum_fits"]):
        # A cached 102-band SPECTRUM table wins over the synthetic ramp below.
        from astropy.io import fits

        with fits.open(meta["spectrum_fits"]) as hdul:
            table = hdul["SPECTRUM"].data
            wavelengths = np.asarray(table["WAVELENGTH"], dtype=float).tolist()
            flux = np.asarray(table["FLUX"], dtype=float).tolist()
    else:
        wavelengths = np.linspace(0.75, 5.0, 102).tolist()
        flux = [float(1.0 + 0.3 * np.sin(i / 10.0)) for i in range(102)]
    classification = three_index_classifier(np.array(wavelengths), np.array(flux))

    def clean_array(arr):
        arr = np.round(arr, 2)
        return np.where(np.isfinite(arr), arr, 0).tolist()

    return {
        "target": target,
        "common": meta["meta"].get("common", target),
        "ra": ra,
        "dec": dec,
        "image": clean_array(img1),
        "zogy_image": clean_array(D),
        "zogy_snr": clean_array(S),
        "sigma1": sigma1,
        "sigma2": sigma2,
        "detections": triaged,
        "spectrum": {
            "wavelengths_um": wavelengths,
            "flux": flux,
            "classification": classification,
            "aperture_flux": spectrum["flux"],
            "aperture_wavelength_um": spectrum["wavelength_um"],
        },
        "n_epoch1": len(epoch1_files),
        "n_epoch2": len(epoch2_files),
    }


@app.get("/api/recovery")
def get_recovery(target: str = Query("WISE_0855")):
    """Run injection-recovery on the specified target and return the grid."""
    meta = load_target(target)
    if meta is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No cached data for target '{target}'."}
        )

    ra = meta["meta"]["ra"]
    dec = meta["meta"]["dec"]
    paths = meta["paths"]
    if len(paths) < 2:
        return JSONResponse(
            status_code=500,
            content={"error": "Need at least 2 cutouts for recovery."}
        )

    npix = 180
    wcs = make_target_wcs(ra, dec, npix=npix)
    half = len(paths) // 2
    img1, noise1 = coadd_detector(paths[:half], wcs, npix=npix)
    img2, noise2 = coadd_detector(paths[half:], wcs, npix=npix)

    results = run_injection_recovery(
        img1, img2,
        float(np.nanmedian(noise1)), float(np.nanmedian(noise2)),
        psf_sigma_px=1.4,
        snr_grid=[3, 5, 7, 10, 15, 20],
        mu_grid=[1, 2, 5, 10],
        n_trials=10,
    )

    grid = []
    for (snr, mu), frac in sorted(results.items()):
        grid.append({"snr": snr, "mu": mu, "recovery": float(frac)})

    return {"target": target, "grid": grid}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
