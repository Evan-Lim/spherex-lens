"""
spherexlens.ingest.neossat_clean
Clean one raw NEOSSat science frame and check a position in it.

Follows the CSA NEOSSat tutorial (https://github.com/asc-csa/NEOSSAT_Tutorial,
notebook 02 "Pre Processing and Analysis", MIT licence): overscan correction
(per-row median of the overscan strip), optional master-dark subtraction,
then the steps the tutorial points to but leaves to the reader -- hot-pixel
and cosmic-ray masking, and a WCS sanity check.

Raw frame layout (verified on CADC NEOSSAT frames, e.g. NEOS_SCI_2024251021442):

    856 x 512 int16 (BZERO 32768), one primary HDU (+ telemetry tables)
    BIASSEC = [1:64,1:512]     overscan strip (left)
    columns 65-344             unused, all zero
    TRIMSEC = [345:856,1:512]  the 512 x 512 image, ~3.0"/px (CDELT 0.0008325)
    WCS keys refer to the *raw* array; CUNIT is written 'degrees'
    GAIN 1.10 e-/ADU, RDNOISE 8 e-, EXPOSURE (s), JD-OBS / DATE-OBS (UTC)

The tutorial's own code assumes the overscan is the last 50 columns; the
header sections are used when present and that convention is the fallback.
The frame WCS comes from the attitude solution (no plate solve); on the frame
above it put HD 80606 within one pixel of the brightest star.

clean_frame(hdul) -> (image, mask, meta)
    image  float64 ADU, overscan (and dark) subtracted, trimmed
    mask   bool, True = do not use (hot pixel, cosmic ray, saturated, NaN)
    meta   {"wcs": astropy WCS of the trimmed image or None, "wcs_ok", ...}

stamp(image, wcs, ra, dec, size_px) -> {"detected": bool, "snr", "cutout", ...}

Data licence: NEOSSat data are (c) Canadian Space Agency, Open Government
Licence - Canada; see ATTRIBUTION.
"""
import re
import warnings

import numpy as np
from scipy.ndimage import median_filter

import config

ATTRIBUTION = ("Contains NEOSSat data provided by the Canadian Space Agency "
               "under the Open Government Licence - Canada "
               "(https://open.canada.ca/en/open-government-licence-canada; "
               "dataset https://data.asc-csa.gc.ca/dataset/neossat), "
               "retrieved from the Canadian Astronomy Data Centre (CADC).")

TUTORIAL_OVERSCAN_WIDTH = 50       # tutorial fallback: last 50 columns
SATURATION_ADU = 60000.0           # raw 16-bit ADC, kept below the 65535 rail
OUTLIER_SIGMA = 6.0                # pixel above its 3x3 median by this many sigma
SHARPNESS = 0.6                    # ... carrying >60% of its own excess: not a PSF
HOT_DARK_SIGMA = 8.0               # hot pixel in the master dark
WCS_MAX_OFFSET_ARCMIN = 10.0       # image centre vs commanded pointing
PIXSCALE_RANGE = (2.5, 3.5)        # arcsec/px a NEOSSat science frame must have


def _section(value):
    """FITS '[x1:x2,y1:y2]' (1-based, inclusive) -> numpy (rows, cols) slices."""
    m = re.match(r"\[\s*(\d+):(\d+)\s*,\s*(\d+):(\d+)\s*\]", str(value or ""))
    if not m:
        return None
    x1, x2, y1, y2 = (int(v) for v in m.groups())
    return slice(y1 - 1, y2), slice(x1 - 1, x2)


def _sexagesimal(text, hours):
    try:
        parts = [float(p) for p in re.split(r"[:\s]+", str(text).strip()) if p]
    except ValueError:
        return None
    if len(parts) != 3:
        return None
    sign = -1.0 if str(text).strip().startswith("-") else 1.0
    val = abs(parts[0]) + parts[1] / 60.0 + parts[2] / 3600.0
    return sign * val * (15.0 if hours else 1.0)


def _robust_sigma(x):
    x = x[np.isfinite(x)]
    if x.size < 2:
        return 0.0
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def overscan_correct(data, header):
    """Per-row overscan median subtracted, trimmed to the image section."""
    bias = _section(header.get("BIASSEC"))
    trim = _section(header.get("TRIMSEC") or header.get("DATASEC"))
    if bias is None or trim is None:
        nx = data.shape[1]
        bias = (slice(None), slice(nx - TUTORIAL_OVERSCAN_WIDTH, nx))
        trim = (slice(None), slice(0, nx - TUTORIAL_OVERSCAN_WIDTH))
        how = "tutorial convention (last 50 columns)"
    else:
        how = f"BIASSEC {header.get('BIASSEC')}"
    level = np.median(data[bias], axis=1)
    image = data[trim] - level[trim[0], None]
    return image, trim, float(np.median(level)), how


