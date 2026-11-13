"""
step2_coadd.py

Reads the SPHEREx FITS cutouts produced by step1_ingest.py, applies the
FLAGS bitmask to reject cosmic rays and saturated pixels, reprojects each
exposure onto a common tangent plane, and produces an inverse-variance
weighted co-added image for a given detector array.

Tested against SPHEREx QR3 and DR1 Level-2 cutouts.  DR1 adds ZODI and EPSF
layers: ZODI is subtracted before the sky fit, EPSF gives each epoch's PSF.
"""

import os
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from reproject import reproject_interp

import config

# SPHEREx Level-2 FLAGS bits, taken from the MP_* ("mask plane") keywords in
# the FLAGS HDU header of a real cutout.  The blueprint's bit values were
# illustrative; these are the ones the pipeline actually receives.
BIT_TRANSIENT = 1 << 0     # transient detected during SUR
BIT_OVERFLOW = 1 << 1      # overflow detected during SUR
BIT_SUR_ERROR = 1 << 2     # error in onboard processing
BIT_PHANTOM = 1 << 4       # phantom pixel
BIT_REFERENCE = 1 << 5     # reference pixel (detector edge)
BIT_NONFUNC = 1 << 6       # permanently unusable
BIT_DICHROIC = 1 << 7      # low efficiency due to dichroic
BIT_MISSING = 1 << 9       # onboard data lost
BIT_HOT = 1 << 10          # hot pixel
BIT_COLD = 1 << 11         # anomalously low signal
BIT_FULLSAMPLE = 1 << 12   # full sample history available (informational)
BIT_PHANMISS = 1 << 14     # phantom correction was not applied
BIT_NONLINEAR = 1 << 15    # nonlinearity correction unreliable
BIT_PERSIST = 1 << 17      # persistent charge above threshold
BIT_OUTLIER = 1 << 19      # flagged by Detect Outliers
BIT_SOURCE = 1 << 21       # mapped to a known source (signal, not a defect)

# Bits that make a pixel unusable for photometry.  BIT_FULLSAMPLE is
# informational and BIT_SOURCE marks real flux, so neither is masked.
BAD_BITS = (BIT_TRANSIENT | BIT_OVERFLOW | BIT_SUR_ERROR | BIT_PHANTOM |
            BIT_REFERENCE | BIT_NONFUNC | BIT_DICHROIC | BIT_MISSING |
            BIT_HOT | BIT_COLD | BIT_PHANMISS | BIT_NONLINEAR | BIT_PERSIST |
            BIT_OUTLIER)

LARGE_VAR = 1e12


def make_target_wcs(ra_deg, dec_deg, npix=180, pixscale_arcsec=3.0):
    """
    Construct a North-up, East-left TAN WCS centred on (ra_deg, dec_deg)
    with the given pixel scale.
    """
    w = WCS(naxis=2)
    w.wcs.crpix = [npix / 2.0, npix / 2.0]
    w.wcs.cdelt = np.array([-pixscale_arcsec / 3600.0, pixscale_arcsec / 3600.0])
    w.wcs.crval = [ra_deg, dec_deg]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return w


def load_exposure(source):
    """
    Read IMAGE, FLAGS, VARIANCE and WCS from a SPHEREx cutout.

    `source` may be a path on disk *or* raw FITS bytes held in memory, which is
    what live mode uses: the cutout is fetched from IRSA, parsed straight from
    the response and never written to disk.
    """
    import io

    handle = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    with fits.open(handle) as hdul:
        if "IMAGE" in hdul:
            img_hdu = hdul["IMAGE"]
        else:
            img_hdu = hdul[0]

        img = img_hdu.data.astype(float)
        wcs = WCS(img_hdu.header)

        if "FLAGS" in hdul:
            flags = hdul["FLAGS"].data.astype(int)
        else:
            flags = np.zeros_like(img, dtype=int)

        if "VARIANCE" in hdul:
            var = hdul["VARIANCE"].data.astype(float)
        else:
            var = np.ones_like(img)

    return img, flags, var, wcs


