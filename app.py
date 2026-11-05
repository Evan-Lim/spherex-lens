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

CACHE = os.environ.get("SPHEREX_CACHE", "data_cache")


def load_target(target):
    meta_path = os.path.join(CACHE, f"{target}_meta.json")
    if not os.path.exists(meta_path):
        return None
    with open(meta_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Provenance, statistics and map overlays
# ---------------------------------------------------------------------------
def exposure_provenance(meta, target_wcs, npix=180):
    """
    One row per downloaded exposure: where the file came from, which detector
    and epoch it belongs to, and the wavelength at the target position.

    Uses the `sources` block written by step1_ingest.py (archive URL, obs_id,
    start time) and falls back to the FITS headers for older caches.
    """
    from astropy.io import fits
    from astropy.time import Time
    from step5_spectrum import wavelength_at

    ra, dec = meta["meta"]["ra"], meta["meta"]["dec"]
    listed = {s.get("path"): s for s in meta.get("sources", []) if s}
    rows = []

    for path, epoch in zip(meta["paths"], meta.get("epochs", [None] * len(meta["paths"]))):
        info = dict(listed.get(path, {}))
        info.setdefault("path", path)
        info.setdefault("file", os.path.basename(path))
        info.setdefault("epoch", epoch)
        info.setdefault("collection", meta.get("collection", "spherex_qr2"))

        try:
            with fits.open(path) as hdul:
                hdu = hdul["IMAGE"] if "IMAGE" in hdul else hdul[0]
                header = hdu.header
                info.setdefault("obs_id", header.get("OBSID"))
                detector = header.get("DETECTOR")
                if detector is not None and not info.get("detector"):
                    info["detector"] = f"D{detector}"
                info.setdefault("exptime_s", header.get("EXPTIME") or header.get("TSAMP"))
                info.setdefault("bunit", header.get("BUNIT", "MJy/sr").replace(" ", ""))
                shape = hdu.data.shape
                info["shape"] = list(shape)
                if not info.get("bytes"):
                    info["bytes"] = os.path.getsize(path)
            lam, band = wavelength_at(path, ra, dec)
            info["wavelength_um"] = lam
            info["bandwidth_um"] = band
        except Exception as exc:                      # keep the row, note the problem
            info["error"] = f"{type(exc).__name__}: {exc}"

        if info.get("mjd") and not info.get("date"):
            try:
                info["date"] = Time(float(info["mjd"]), format="mjd").iso[:10]
            except Exception:
                pass
        rows.append(info)

    return rows


def catalog_overlay(meta, target_wcs, npix=180):
    """
    Catalog positions inside the field, converted to target-plane pixels so the
    frontend can draw them on top of the map.
    """
    ra, dec = meta["meta"]["ra"], meta["meta"]["dec"]
    out = []
    cats = meta.get("catalogs", {})
    for cat_name, sources in cats.items():
        for src in sources or []:
            s_ra, s_dec = src.get("ra"), src.get("dec")
            if s_ra is None or s_dec is None:
                continue
            try:
                x, y = target_wcs.world_to_pixel_values(s_ra, s_dec)
            except Exception:
                continue
            x, y = float(x), float(y)
            if not (0 <= x < npix and 0 <= y < npix):
                continue
            out.append({
                "cat": cat_name,
                "x": round(x, 1),
                "y": round(y, 1),
                "ra": s_ra,
                "dec": s_dec,
                "mag": src.get("gmag", src.get("w1", src.get("mag"))),
                "label": src.get("name") or src.get("type"),
            })
    out.sort(key=lambda r: (r["cat"], r["mag"] if r["mag"] is not None else 99))
    return out[:600]


def target_statistics(meta, detections, sigma1, sigma2, npix=180,
                      pixscale_arcsec=3.0, dt_years=0.5):
    """The numbers shown in the dashboard's stat cards."""
    depths = [s.get("mjd") for s in meta.get("sources", []) if s.get("mjd")]
    baseline_days = round(max(depths) - min(depths), 1) if len(depths) > 1 else None
    snrs = [d["snr"] for d in detections] or [0.0]
    n_known = sum(1 for d in detections if d.get("verdict") == "KNOWN")
    fov_armin = npix * pixscale_arcsec / 60.0
    return {
        "n_exposures": len(meta["paths"]),
        "n_epochs": len(set(meta.get("epochs", []))) or 2,
        "baseline_days": baseline_days,
        "baseline_months": round(baseline_days / 30.44, 1) if baseline_days else None,
        "dt_years": dt_years,
        "pixscale_arcsec": pixscale_arcsec,
        "npix": npix,
        "fov_arcmin": round(fov_armin, 2),
        "sky_area_deg2": round((fov_armin / 60.0) ** 2, 5),
        "sigma1": sigma1,
        "sigma2": sigma2,
        "depth_5sigma": round(5.0 * float(np.nanmean([sigma1, sigma2])), 4),
        "n_detections": len(detections),
        "n_known": n_known,
        "n_candidate": len(detections) - n_known,
        "n_dipoles": sum(1 for d in detections if d.get("kind") == "dipole"),
        "n_monopoles": sum(1 for d in detections if d.get("kind") == "monopole"),
        "max_snr": round(float(max(snrs)), 2),
        "mean_snr": round(float(np.mean(snrs)), 2) if detections else 0.0,
        "n_gaia": len(meta.get("catalogs", {}).get("gaia", []) or []),
        "n_wise": len(meta.get("catalogs", {}).get("wise", []) or []),
        "n_vsx": len(meta.get("catalogs", {}).get("vsx", []) or []),
    }


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

    epochs = meta.get("epochs")
    if epochs and len(epochs) == len(paths) and len(set(epochs)) > 1:
        first = min(epochs)
        epoch1_files = [p for p, e in zip(paths, epochs) if e == first]
        epoch2_files = [p for p, e in zip(paths, epochs) if e != first]
    else:
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
        classification = three_index_classifier(np.array(wavelengths), np.array(flux))
    else:
        # Real SPHEREx cutouts: build the observed spectrum from the aperture
        # photometry of every cached exposure, using each file's own WCS-WAVE
        # wavelength calibration.
        points = []
        for path in paths:
            spec = extract_spectrum_from_cutout(path, ra, dec)
            if spec.get("wavelength_um") is not None:
                points.append((spec["wavelength_um"], spec["flux"]))
        points.sort()
        wavelengths = [float(p[0]) for p in points]
        flux = [float(p[1]) for p in points]
        # The three-index classifier needs a sampled spectrum, not a handful
        # of exposures, so it is only reported when enough channels exist.
        classification = (three_index_classifier(np.array(wavelengths),
                                                 np.array(flux))
                          if len(points) >= 30 else None)

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
        "map": {
            "npix": npix,
            "pixscale_arcsec": 3.0,
            "ra": ra,
            "dec": dec,
            "fov_arcmin": round(npix * 3.0 / 60.0, 2),
        },
        "provenance": exposure_provenance(meta, wcs, npix=npix),
        "stats": target_statistics(meta, triaged, sigma1, sigma2, npix=npix),
        "catalog_overlay": catalog_overlay(meta, wcs, npix=npix),
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
