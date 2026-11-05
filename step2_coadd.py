"""
step2_coadd.py

Reads the SPHEREx FITS cutouts produced by step1_ingest.py, applies the
FLAGS bitmask to reject cosmic rays and saturated pixels, reprojects each
exposure onto a common tangent plane, and produces an inverse-variance
weighted co-added image for a given detector array.

Tested against SPHEREx QR3 Level-2 cutouts.
"""

import os
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from reproject import reproject_interp

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


def load_exposure(path):
    """
    Read IMAGE, FLAGS, VARIANCE and WCS from a SPHEREx cutout.
    """
    with fits.open(path) as hdul:
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


def coadd_detector(fits_paths, target_wcs, npix=180):
    """
    Inverse-variance co-add of a list of FITS files.

    Returns (coadded_image, noise_sigma).
    """
    numerator = np.zeros((npix, npix))
    denominator = np.zeros((npix, npix))

    for path in fits_paths:
        try:
            img, flags, var, wcs = load_exposure(path)
        except Exception as e:
            print(f" Skipping {path}: {e}")
            continue

        valid = ((flags & BAD_BITS) == 0) & np.isfinite(img) & (var > 0)
        img_masked = np.where(valid, img, np.nan)
        var_masked = np.where(valid, var, LARGE_VAR)

        try:
            img_reproj = reproject_to_target(img_masked, wcs, target_wcs, npix)
            var_reproj = reproject_to_target(var_masked, wcs, target_wcs, npix)
        except Exception as e:
            print(f" Reprojection failed for {path}: {e}")
            continue

        weight = 1.0 / np.maximum(var_reproj, 1e-12)
        numerator += np.nan_to_num(img_reproj * weight)
        denominator += np.nan_to_num(weight)

    coadd = numerator / np.maximum(denominator, 1e-12)
    coadd_var = 1.0 / np.maximum(denominator, 1e-12)
    return coadd, np.sqrt(coadd_var)


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