def frame_wcs(header, trim):
    """astropy WCS of the trimmed image (CRPIX shifted, CUNIT fixed)."""
    from astropy.wcs import WCS, FITSFixedWarning
    h = header.copy()
    if "CTYPE1" not in h or "CRVAL1" not in h:
        return None
    for k in ("CUNIT1", "CUNIT2"):
        if str(h.get(k, "")).strip().lower() in ("degrees", "degree"):
            h[k] = "deg"
    h["CRPIX1"] = float(h.get("CRPIX1", 0)) - (trim[1].start or 0)
    h["CRPIX2"] = float(h.get("CRPIX2", 0)) - (trim[0].start or 0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        try:
            w = WCS(h, naxis=2)
        except Exception:
            return None
    return w if w.has_celestial else None


def check_wcs(wcs, header, shape):
    """(ok, details): pixel scale sane, centre near the commanded pointing."""
    if wcs is None:
        return False, {"reason": "no celestial WCS in header"}
    ny, nx = shape
    (ra_c, dec_c), = wcs.all_pix2world([[(nx - 1) / 2.0, (ny - 1) / 2.0]], 0)
    scale = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))) * 3600.0)
    out = {"centre_ra": round(float(ra_c), 6), "centre_dec": round(float(dec_c), 6),
           "pixscale_arcsec": round(scale, 3)}
    ok = PIXSCALE_RANGE[0] <= scale <= PIXSCALE_RANGE[1]
    if not ok:
        out["reason"] = f"pixel scale {scale:.2f}\" outside {PIXSCALE_RANGE}"
    p_ra = _sexagesimal(header.get("OBJCTRA") or header.get("RA"), hours=True)
    p_dec = _sexagesimal(header.get("OBJCTDEC") or header.get("DEC"), hours=False)
    if p_ra is not None and p_dec is not None:
        from catalog_tools import sep_arcsec
        off = sep_arcsec(float(ra_c), float(dec_c), p_ra, p_dec) / 60.0
        out["pointing_offset_arcmin"] = round(off, 2)
        if off > WCS_MAX_OFFSET_ARCMIN:
            ok = False
            out["reason"] = f"WCS centre {off:.1f}' from the commanded pointing"
    return ok, out


def find_outliers(image, sigma=OUTLIER_SIGMA):
    """Single-pixel spikes (hot pixels / cosmic rays): sharper than any PSF."""
    med3 = median_filter(image, size=3, mode="nearest")
    resid = image - med3
    noise = _robust_sigma(resid) or 1.0
    bkg = float(np.nanmedian(image))
    excess = image - bkg
    with np.errstate(invalid="ignore", divide="ignore"):
        sharp = resid > SHARPNESS * np.where(excess > 0, excess, np.inf)
    return (resid > sigma * noise) & sharp


