"""
step5_spectrum.py

Given a FITS cutout and a sky position, extract the flux at that position
across all 102 SPHEREx bands and compute three diagnostic spectral
indices: methane absorption, water absorption, and the near-infrared
continuum slope.
"""

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


def extract_spectrum_from_cutout(cutout_path, ra, dec, aperture_radius_pix=3):
    """
    Extract the flux at (ra, dec) from a single SPHEREx cutout.
    """
    with fits.open(cutout_path) as hdul:
        if "IMAGE" in hdul:
            img = hdul["IMAGE"].data.astype(float)
            wcs = WCS(hdul["IMAGE"].header)
        else:
            img = hdul[0].data.astype(float)
            wcs = WCS(hdul[0].header)

        if "CWAVE" in hdul:
            cwave = hdul["CWAVE"].data.astype(float)
        else:
            cwave = None

    try:
        cx, cy = wcs.world_to_pixel_values(ra, dec)
    except Exception:
        return {"flux": 0.0, "wavelength_um": None, "center_pix": (0, 0)}

    cy_i, cx_i = int(cy), int(cx)

    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    dist = np.hypot(xx - cx, yy - cy)

    aperture = dist < aperture_radius_pix
    annulus = (dist > aperture_radius_pix + 2) & (dist < aperture_radius_pix + 6)

    if aperture.sum() == 0:
        return {"flux": 0.0, "wavelength_um": None, "center_pix": (cy_i, cx_i)}

    bg = np.nanmean(img[annulus]) if annulus.sum() > 0 else 0.0
    flux = np.nansum(img[aperture]) - bg * aperture.sum()

    if cwave is not None and 0 <= cy_i < cwave.shape[0] and 0 <= cx_i < cwave.shape[1]:
        lam = float(cwave[cy_i, cx_i])
    else:
        lam = None

    return {
        "flux": float(flux),
        "wavelength_um": lam,
        "center_pix": (cy_i, cx_i),
    }


def three_index_classifier(wavelengths, flux):
    """
    Compute the three diagnostic spectral indices and classify the object.
    """
    wavelengths = np.asarray(wavelengths, dtype=float)
    flux = np.asarray(flux, dtype=float)

    def band_mean(l1, l2):
        mask = (wavelengths >= l1) & (wavelengths <= l2)
        if not mask.any():
            return 1e-6
        return float(np.nanmean(flux[mask]))

    ch4_1 = band_mean(1.56, 1.64) / max(band_mean(1.70, 1.78), 1e-6)
    ch4_2 = band_mean(2.05, 2.15) / max(band_mean(2.28, 2.38), 1e-6)
    ch4 = 0.5 * (ch4_1 + ch4_2)

    continuum = band_mean(1.24, 1.34) + band_mean(2.12, 2.22)
    trough = (band_mean(1.46, 1.54) + band_mean(1.96, 2.04) +
              band_mean(2.95, 3.15))
    h2o = continuum / max(trough, 1e-6)

    nir_slope = np.log10(max(band_mean(4.40, 4.80), 1e-6) /
                         max(band_mean(1.00, 1.20), 1e-6))

    if ch4 > 2.0 and nir_slope > 0.5:
        label = "Ultracool Y-dwarf"
    elif ch4 > 1.8:
        label = "T-type brown dwarf"
    elif h2o > 0.95 and nir_slope < -0.2:
        label = "Icy KBO / Planet X candidate"
    elif nir_slope > 0.25:
        label = "Dusty T Tauri / variable star"
    else:
        label = "M-dwarf / Mira red giant"

    return {
        "ch4": float(round(ch4, 2)),
        "h2o": float(round(h2o, 2)),
        "nir_slope": float(round(nir_slope, 2)),
        "label": label,
    }


if __name__ == "__main__":
    wl = np.linspace(0.75, 5.0, 102)
    flux = 1.0 + 0.5 * np.sin(wl * 2.0) + wl ** 1.5
    result = three_index_classifier(wl, flux)
    print(f"Classification: {result['label']}")
