"""
Forced aperture photometry of many sources on one SPHEREx cutout.

Each cutout is opened once; for every source we measure the background-
subtracted aperture flux (mJy), its error from the VARIANCE layer, the
wavelength the linear variable filter delivers at that pixel (WCS-WAVE), a
flux-weighted centroid (for motion) and whether any flagged pixel touches the
aperture.
"""
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from step5_spectrum import _wavelength_from_wave_tab

APERTURE_PX = 2.0          # native 6.2" pixels; PSF FWHM ~7.5" = 1.2 px
ANNULUS_PX = (4.0, 7.0)
# FLAGS bits that make a measurement unusable: the same set the co-add masks
# (step2_coadd.BAD_BITS).  SOURCE (bit 21) and FULLSAMPLE are informational --
# SOURCE marks pixels *on a real star*, so treating every bit as bad flagged
# nearly every aperture and the first NEP run analysed 0 stars.
from step2_coadd import BAD_BITS as BAD_FLAG_MASK  # noqa: E402


def _mjd(header):
    for key in ("MJD-AVG", "MJD-OBS", "MJD-BEG"):
        if key in header:
            try:
                return float(header[key])
            except (TypeError, ValueError):
                pass
    return None


def measure(path, sources):
    """sources: [{"id", "ra", "dec"}]; returns list of measurement dicts."""
    out = []
    with fits.open(path) as hdul:
        hdu = hdul["IMAGE"] if "IMAGE" in hdul else hdul[0]
        img = hdu.data.astype(float)
        hdr = hdu.header
        wcs = WCS(hdr)
        var = hdul["VARIANCE"].data.astype(float) if "VARIANCE" in hdul else None
        flags = hdul["FLAGS"].data.astype(np.int64) if "FLAGS" in hdul else None
        zodi = hdul["ZODI"].data.astype(float) if "ZODI" in hdul else None
        if zodi is not None and zodi.shape == img.shape:
            img = img - zodi
        try:
            from astropy.wcs.utils import proj_plane_pixel_area
            pix_sr = float(proj_plane_pixel_area(wcs.celestial)) * (np.pi / 180) ** 2
        except Exception:
            pix_sr = None
        mjd = _mjd(hdr)
        detector = hdr.get("DETECTOR")
        obsid = hdr.get("OBSID")
        ny, nx = img.shape
        yy, xx = np.mgrid[0:ny, 0:nx]
        ras = np.array([s["ra"] for s in sources], dtype=float)
        decs = np.array([s["dec"] for s in sources], dtype=float)
        try:
            px, py = wcs.world_to_pixel_values(ras, decs)
        except Exception:
            return out
        for s, x, y in zip(sources, np.atleast_1d(px), np.atleast_1d(py)):
            if not (ANNULUS_PX[1] <= x < nx - ANNULUS_PX[1]
                    and ANNULUS_PX[1] <= y < ny - ANNULUS_PX[1]):
                continue
            x0, x1 = int(x - ANNULUS_PX[1] - 1), int(x + ANNULUS_PX[1] + 2)
            y0, y1 = int(y - ANNULUS_PX[1] - 1), int(y + ANNULUS_PX[1] + 2)
            sub = img[y0:y1, x0:x1]
            d = np.hypot(xx[y0:y1, x0:x1] - x, yy[y0:y1, x0:x1] - y)
            ap = d < APERTURE_PX
            an = (d > ANNULUS_PX[0]) & (d < ANNULUS_PX[1]) & np.isfinite(sub)
            if ap.sum() == 0 or an.sum() < 8:
                continue
            bg = float(np.nanmedian(sub[an]))
            vals = sub[ap] - bg
            flux_sr = float(np.nansum(vals))                 # MJy/sr summed
            err_sr = (float(np.sqrt(np.nansum(var[y0:y1, x0:x1][ap])))
                      if var is not None else float(np.nanstd(sub[an]) * np.sqrt(ap.sum())))
            flagged = bool(flags is not None
                           and np.any(flags[y0:y1, x0:x1][ap] & BAD_FLAG_MASK))
            w = np.clip(vals, 0, None)
            if w.sum() > 0:
                cx = float((w * xx[y0:y1, x0:x1][ap]).sum() / w.sum())
                cy = float((w * yy[y0:y1, x0:x1][ap]).sum() / w.sum())
                c_ra, c_dec = (float(v) for v in wcs.pixel_to_world_values(cx, cy))
            else:
                c_ra = c_dec = None
            lam, band = _wavelength_from_wave_tab(hdul, x, y)
            scale = pix_sr * 1e9 if pix_sr else 1.0          # MJy -> mJy
            out.append({
                "id": s["id"], "mjd": mjd, "detector": detector, "obsid": obsid,
                "wavelength_um": lam, "bandwidth_um": band,
                "flux_mjy": flux_sr * scale, "err_mjy": err_sr * scale,
                "flagged": flagged, "centroid_ra": c_ra, "centroid_dec": c_dec,
            })
    return out