def load_exposure_full(source, target_radec=None):
    """
    Everything the v2 pipeline needs from one cutout, as a dict:

        img, flags, var, wcs, header -- as load_exposure (header = IMAGE header)
        zodi       -- ZODI layer (MJy/sr, same grid as IMAGE) or None
        psf        -- normalised, centred, odd-sized PSF on the 3"/px
                      north-up target grid (spherexlens.imaging.psf_from_hdul)
        psf_fwhm_arcsec      -- FWHM of the selected ePSF zone (instrument)
        psf_grid_fwhm_arcsec -- FWHM of the resampled kernel (incl. the
                                bilinear reprojection blur)
        psf_source -- "epsf" or "gaussian_fallback"; psf_zone, psf_note
        axis_angles_deg -- detector x/y axes on the target grid (spike masks)
        header_psf_fwhm_arcsec, mjd, detector, obsid

    target_radec -- (ra, dec): pick the PSF zone at the target, not the cutout
    centre.  `source` is a path or raw FITS bytes, as for load_exposure.
    """
    import io
    from spherexlens.imaging import psf_from_hdul

    handle = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    with fits.open(handle) as hdul:
        img_hdu = hdul["IMAGE"] if "IMAGE" in hdul else hdul[0]
        hdr = img_hdu.header.copy()
        img = img_hdu.data.astype(float)
        wcs = WCS(hdr)
        flags = (hdul["FLAGS"].data.astype(int) if "FLAGS" in hdul
                 else np.zeros_like(img, dtype=int))
        var = (hdul["VARIANCE"].data.astype(float) if "VARIANCE" in hdul
               else np.ones_like(img))
        zodi = None
        if "ZODI" in hdul and hdul["ZODI"].data is not None:
            z = hdul["ZODI"].data.astype(float)
            if z.shape == img.shape and np.isfinite(z).any():
                zodi = z
        xy = None
        if target_radec is not None:
            try:
                pix = wcs.all_world2pix([[target_radec[0], target_radec[1]]], 0,
                                        tolerance=1e-8, maxiter=100, quiet=True)[0]
                if (np.all(np.isfinite(pix)) and 0 <= pix[0] <= img.shape[1] - 1
                        and 0 <= pix[1] <= img.shape[0] - 1):
                    xy = (float(pix[0]), float(pix[1]))
            except Exception:
                xy = None
        psf = psf_from_hdul(hdul, hdr, wcs, xy=xy)

    def _get(key, cast):
        try:
            return cast(hdr[key])
        except (KeyError, TypeError, ValueError):
            return None

    out = {"img": img, "flags": flags, "var": var, "wcs": wcs, "header": hdr,
           "zodi": zodi,
           "header_psf_fwhm_arcsec": _get("PSF_FWHM", float),
           "mjd": _get("MJD-AVG", float) or _get("MJD-OBS", float),
           "detector": _get("DETECTOR", int),
           "obsid": _get("OBSID", str)}
    out.update(psf)
    out.setdefault("axis_angles_deg", None)
    return out


def prepare_exposure(ex, subtract_sky=True, subtract_zodi=None):
    """
    ZODI then sky subtraction for one load_exposure_full() dict.

    The ZODI layer is the Kelsall+1998 model in the IMAGE's own units, so it
    is subtracted pixel by pixel first; the existing constant sky fit then
    takes out what the model leaves.  Missing ZODI = no-op.  Returns
    (img, valid, zodi_applied).
    """
    subtract_zodi = config.SUBTRACT_ZODI if subtract_zodi is None else subtract_zodi
    img, flags, var = ex["img"], ex["flags"], ex["var"]
    valid = ((flags & BAD_BITS) == 0) & np.isfinite(img) & (var > 0)
    applied = False
    if subtract_zodi and ex.get("zodi") is not None:
        z = ex["zodi"]
        img = np.where(np.isfinite(z), img - z, img)
        applied = True
    if subtract_sky:
        img = img - sky_level(img, valid)
    return img, valid, applied


def _bilinear_sample(image, xs, ys):
    """Bilinear resampling of image at fractional pixel coordinates (NaN-safe)."""
    ny, nx = image.shape
    out = np.full(xs.shape, np.nan, dtype=float)
    valid = (np.isfinite(xs) & np.isfinite(ys)
             & (xs >= 0) & (xs <= nx - 1) & (ys >= 0) & (ys <= ny - 1))
    if not valid.any():
        return out

    x = xs[valid]
    y = ys[valid]
    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    x1 = np.minimum(x0 + 1, nx - 1)
    y1 = np.minimum(y0 + 1, ny - 1)
    dx = x - x0
    dy = y - y0

    corners = (image[y0, x0], image[y0, x1], image[y1, x0], image[y1, x1])
    value = ((1 - dx) * (1 - dy) * corners[0] + dx * (1 - dy) * corners[1]
             + (1 - dx) * dy * corners[2] + dx * dy * corners[3])
    value = np.where(np.any([~np.isfinite(c) for c in corners], axis=0),
                     np.nan, value)
    out[valid] = value
    return out


