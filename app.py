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
from live_ingest import live_cutouts, describe_live_cutouts

app = FastAPI(title="SPHEREx Discovery Portal API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE = os.environ.get("SPHEREX_CACHE", "data_cache")

# Live mode keeps fetched cutouts in RAM only (no disk), with a small TTL cache
# so a second request for the same target is instant.
LIVE_TTL_SECONDS = int(os.environ.get("SPHEREX_LIVE_TTL", "1800"))
LIVE_BANDS = tuple(os.environ.get(
    "SPHEREX_LIVE_BANDS", "SPHEREx-D1,SPHEREx-D4").split(","))
LIVE_CACHE = {}          # key -> (timestamp, cutouts, provenance)
FIELD_REGISTRY = {}      # name -> meta dict for fields added at runtime
FIELD_DASH_CACHE = {}    # name -> dashboard payload for those fields


def load_target(target):
    if target in FIELD_REGISTRY:
        return FIELD_REGISTRY[target]
    meta_path = os.path.join(CACHE, f"{target}_meta.json")
    if not os.path.exists(meta_path):
        return None
    with open(meta_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Provenance, statistics and map overlays
# ---------------------------------------------------------------------------
def significance_histogram(S, bins=32):
    """
    Histogram of the significance map, in sigma.

    A healthy difference image is a narrow noise peak around zero plus a long
    tail of real sources; the shape is the quickest way to see whether the
    subtraction worked.
    """
    values = np.asarray(S, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"bins": [], "counts": [], "edges": []}
    lo, hi = -6.0, max(6.0, float(np.percentile(values, 99.9)))
    counts, edges = np.histogram(np.clip(values, lo, hi), bins=bins,
                                 range=(lo, hi))
    return {
        "edges": [round(float(e), 2) for e in edges],
        "counts": [int(c) for c in counts],
        "n_pixels": int(values.size),
    }


def live_exposures(target, meta, size_deg=0.2, epoch_mode="firstlast",
                  max_slots=0, bands=LIVE_BANDS):
    """
    Fetch a target's cutouts from IRSA into memory (TTL-cached, no disk).

    Returns (cutouts, provenance).  Raises RuntimeError if fewer than two
    exposures come back, because ZOGY needs a pair.
    """
    import time as _time

    key = (target, size_deg, epoch_mode, tuple(bands), max_slots)
    hit = LIVE_CACHE.get(key)
    if hit and (_time.time() - hit[0]) < LIVE_TTL_SECONDS:
        return hit[1], hit[2], True

    cutouts = live_cutouts(meta["meta"]["ra"], meta["meta"]["dec"],
                           size_deg=size_deg, bands=bands,
                           epoch_mode=epoch_mode,
                           max_slots=max_slots or None,
                           log=lambda msg: print(msg, flush=True))
    if len(cutouts) < 2:
        raise RuntimeError(
            f"live mode fetched only {len(cutouts)} usable cutout(s); "
            "IRSA may be throttling -- try again, or use source=cache")
    provenance = describe_live_cutouts(cutouts)
    LIVE_CACHE[key] = (_time.time(), cutouts, provenance)
    return cutouts, provenance, False


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


def build_field_objects(meta, detections, target_wcs, npix=180,
                        match_arcsec=30.0, dedup_arcsec=2.0):
    """
    Merge the whole catalogue and our detections into one object list.

    Each row is one physical object in the 9' field and carries a verdict:

    * ``KNOWN``     -- the object is in at least one catalogue (Gaia DR3, VSX,
                       AllWISE).  ``detected`` tells you whether SPHEREx also
                       saw it change; ``snr`` is that significance.
    * ``CANDIDATE`` -- the object is *not* in any catalogue but SPHEREx did
                       detect a change there: the follow-up list.

    Catalogue entries appearing in several catalogues (a Gaia star that is also
    a VSX variable) are merged into one row whose ``catalogs`` list names all of
    them.
    """
    ra0, dec0 = meta["meta"]["ra"], meta["meta"]["dec"]
    objects = []

    def to_pixels(ra, dec, require_inside=False):
        try:
            x, y = target_wcs.world_to_pixel_values(ra, dec)
        except Exception:
            return None
        x, y = float(x), float(y)
        if require_inside and not (0 <= x < npix and 0 <= y < npix):
            return None
        return x, y

    # --- catalogue entries ------------------------------------------------
    for cat_name, sources in (meta.get("catalogs") or {}).items():
        for src in sources or []:
            s_ra, s_dec = src.get("ra"), src.get("dec")
            if s_ra is None or s_dec is None:
                continue
            pix = to_pixels(s_ra, s_dec)
            if pix is None:
                continue
            x, y = pix
            in_field = (0 <= x < npix and 0 <= y < npix)

            # merge with an existing row for the same star
            twin = None
            for obj in objects:
                if (abs(obj["ra"] - s_ra) < dedup_arcsec / 3600.0
                        and abs(obj["dec"] - s_dec) < dedup_arcsec / 3600.0):
                    twin = obj
                    break
            if twin is not None:
                if cat_name not in twin["catalogs"]:
                    twin["catalogs"].append(cat_name)
                if twin.get("mag") is None:
                    twin["mag"] = src.get("gmag", src.get("w1", src.get("mag")))
                if src.get("name"):
                    twin["name"] = twin.get("name") or src.get("name")
                continue

            objects.append({
                "ra": s_ra,
                "dec": s_dec,
                "x": round(x, 1),
                "y": round(y, 1),
                "mag": src.get("gmag", src.get("w1", src.get("mag"))),
                "in_field": in_field,
                "catalogs": [cat_name],
                "name": src.get("name"),
                "type": src.get("type"),
                "verdict": "KNOWN",
                "detected": False,
                "snr": None,
                "sep_arcsec": None,
                "kind": None,
                "pair": None,
            })

    # --- detections: attach to a catalogue row, or add as CANDIDATE -------
    def sep_arcsec(a_ra, a_dec, b_ra, b_dec):
        cosd = np.cos(np.radians(a_dec))
        return float(np.hypot((a_ra - b_ra) * cosd, a_dec - b_dec) * 3600.0)

    for det in detections:
        pos = det.get("position") or [0, 0]
        d_ra, d_dec = det.get("ra"), det.get("dec")
        if d_ra is None or d_dec is None:
            continue
        best = None
        best_sep = match_arcsec
        for obj in objects:
            s = sep_arcsec(d_ra, d_dec, obj["ra"], obj["dec"])
            if s < best_sep:
                best, best_sep = obj, s

        if best is not None:
            if (best.get("snr") or 0) < det["snr"]:
                best["snr"] = round(float(det["snr"]), 2)
                best["sep_arcsec"] = round(best_sep, 2)
                best["kind"] = det.get("kind")
                best["pair"] = det.get("pair")
            best["detected"] = True
            continue

        pix = to_pixels(d_ra, d_dec, require_inside=True)
        if pix is None:
            continue
        objects.append({
            "ra": d_ra,
            "dec": d_dec,
            "x": round(pix[0], 1),
            "y": round(pix[1], 1),
            "mag": None,
            "in_field": True,
            "catalogs": [],
            "name": None,
            "type": None,
            "verdict": "CANDIDATE",
            "detected": True,
            "snr": round(float(det["snr"]), 2),
            "sep_arcsec": 0.0,
            "kind": det.get("kind"),
            "pair": det.get("pair"),
        })

    objects.sort(key=lambda o: (not o["detected"],
                                -(o.get("snr") or 0),
                                o.get("mag") if o.get("mag") is not None else 99))
    return objects


def target_statistics(meta, detections, sigma1, sigma2, npix=180,
                      pixscale_arcsec=3.0, dt_years=0.5, n_exposures=None):
    """The numbers shown in the dashboard's stat cards."""
    depths = [s.get("mjd") for s in meta.get("sources", []) if s.get("mjd")]
    baseline_days = round(max(depths) - min(depths), 1) if len(depths) > 1 else None
    snrs = [d["snr"] for d in detections] or [0.0]
    n_known = sum(1 for d in detections if d.get("verdict") == "KNOWN")
    fov_armin = npix * pixscale_arcsec / 60.0

    # A frame that is almost entirely flagged (a saturated naked-eye star, for
    # instance) leaves no valid pixels to weight, so the inverse-variance noise
    # collapses to a meaningless number. Report that honestly as "no estimate"
    # instead of printing 1e+06 as if it were a measurement.
    def usable(sig):
        try:
            sig = float(sig)
        except (TypeError, ValueError):
            return None
        return sig if np.isfinite(sig) and 1e-4 < sig < 1e3 else None

    s1, s2 = usable(sigma1), usable(sigma2)
    usable_noise = [x for x in (s1, s2) if x is not None]
    depth = round(5.0 * float(np.mean(usable_noise)), 4) if usable_noise else None

    return {
        "n_exposures": n_exposures if n_exposures is not None else len(meta["paths"]),
        "n_epochs": len(set(meta.get("epochs", []))) or 2,
        "baseline_days": baseline_days,
        "baseline_months": round(baseline_days / 30.44, 1) if baseline_days else None,
        "dt_years": dt_years,
        "pixscale_arcsec": pixscale_arcsec,
        "npix": npix,
        "fov_arcmin": round(fov_armin, 2),
        "sky_area_deg2": round((fov_armin / 60.0) ** 2, 5),
        "sigma1": s1,
        "sigma2": s2,
        "noise_note": (None if usable_noise else
                       "noise estimate degenerate: the frame is almost entirely "
                       "flagged (saturated star), so no valid pixels are left to weight"),
        "depth_5sigma": depth,
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
    # fields opened at runtime (no files) are addressable too
    for name, meta in FIELD_REGISTRY.items():
        if any(t["name"] == name for t in targets):
            continue
        targets.append({
            "name": name,
            "common": meta["meta"].get("common", name),
            "ra": meta["meta"]["ra"],
            "dec": meta["meta"]["dec"],
            "custom": True,
        })

    return {"targets": targets}


@app.get("/api/dashboard")
def get_dashboard(target: str = Query("WISE_0855"),
                  source: str = Query("cache"),
                  live_slots: int = Query(0),
                  live_size: float = Query(0.2),
                  live_epochs: str = Query("firstlast")):
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

    custom = bool(meta.get("custom"))
    live_mode = (str(source).lower() == "live") or custom
    if custom and target in FIELD_DASH_CACHE and str(source).lower() != "refresh":
        return FIELD_DASH_CACHE[target]
    live_provenance = None
    live_cached = False
    if live_mode:
        try:
            cutouts, live_provenance, live_cached = live_exposures(
                target, meta, size_deg=live_size,
                epoch_mode=live_epochs, max_slots=live_slots)
        except Exception as e:
            return JSONResponse(
                status_code=502,
                content={"error": f"Live fetch from IRSA failed: {e}",
                         "hint": "retry, or call without ?source=live to use data_cache/"}
            )
        paths = []
        sources = [c["data"] for c in cutouts]
        epochs = [c["epoch"] for c in cutouts]
    else:
        paths = meta["paths"]
        sources = list(paths)
        epochs = meta.get("epochs")

    if len(sources) < 2:
        return JSONResponse(
            status_code=500,
            content={"error": f"Only {len(sources)} usable cutout(s) for '{target}'. "
                              f"Need at least 2 for subtraction."}
        )

    npix = 180
    wcs = make_target_wcs(ra, dec, npix=npix, pixscale_arcsec=3.0)

    # -- epochs -----------------------------------------------------------
    # Every epoch in the cache is co-added separately, and ZOGY is run on each
    # consecutive pair.  The deepest baseline (first vs last) becomes the map
    # shown by default; the extra pairs contribute detections only.
    if not (epochs and len(epochs) == len(sources) and len(set(epochs)) > 1):
        half = len(sources) // 2
        epochs = [1] * max(1, half) + [2] * max(1, len(sources) - half)
        epochs = epochs[:len(sources)]

    order = sorted(set(epochs))
    files_by_epoch = {e: [s for s, pe in zip(sources, epochs) if pe == e]
                      for e in order}

    try:
        stacks, noises = {}, {}
        for e in order:
            stack, noise = coadd_detector(files_by_epoch[e], wcs, npix=npix)
            stacks[e] = stack
            noises[e] = float(np.nanmedian(noise))
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Co-addition failed: {str(e)}"}
        )

    epoch1_files = files_by_epoch[order[0]]
    epoch2_files = files_by_epoch[order[-1]]
    img1, img2 = stacks[order[0]], stacks[order[-1]]
    sigma1, sigma2 = noises[order[0]], noises[order[-1]]

    try:
        D, S = zogy(img1, img2, sigma1, sigma2, psf_sigma1=1.4, psf_sigma2=1.4)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"ZOGY failed: {str(e)}"}
        )

    # consecutive pairs (E1->E2, E2->E3, ...) plus the deep pair E1->En
    pair_list = [(a, b) for a, b in zip(order, order[1:])]
    if len(order) > 2 and (order[0], order[-1]) not in pair_list:
        pair_list.append((order[0], order[-1]))
    if not pair_list:
        pair_list = [(order[0], order[0])]

    raw_detections = []
    pair_summary = []
    for ea, eb in pair_list:
        if ea == eb:
            continue
        try:
            if (ea, eb) == (order[0], order[-1]):
                d_img, s_img = D, S
            else:
                d_img, s_img = zogy(stacks[ea], stacks[eb], noises[ea],
                                    noises[eb], 1.4, 1.4)
            found = find_detections(s_img, pix_scale_arcsec=3.0, dt_years=0.5)
        except Exception:
            found = []
        pair_summary.append({
            "pair": f"E{ea}\u2192E{eb}",
            "n_exposures_a": len(files_by_epoch[ea]),
            "n_exposures_b": len(files_by_epoch[eb]),
            "n_detections": len(found),
            "sigma_a": noises[ea],
            "sigma_b": noises[eb],
        })
        for d in found:
            d["pair"] = f"E{ea}\u2192E{eb}"
            raw_detections.append(d)

    # merge the same object found in several pairs, keeping the strongest
    detections = []
    for d in sorted(raw_detections, key=lambda d: -d["snr"]):
        pos = d.get("pos_plus") or d.get("pos") or (0, 0)
        if any(abs(pos[0] - (m.get("pos_plus") or m.get("pos") or (0, 0))[0]) <= 2
               and abs(pos[1] - (m.get("pos_plus") or m.get("pos") or (0, 0))[1]) <= 2
               for m in detections):
            continue
        detections.append(d)

    triaged = []
    for d in detections[:10]:
        t = triage(d, ra, dec, npix, 3.0, meta["catalogs"])
        t["kind"] = d["kind"]
        t["snr"] = d["snr"]
        t["pair"] = d.get("pair")
        t["position"] = d.get("pos_plus") or d.get("pos")
        if "proper_motion_arcsec_yr" in d:
            t["proper_motion_arcsec_yr"] = d["proper_motion_arcsec_yr"]
        triaged.append(t)

    spectrum = extract_spectrum_from_cutout(sources[0], ra, dec)
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
        for path in sources:
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
        "provenance": (live_provenance if live_mode
                       else exposure_provenance(meta, wcs, npix=npix)),
        "source": "live" if live_mode else "cache",
        "live_cached": live_cached,
        "stats": target_statistics(meta, triaged, sigma1, sigma2, npix=npix,
                                   n_exposures=len(sources)),
        "catalog_overlay": catalog_overlay(meta, wcs, npix=npix),
        "field_objects": build_field_objects(meta, triaged, wcs, npix=npix),
        "histogram": significance_histogram(S),
        "epoch_pairs": pair_summary,
        "catalog_counts": {k: len(v or []) for k, v in
                           (meta.get("catalogs") or {}).items()},
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