def clean_frame(hdul, master_dark=None, hot_pixels=None):
    """Overscan, dark, hot pixels, cosmic rays, saturation, WCS check.

    master_dark: overscan-corrected, trimmed master dark (same shape and
    exposure as the frame, as the tutorial builds it); hot pixels are taken
    from it when given.  hot_pixels: optional bool mask of known hot pixels.
    """
    hdu = hdul[0]
    header = hdu.header
    raw = np.asarray(hdu.data, dtype=float)
    image, trim, level, how = overscan_correct(raw, header)
    steps = [f"overscan: {how}, median level {level:.1f} ADU"]

    mask = ~np.isfinite(image)
    saturated = raw[trim] >= SATURATION_ADU
    mask |= saturated

    known_hot = np.zeros(image.shape, bool) if hot_pixels is None else \
        np.asarray(hot_pixels, bool)
    if master_dark is not None:
        dark = np.asarray(master_dark, float)
        if dark.shape != image.shape:
            raise ValueError(f"master dark {dark.shape} != image {image.shape}")
        known_hot = known_hot | (dark > np.median(dark)
                                 + HOT_DARK_SIGMA * (_robust_sigma(dark) or 1.0))
        image = image - dark
        steps.append("dark: master dark subtracted")
    else:
        steps.append("dark: none supplied (background left in; stamp() "
                     "subtracts a local annulus)")

    spikes = find_outliers(np.where(mask, np.nanmedian(image), image))
    hot = known_hot
    cosmic = spikes & ~known_hot
    mask |= hot | cosmic
    steps.append(f"hot pixels: {int(hot.sum())} masked; cosmic rays / "
                 f"unexplained spikes: {int(cosmic.sum())} masked")
    if saturated.any():
        steps.append(f"saturated: {int(saturated.sum())} pixels masked")

    wcs = frame_wcs(header, trim)
    wcs_ok, wcs_info = check_wcs(wcs, header, image.shape)
    steps.append("wcs: ok" if wcs_ok else f"wcs: {wcs_info.get('reason')}")

    from spherexlens.ingest.normalize import to_mjd
    mjd = None
    for key, fmt in (("JD-OBS", "jd"), ("DATE-OBS", "iso")):
        if header.get(key) not in (None, ""):
            try:
                mjd = to_mjd(header[key], fmt)
                break
            except (ValueError, TypeError):
                continue
    meta = {"wcs": wcs, "wcs_ok": wcs_ok, **wcs_info,
            "mjd": mjd, "exposure_s": header.get("EXPOSURE"),
            "gain_e_per_adu": _float(header.get("GAIN")),
            "object": header.get("OBJECT"), "mode": header.get("MODE"),
            "overscan_level_adu": round(level, 2),
            "n_hot": int(hot.sum()), "n_cosmic": int(cosmic.sum()),
            "n_saturated": int(saturated.sum()), "n_masked": int(mask.sum()),
            "steps": steps, "attribution": ATTRIBUTION}
    return image, mask, meta


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def stamp(image, wcs, ra, dec, size_px=15, mask=None, aperture_px=3.0,
          search_px=2, snr_min=None):
    """Cutout around (ra, dec) and whether a source is seen there.

    Aperture photometry at the brightest pixel within ``search_px`` of the
    predicted position (the attitude WCS is good to ~1 px), local background
    and noise from an annulus at 2-3 aperture radii.  detected = SNR >=
    snr_min (config.NEOSSAT_STAMP_SNR).
    """
    snr_min = config.NEOSSAT_STAMP_SNR if snr_min is None else snr_min
    out = {"detected": False, "inside": False, "snr": None, "x": None, "y": None,
           "size_px": int(size_px), "snr_min": snr_min}
    if wcs is None:
        out["reason"] = "no WCS"
        return out
    (x, y), = wcs.all_world2pix([[ra, dec]], 0)
    ny, nx = image.shape
    out["x"], out["y"] = round(float(x), 2), round(float(y), 2)
    r_out = 3.0 * aperture_px
    if not (r_out <= x <= nx - 1 - r_out and r_out <= y <= ny - 1 - r_out):
        out["reason"] = "position outside the frame (or too close to its edge)"
        return out
    out["inside"] = True
    good = np.ones(image.shape, bool) if mask is None else ~np.asarray(mask, bool)
    good &= np.isfinite(image)

    xi, yi = int(round(x)), int(round(y))
    half = int(size_px) // 2
    y0, y1 = max(0, yi - half), min(ny, yi + half + 1)
    x0, x1 = max(0, xi - half), min(nx, xi + half + 1)
    cut = np.where(good[y0:y1, x0:x1], image[y0:y1, x0:x1], np.nan)
    out["cutout"] = np.round(cut, 1).tolist()

    yy, xx = np.mgrid[0:ny, 0:nx]
    s0, s1 = max(0, yi - search_px), min(ny, yi + search_px + 1)
    t0, t1 = max(0, xi - search_px), min(nx, xi + search_px + 1)
    win = np.where(good[s0:s1, t0:t1], image[s0:s1, t0:t1], -np.inf)
    py, px = np.unravel_index(int(np.argmax(win)), win.shape)
    cy, cx = s0 + py, t0 + px
    r = np.hypot(xx - cx, yy - cy)
    ap = (r <= aperture_px) & good
    ann = (r >= 2 * aperture_px) & (r <= r_out) & good
    if ap.sum() < 3 or ann.sum() < 10:
        out["reason"] = "too few unmasked pixels"
        return out
    bkg = float(np.median(image[ann]))
    noise = _robust_sigma(image[ann]) or 1e-9
    n_ap = int(ap.sum())
    flux = float(np.sum(image[ap] - bkg))
    err = noise * np.sqrt(n_ap * (1.0 + n_ap / ann.sum()))
    snr = flux / err
    out.update({"snr": round(float(snr), 2), "flux_adu": round(flux, 1),
                "background_adu": round(bkg, 2), "noise_adu": round(noise, 2),
                "peak_x": int(cx), "peak_y": int(cy),
                "detected": bool(snr >= snr_min)})
    return out