def reproject_to_target(image, wcs_in, wcs_out, npix):
    """
    Resample one exposure onto the common tangent plane.

    reproject_interp is tried first.  IRSA cutouts carry SIP distortion terms
    anchored outside the cropped frame (CRPIX is strongly negative), and
    astropy's default SIP inverter then diverges and returns NaN everywhere, so
    the fallback does the same bilinear resampling with an explicitly
    converged WCS inversion.
    """
    try:
        out, _ = reproject_interp((image, wcs_in), wcs_out,
                                  shape_out=(npix, npix))
        if np.isfinite(out).any():
            return out
    except Exception:
        pass

    yy, xx = np.mgrid[0:npix, 0:npix]
    ra, dec = wcs_out.pixel_to_world_values(xx.ravel(), yy.ravel())
    try:
        pix = wcs_in.all_world2pix(np.column_stack([ra, dec]), 0,
                                   tolerance=1e-8, maxiter=100, quiet=True)
    except Exception:
        return np.full((npix, npix), np.nan)
    xs = np.asarray(pix[:, 0], dtype=float).reshape(npix, npix)
    ys = np.asarray(pix[:, 1], dtype=float).reshape(npix, npix)
    return _bilinear_sample(image, xs, ys)


def sky_level(img, valid, n_iter=3, clip=3.0):
    """
    Sigma-clipped median of an exposure's valid pixels: its sky level.

    A single constant per exposure (not a background mesh), so extended
    targets -- M31, the Orion nebula -- keep their structure.
    """
    vals = img[valid]
    vals = vals[np.isfinite(vals)]
    if vals.size < 50:
        return 0.0
    for _ in range(n_iter):
        med = np.median(vals)
        mad = 1.4826 * np.median(np.abs(vals - med))
        if mad <= 0:
            break
        vals = vals[np.abs(vals - med) < clip * mad]
    return float(np.median(vals))


def coadd_detector(fits_paths, target_wcs, npix=180, subtract_sky=True):
    """
    Inverse-variance co-add of a list of FITS files (one epoch, no mask
    matching -- the dashboard uses coadd_matched for epoch comparisons).

    subtract_sky -- remove each exposure's ZODI model and its own sky level
        first: exposures from different detectors (D1 at ~1 um, D4 at ~3 um)
        sit at very different zodiacal levels.

    Returns (coadded_image, noise_sigma).
    """
    numerator = np.zeros((npix, npix))
    denominator = np.zeros((npix, npix))

    for path in fits_paths:
        try:
            ex = load_exposure_full(path)
        except Exception as e:
            print(f" Skipping {path}: {e}")
            continue

        img, valid, _ = prepare_exposure(ex, subtract_sky=subtract_sky,
                                         subtract_zodi=None if subtract_sky else False)
        img_masked = np.where(valid, img, np.nan)
        var_masked = np.where(valid, ex["var"], LARGE_VAR)

        try:
            img_reproj = reproject_to_target(img_masked, ex["wcs"], target_wcs, npix)
            var_reproj = reproject_to_target(var_masked, ex["wcs"], target_wcs, npix)
        except Exception as e:
            print(f" Reprojection failed for {path}: {e}")
            continue

        weight = 1.0 / np.maximum(var_reproj, 1e-12)
        numerator += np.nan_to_num(img_reproj * weight)
        denominator += np.nan_to_num(weight)

    coadd = numerator / np.maximum(denominator, 1e-12)
    coadd_var = 1.0 / np.maximum(denominator, 1e-12)
    return coadd, np.sqrt(coadd_var)


def _target_radec(target_wcs, npix):
    try:
        ra, dec = target_wcs.pixel_to_world_values(npix / 2.0, npix / 2.0)
        return float(ra), float(dec)
    except Exception:
        return None


