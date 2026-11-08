"""
step5_spectrum.py

Given a FITS cutout and a sky position, extract the flux at that position
across all 102 SPHEREx bands and compute three diagnostic spectral
indices: methane absorption, water absorption, and the near-infrared
continuum slope.
"""

import io

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


def _wavelength_from_wave_tab(hdul, x_cut, y_cut):
    """
    Central wavelength (um) at a cutout pixel from the real WCS-WAVE table.

    SPHEREx Level-2 files do not store CWAVE/CBAND images: the wavelength
    calibration is a FITS "WAVE-TAB" lookup (Paper 3, section 6.1) on a coarse
    9x9 grid of the *parent* 2040x2040 image, stored in a BINTABLE with X, Y
    key columns and VALUES(x, y, 2) = (wavelength, bandwidth).  The image
    header carries CRPIX1W/CRPIX2W so the cutout pixel can be mapped back to
    parent coordinates before the bilinear interpolation.
    """
    if "WCS-WAVE" not in hdul:
        return None, None
    header = hdul["IMAGE"].header
    x_parent = x_cut - float(header.get("CRPIX1W", 0.0))
    y_parent = y_cut - float(header.get("CRPIX2W", 0.0))

    table = hdul["WCS-WAVE"].data
    keys_x = np.asarray(table["X"][0], dtype=float)
    keys_y = np.asarray(table["Y"][0], dtype=float)
    values = np.asarray(table["VALUES"][0], dtype=float)   # (ny, nx, 2)

    if not (keys_x[0] <= x_parent <= keys_x[-1]):
        return None, None
    if not (keys_y[0] <= y_parent <= keys_y[-1]):
        return None, None

    ix = int(np.clip(np.searchsorted(keys_x, x_parent) - 1, 0, len(keys_x) - 2))
    iy = int(np.clip(np.searchsorted(keys_y, y_parent) - 1, 0, len(keys_y) - 2))
    fx = (x_parent - keys_x[ix]) / (keys_x[ix + 1] - keys_x[ix])
    fy = (y_parent - keys_y[iy]) / (keys_y[iy + 1] - keys_y[iy])

    lam = ((1 - fx) * (1 - fy) * values[iy, ix, 0]
           + fx * (1 - fy) * values[iy, ix + 1, 0]
           + (1 - fx) * fy * values[iy + 1, ix, 0]
           + fx * fy * values[iy + 1, ix + 1, 0])
    band = ((1 - fx) * (1 - fy) * values[iy, ix, 1]
            + fx * (1 - fy) * values[iy, ix + 1, 1]
            + (1 - fx) * fy * values[iy + 1, ix, 1]
            + fx * fy * values[iy + 1, ix + 1, 1])
    return float(lam), float(band)


def wavelength_at(cutout_path, ra, dec):
    """
    Central wavelength and bandwidth (um) at a sky position in one cutout.

    Thin wrapper around the CWAVE/WCS-WAVE lookups so callers that only need
    the spectral calibration (the provenance table, the metadata endpoint) do
    not have to run aperture photometry.
    """
    handle = (io.BytesIO(cutout_path)
              if isinstance(cutout_path, (bytes, bytearray)) else cutout_path)
    with fits.open(handle) as hdul:
        hdu = hdul["IMAGE"] if "IMAGE" in hdul else hdul[0]
        wcs = WCS(hdu.header)
        try:
            x, y = wcs.world_to_pixel_values(ra, dec)
        except Exception:
            return None, None
        if "CWAVE" in hdul:
            cwave = hdul["CWAVE"].data
            xi, yi = int(x), int(y)
            if 0 <= yi < cwave.shape[0] and 0 <= xi < cwave.shape[1]:
                return float(cwave[yi, xi]), None
            return None, None
        return _wavelength_from_wave_tab(hdul, x, y)


def extract_spectrum_from_cutout(cutout_path, ra, dec, aperture_radius_pix=3):
    """
    Extract the flux at (ra, dec) from a single SPHEREx cutout.

    Returns the background-subtracted aperture flux, the central wavelength of
    the aperture (from CWAVE when present, else from the real WCS-WAVE lookup
    table) and the pixel coordinates of the aperture centre.
    """
    handle = (io.BytesIO(cutout_path)
              if isinstance(cutout_path, (bytes, bytearray)) else cutout_path)
    with fits.open(handle) as hdul:
        if "IMAGE" in hdul:
            img = hdul["IMAGE"].data.astype(float)
            wcs = WCS(hdul["IMAGE"].header)
        else:
            img = hdul[0].data.astype(float)
            wcs = WCS(hdul[0].header)

        cwave = hdul["CWAVE"].data.astype(float) if "CWAVE" in hdul else None

        # Pixel solid angle, so a sum of MJy/sr pixels can become mJy (SED).
        try:
            from astropy.wcs.utils import proj_plane_pixel_area
            pix_sr = float(proj_plane_pixel_area(wcs.celestial)) * (np.pi / 180.0) ** 2
        except Exception:
            pix_sr = None

        try:
            cx, cy = wcs.world_to_pixel_values(ra, dec)
        except Exception:
            return {"flux": 0.0, "wavelength_um": None, "bandwidth_um": None,
                    "center_pix": (0, 0)}

        cy_i, cx_i = int(cy), int(cx)

        if cwave is not None and 0 <= cy_i < cwave.shape[0] and 0 <= cx_i < cwave.shape[1]:
            lam, band = float(cwave[cy_i, cx_i]), None
        else:
            lam, band = _wavelength_from_wave_tab(hdul, cx, cy)

    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    dist = np.hypot(xx - cx, yy - cy)

    aperture = dist < aperture_radius_pix
    annulus = (dist > aperture_radius_pix + 2) & (dist < aperture_radius_pix + 6)

    if aperture.sum() == 0:
        return {"flux": 0.0, "wavelength_um": lam, "bandwidth_um": band,
                "center_pix": (cy_i, cx_i)}

    bg = np.nanmean(img[annulus]) if annulus.sum() > 0 else 0.0
    if not np.isfinite(bg):
        bg = 0.0
    flux = np.nansum(img[aperture]) - bg * aperture.sum()

    return {
        "flux": float(flux),
        "wavelength_um": lam,
        "bandwidth_um": band,
        "center_pix": (cy_i, cx_i),
        "pix_sr": pix_sr,
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