MOSAIC_CACHE = {"t": 0.0, "payload": None}
MOSAIC_TTL = int(os.environ.get("SPHEREX_MOSAIC_TTL", "900"))


def _thumbnail(arr, size=48):
    """Block-average a 180x180 map down to a small square, rounded to 2 dp."""
    a = np.asarray(arr, dtype=float)
    a = np.where(np.isfinite(a), a, 0.0)
    ny, nx = a.shape
    fy, fx = ny // size, nx // size
    if fy < 1 or fx < 1:
        return a[:size, :size].round(2).tolist()
    trimmed = a[:fy * size, :fx * size]
    blocks = trimmed.reshape(size, fy, size, fx).mean(axis=(1, 3))
    return np.round(blocks, 2).tolist()


@app.get("/api/field")
def add_field(ra: float = Query(...), dec: float = Query(...),
              name: str = Query(""), size: float = Query(0.2),
              slots: int = Query(0), epochs: str = Query("firstlast")):
    """
    Load a field that is not in data_cache/ -- any position on the sky.

    The cutouts are fetched from IRSA into RAM (nothing is written to disk),
    the full pipeline runs, and the field is registered for this process so the
    dashboard, the mosaic and the sky view can address it by name.
    """
    import re as _re

    safe = _re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or (
        f"Field_{ra:.2f}_{dec:.2f}".replace("-", "m").replace(".", "p"))
    meta = {
        "target": safe,
        "meta": {"ra": float(ra), "dec": float(dec), "common": name or safe,
                 "type": "Custom field"},
        "paths": [], "epochs": [], "catalogs": {},
        "collection": "spherex_qr2", "cutout_size_deg": size,
        "custom": True,
    }
    FIELD_REGISTRY[safe] = meta
    FIELD_DASH_CACHE.pop(safe, None)

    payload = get_dashboard(target=safe, source="live", live_slots=slots,
                            live_size=size, live_epochs=epochs)
    if not isinstance(payload, dict):
        return payload

    FIELD_DASH_CACHE[safe] = payload
    MOSAIC_CACHE["payload"] = None          # force the all-sky tiles to refresh
    return {
        "target": safe,
        "common": payload.get("common"),
        "ra": payload.get("ra"),
        "dec": payload.get("dec"),
        "n_detections": payload.get("stats", {}).get("n_detections"),
        "max_snr": payload.get("stats", {}).get("max_snr"),
        "n_exposures": payload.get("stats", {}).get("n_exposures"),
        "n_epochs": payload.get("stats", {}).get("n_epochs"),
        "note": "registered for this process; nothing written to disk",
    }