def coadd_matched(groups, target_wcs, npix=180, subtract_sky=True,
                  return_psf=False, quality_min=None, subtract_zodi=None):
    """
    Co-add several epochs with *matched* masks.

    groups -- {epoch: [(source, band), ...]}; source is a path or FITS bytes.

    At each pixel a band contributes only if it is valid (inside the footprint
    and not FLAGS-masked) in EVERY epoch. Otherwise a band masked in just one
    epoch drops out of that epoch's average only, and because a star is not
    equally bright in every band, the co-add fakes a change exactly there
    (seen on TRAPPIST-1: two "new sources" on pixels masked in one 2026 frame).

    Each exposure first loses its ZODI model and sky level (prepare_exposure)
    and is scored (spherexlens.imaging.score_exposures); exposures below
    ``quality_min`` (default config.EXPOSURE_QUALITY_MIN; pass 0 to keep all)
    are left out, never emptying an epoch.

    Returns (stacks, noise_maps, excluded, n_bands):
        stacks[e], noise_maps[e] -- co-add and 1-sigma map per epoch
        excluded -- per pixel, how many bands were dropped by the matching
        n_bands  -- number of distinct bands
    With return_psf=True a fifth element, a dict, is appended:
        psfs[e]          -- effective PSF of epoch e: inverse-variance-weighted
                            mean of its exposures' PSFs (3"/px, sum 1)
        psf_info[e]      -- {"source", "fwhm_arcsec", "grid_fwhm_arcsec", ...}
        exposure_quality -- one row per exposure (CONTRACT.md, phase 3.2)
        edge_map         -- per pixel, number of exposure footprints with an
                            edge within config.EDGE_MASK_PX
        axis_angles_deg  -- detector-axis directions on the grid (all exposures)
    """
    from spherexlens.imaging import (combine_psfs, exposure_metrics,
                                     fallback_psf_info, footprint_edge_map,
                                     fwhm_from_array, score_exposures)

    quality_min = config.EXPOSURE_QUALITY_MIN if quality_min is None else quality_min
    radec = _target_radec(target_wcs, npix)

    # ---- pass 1: read and score every exposure (native pixels) -------------
    loaded = []
    for e, items in groups.items():
        for source, band in items:
            try:
                ex = load_exposure_full(source, target_radec=radec)
            except Exception as exc:
                print(f" Skipping {source if isinstance(source, str) else band}: {exc}")
                continue
            img, valid, zodi_applied = prepare_exposure(ex, subtract_sky=subtract_sky,
                                                        subtract_zodi=subtract_zodi)
            # OBSID is shared by the D1..D6 frames of one exposure; the
            # detector suffix matches the IRSA file names (..._0241_1D1_...)
            obs_id = ex.get("obsid") or (os.path.basename(source)
                                         if isinstance(source, str) else str(band))
            if ex.get("obsid") and ex.get("detector") is not None:
                obs_id = f"{obs_id}D{ex['detector']}"
            loaded.append({"epoch": e, "band": band, "ex": ex, "img": img,
                           "valid": valid, "zodi_applied": zodi_applied,
                           "obs_id": obs_id,
                           "psf_fwhm_arcsec": ex.get("psf_fwhm_arcsec"),
                           "metrics": exposure_metrics(ex["img"], ex["flags"], ex["var"],
                                                       ex["header"], BAD_BITS)})
    if loaded:
        score_exposures(loaded, quality_min=quality_min)

    # ---- pass 2: reproject the kept exposures -------------------------------
    reproj = {e: [] for e in groups}
    footprints, angles = [], []
    for rec in loaded:
        if not rec["kept"]:
            continue
        ex, band, e = rec["ex"], rec["band"], rec["epoch"]
        try:
            img_r = reproject_to_target(np.where(rec["valid"], rec["img"], np.nan),
                                        ex["wcs"], target_wcs, npix)
            var_r = reproject_to_target(np.where(rec["valid"], ex["var"], LARGE_VAR),
                                        ex["wcs"], target_wcs, npix)
        except Exception as exc:
            print(f" Reprojection failed for {band}: {exc}")
            rec["kept"] = False
            rec["reasons"].append(f"reprojection failed: {exc}")
            continue
        ok = np.isfinite(img_r) & np.isfinite(var_r) & (var_r < 1e6)
        footprints.append(np.isfinite(var_r))
        angles.extend(ex.get("axis_angles_deg") or [])
        reproj[e].append((band, img_r, var_r, ok, rec))

    bands = sorted({b for items in reproj.values() for b, *_ in items})
    common = {}
    for b in bands:
        m = np.ones((npix, npix), dtype=bool)
        for e in reproj:
            in_e = [ok for bb, _, _, ok, _ in reproj[e] if bb == b]
            m &= np.logical_or.reduce(in_e) if in_e else False
        common[b] = m

    covered = np.zeros((npix, npix), dtype=np.int16)
    for b in bands:
        any_cov = np.zeros((npix, npix), dtype=bool)
        for e in reproj:
            for bb, img_r, var_r, ok, _ in reproj[e]:
                if bb == b:
                    any_cov |= np.isfinite(var_r)
        covered += (any_cov & ~common[b]).astype(np.int16)

    stacks, noise_maps, psfs, psf_info = {}, {}, {}, {}
    for e, items in reproj.items():
        num = np.zeros((npix, npix))
        den = np.zeros((npix, npix))
        p_list, p_w, fw, n_epsf = [], [], [], 0
        for b, img_r, var_r, ok, rec in items:
            use = ok & common[b]
            w = np.where(use, 1.0 / np.maximum(var_r, 1e-12), 0.0)
            num += np.where(use, img_r, 0.0) * w
            den += w
            wsum = float(w.sum())
            p_list.append(rec["ex"]["psf"])
            p_w.append(wsum)
            fw.append((rec["ex"].get("psf_fwhm_arcsec"), wsum))
            n_epsf += rec["ex"].get("psf_source") == "epsf"
        stacks[e] = num / np.maximum(den, 1e-12)
        noise_maps[e] = np.sqrt(1.0 / np.maximum(den, 1e-12))
        if return_psf:
            psf = combine_psfs(p_list, p_w) if p_list else None
            if psf is None:
                psf, n_epsf = fallback_psf_info("no exposure in epoch")["psf"], 0
            good = [(f, w) for f, w in fw if f and w > 0]
            fwhm = (sum(f * w for f, w in good) / sum(w for _, w in good)
                    if good else fwhm_from_array(psf, config.PIXSCALE_ARCSEC))
            src = ("epsf" if items and n_epsf == len(items)
                   else "gaussian_fallback" if n_epsf == 0 else "mixed")
            psfs[e] = psf
            psf_info[e] = {"source": src,
                           "fwhm_arcsec": round(float(fwhm), 2) if fwhm else None,
                           "grid_fwhm_arcsec": round(float(
                               fwhm_from_array(psf, config.PIXSCALE_ARCSEC)), 2),
                           "n_epsf": int(n_epsf), "n_exposures": len(items)}

    if not return_psf:
        return stacks, noise_maps, covered, len(bands)

    quality = []
    for rec in loaded:
        fw = rec["psf_fwhm_arcsec"]
        quality.append({"obs_id": rec["obs_id"], "epoch": rec["epoch"],
                        "band": rec["band"], "score": rec["score"],
                        "kept": rec["kept"], "reasons": rec["reasons"],
                        "components": rec["components"],
                        "zodi_subtracted": rec["zodi_applied"],
                        "psf_source": rec["ex"].get("psf_source"),
                        "psf_fwhm_arcsec": round(fw, 2) if fw else None,
                        "mjd": rec["ex"].get("mjd")})
    extras = {"psfs": psfs, "psf_info": psf_info, "exposure_quality": quality,
              "edge_map": (footprint_edge_map(footprints) if footprints
                           else np.zeros((npix, npix), dtype=np.int16)),
              "axis_angles_deg": sorted({round(a, 1) for a in angles})}
    return stacks, noise_maps, covered, len(bands), extras


if __name__ == "__main__":
    import json
    cache = os.environ.get("SPHEREX_CACHE", "data_cache")
    with open(os.path.join(cache, "WISE_0855_meta.json")) as f:
        meta = json.load(f)
    wcs = make_target_wcs(meta["meta"]["ra"], meta["meta"]["dec"], npix=180)
    files = meta["paths"][:5]
    if files:
        img, noise = coadd_detector(files, wcs)
        print(f"Co-add shape: {img.shape}, median noise: {np.median(noise):.4f}")
    else:
        print("No files cached -- run step1_ingest.py first.")
