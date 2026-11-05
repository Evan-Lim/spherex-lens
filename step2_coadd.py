"""
step2_coadd.py

Reads the SPHEREx FITS cutouts produced by step1_ingest.py, applies the
FLAGS bitmask to reject cosmic rays and saturated pixels, reprojects each
exposure onto a common tangent plane, and produces an inverse-variance
weighted co-added image for a given detector array.

Tested against SPHEREx QR3 Level-2 cutouts.
"""

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from reproject import reproject_interp

# Bits that flag a pixel as unusable.
BIT_DEAD = 1 << 0
BIT_SATURATED = 1 << 1
BIT_COSMIC_RAY = 1 << 2
BIT_HOT_PIXEL = 1 << 5
BIT_STRAY_LIGHT = 1 << 8

BAD_BITS = BIT_DEAD | BIT_SATURATED | BIT_COSMIC_RAY | BIT_HOT_PIXEL | BIT_STRAY_LIGHT

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
            img_reproj, _ = reproject_interp((img_masked, wcs), target_wcs,
                                              shape_out=(npix, npix))
            var_reproj, _ = reproject_interp((var_masked, wcs), target_wcs,
                                              shape_out=(npix, npix))
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
    with open("data_cache/WISE_0855_meta.json") as f:
        meta = json.load(f)
    wcs = make_target_wcs(meta["meta"]["ra"], meta["meta"]["dec"], npix=180)
    files = meta["paths"][:5]
    if files:
        img, noise = coadd_detector(files, wcs)
        print(f"Co-add shape: {img.shape}, median noise: {np.median(noise):.4f}")
    else:
        print("No files cached -- run step1_ingest.py first.")