@app.get("/api/mosaic")
def get_mosaic(thumb: int = Query(48), refresh: int = Query(0)):
    """
    One small tile per cached target, for the all-sky view.

    Each tile carries the field centre, size, a down-sampled significance map,
    the detection split and the headline statistics -- enough to draw an
    all-sky chart and to decide which field is worth opening.
    """
    import time as _time

    if (not refresh and MOSAIC_CACHE["payload"]
            and (_time.time() - MOSAIC_CACHE["t"]) < MOSAIC_TTL):
        return MOSAIC_CACHE["payload"]

    tiles = []
    for entry in list_targets()["targets"]:
        if entry["name"] in FIELD_REGISTRY:
            continue
        name = entry["name"]
        dashboard = get_dashboard(target=name)
        if not isinstance(dashboard, dict):
            continue
        stats = dashboard.get("stats", {})
        tiles.append({
            "target": name,
            "common": dashboard.get("common", name),
            "ra": dashboard["ra"],
            "dec": dashboard["dec"],
            "fov_arcmin": dashboard["map"]["fov_arcmin"],
            "thumb": _thumbnail(dashboard["zogy_snr"], size=thumb),
            "thumb_npix": thumb,
            "n_detections": stats.get("n_detections", 0),
            "n_known": stats.get("n_known", 0),
            "n_candidate": stats.get("n_candidate", 0),
            "max_snr": stats.get("max_snr"),
            "baseline_days": stats.get("baseline_days"),
            "sigma1": stats.get("sigma1"),
            "sigma2": stats.get("sigma2"),
            "n_exposures": stats.get("n_exposures"),
            "n_epochs": stats.get("n_epochs"),
            "n_gaia": stats.get("n_gaia"),
            "n_vsx": stats.get("n_vsx"),
            "source": dashboard.get("source", "cache"),
        })

    for name, payload_dash in FIELD_DASH_CACHE.items():
        stats = payload_dash.get("stats", {})
        tiles.append({
            "target": name,
            "common": payload_dash.get("common", name),
            "ra": payload_dash["ra"], "dec": payload_dash["dec"],
            "fov_arcmin": payload_dash["map"]["fov_arcmin"],
            "thumb": _thumbnail(payload_dash["zogy_snr"], size=thumb),
            "thumb_npix": thumb,
            "n_detections": stats.get("n_detections", 0),
            "n_known": stats.get("n_known", 0),
            "n_candidate": stats.get("n_candidate", 0),
            "max_snr": stats.get("max_snr"),
            "baseline_days": stats.get("baseline_days"),
            "sigma1": stats.get("sigma1"), "sigma2": stats.get("sigma2"),
            "n_exposures": stats.get("n_exposures"),
            "n_epochs": stats.get("n_epochs"),
            "n_gaia": 0, "n_vsx": 0,
            "source": "live", "custom": True,
        })

    payload = {"tiles": tiles, "n_fields": len(tiles),
               "sky_area_deg2": round(sum(
                   (t["fov_arcmin"] / 60.0) ** 2 for t in tiles), 6)}
    MOSAIC_CACHE["t"] = _time.time()
    MOSAIC_CACHE["payload"] = payload
    return payload


app.mount("/", StaticFiles(directory="static", html=True), name="static")
