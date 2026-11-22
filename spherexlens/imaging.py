"""
spherexlens.imaging
Image-science helpers for the co-add / subtraction / triage chain (v2 phases
1.3, 3.2, 3.3, 3.6).  Pure functions on numpy arrays and FITS headers; no
imports from app.py so step2/step3/step4 can all use them.

PSF (phase 1.3)
---------------
A SPHEREx DR1 cutout carries an ``EPSF`` table: 21 x 21 detector zones, each a
33 x 33 effective PSF oversampled ``OVSMPX = 5`` times (1.24"/sample), with the
zone centres ``XCENTER/YCENTER`` in 0-based full-detector ("active") pixels.

Cutout -> detector mapping.  The IMAGE header holds an alternate linear WCS
``A`` named "0-based active pixel coordinates" with ``CRPIX1A/CRPIX2A`` (e.g.
-997, -508) and CDELT 1, CRVAL 0.  So for a 0-based cutout pixel (x, y):

    x_det = x + 1 - CRPIX1A,   y_det = y + 1 - CRPIX2A

which is exactly the IRSA tutorial ("spherex-psf": ``xpix_orig = 1 +
xpix_cutout - crpix1a``).  Cross-check: the sky WCS has CRPIX1 = 22.5 =
1020.5 - 998, i.e. its reference pixel is the centre of the 2040-pixel active
area.  The tiling is uniform: XCENTER = (i + 0.5) * 2040/21 - 0.5.

The zone nearest the target (or the cutout centre) is used.  The ePSF is in
detector (cutout) pixel axes -- both headers say ``DETCOORD = 'sky'`` -- and
our co-add grid is north-up / east-left at 3"/px, so the PSF is *resampled
through the local WCS Jacobian*: every target-grid pixel offset is mapped to a
sky offset, then to a cutout-pixel offset, then to an oversampled ePSF index
(bilinear).  Before sampling, the ePSF is convolved with the bilinear "tent"
kernel of one native pixel, because the co-add itself is made by bilinear
reprojection of 6.2" pixels -- that blur is part of the PSF the co-add really
has.  The result is normalised to sum 1, odd-sized and centred.
"""
import math

import numpy as np
from numpy.fft import ifftshift
from scipy.ndimage import convolve1d, distance_transform_edt, map_coordinates, zoom
from scipy.spatial import cKDTree

import config

GAUSS_FWHM_FACTOR = 2.0 * math.sqrt(2.0 * math.log(2.0))   # 2.3548


# ---------------------------------------------------------------------------
# PSF
# ---------------------------------------------------------------------------
def fwhm_from_array(psf, scale_arcsec):
    """
    FWHM of a PSF image from the area above half maximum: the diameter of the
    circle with that area.  Robust for slightly non-Gaussian, pixelated PSFs.
    """
    a = np.asarray(psf, dtype=float)
    if scale_arcsec > 2.0:
        # a 3" grid quantises the half-max area badly: measure it on a
        # bilinearly 5x-upsampled copy instead
        n = a.shape[0]
        a = zoom(np.where(np.isfinite(a), a, 0.0), 5, order=1)
        # zoom keeps the corner samples fixed: new spacing = (n-1)/(m-1)
        scale_arcsec = float(scale_arcsec) * (n - 1) / (a.shape[0] - 1)
    peak = np.nanmax(a)
    if not np.isfinite(peak) or peak <= 0:
        return None
    area = float(np.count_nonzero(a >= 0.5 * peak)) * scale_arcsec ** 2
    return 2.0 * math.sqrt(area / math.pi)


def gaussian_kernel(size=None, sigma_px=None):
    """Normalised, centred, odd-sized Gaussian (the documented fallback PSF)."""
    sigma_px = config.PSF_SIGMA_PX_FALLBACK if sigma_px is None else sigma_px
    size = size or config.PSF_SIZE_PX
    size = int(size) | 1
    c = size // 2
    y, x = np.mgrid[0:size, 0:size]
    k = np.exp(-((x - c) ** 2 + (y - c) ** 2) / (2.0 * sigma_px ** 2))
    return k / k.sum()


def fallback_psf_info(reason="no EPSF extension"):
    psf = gaussian_kernel()
    fwhm = GAUSS_FWHM_FACTOR * config.PSF_SIGMA_PX_FALLBACK * config.PIXSCALE_ARCSEC
    return {"psf": psf, "psf_fwhm_arcsec": fwhm, "psf_grid_fwhm_arcsec": fwhm,
            "psf_source": "gaussian_fallback", "psf_zone": None,
            "psf_note": reason}


def detector_offset(header):
    """
    (dx, dy) such that x_det = x_cutout + dx (0-based on both sides), from the
    'A' alternate WCS ("0-based active pixel coordinates").  None if absent.
    """
    try:
        c1, c2 = float(header["CRPIX1A"]), float(header["CRPIX2A"])
    except (KeyError, TypeError, ValueError):
        return None
    name = str(header.get("WCSNAMEA", "")).lower()
    if name and "pixel" not in name:
        return None
    return 1.0 - c1, 1.0 - c2


def select_epsf_zone(table, x_det, y_det):
    """Row index of the EPSF zone whose centre is nearest (x_det, y_det)."""
    xc = np.asarray(table["XCENTER"], dtype=float)
    yc = np.asarray(table["YCENTER"], dtype=float)
    d = np.hypot(xc - x_det, yc - y_det)
    return int(np.argmin(d)), float(np.min(d))


def local_jacobian_arcsec(wcs, x, y, step=0.5):
    """
    2x2 matrix J mapping a cutout-pixel offset (dx, dy) to a local sky offset
    (xi = east, eta = north) in arcsec, at cutout pixel (x, y).
    """
    pts = np.array([[x, y], [x + step, y], [x - step, y], [x, y + step], [x, y - step]])
    ra, dec = wcs.pixel_to_world_values(pts[:, 0], pts[:, 1])
    ra = np.asarray(ra, dtype=float)
    dec = np.asarray(dec, dtype=float)
    dra = ((ra - ra[0] + 180.0) % 360.0) - 180.0
    xi = dra * math.cos(math.radians(dec[0])) * 3600.0
    eta = (dec - dec[0]) * 3600.0
    j = np.array([[(xi[1] - xi[2]) / (2 * step), (xi[3] - xi[4]) / (2 * step)],
                  [(eta[1] - eta[2]) / (2 * step), (eta[3] - eta[4]) / (2 * step)]])
    if not np.all(np.isfinite(j)) or abs(np.linalg.det(j)) < 1e-6:
        raise ValueError("degenerate WCS Jacobian")
    return j


def target_axis_angles(jac):
    """
    Position angles (deg, 0..180, measured on the target grid from +x towards
    +y) of the cutout's detector x and y axes once reprojected to the
    north-up / east-left grid (u = -xi / scale, v = eta / scale).
    """
    out = []
    for col in (0, 1):
        u, v = -jac[0, col], jac[1, col]
        out.append(round(math.degrees(math.atan2(v, u)) % 180.0, 2))
    return out


def tent_blur(epsf, ovs):
    """Convolve with the bilinear-interpolation kernel of one native pixel."""
    half = int(ovs)
    k = np.array([1.0 - abs(i) / half for i in range(-half + 1, half)])
    k /= k.sum()
    return convolve1d(convolve1d(epsf, k, axis=0, mode="constant"),
                      k, axis=1, mode="constant")


def epsf_to_grid(epsf, ovs, jac, pixscale_arcsec=None, size=None, reproject_blur=True):
    """
    Resample one oversampled ePSF (detector axes, [y, x]) onto the north-up,
    east-left target grid.  Returns a normalised odd-sized array.
    """
    pixscale_arcsec = pixscale_arcsec or config.PIXSCALE_ARCSEC
    size = int(size or config.PSF_SIZE_PX) | 1
    e = np.asarray(epsf, dtype=float)
    e = np.where(np.isfinite(e), e, 0.0)
    if reproject_blur:
        e = tent_blur(e, ovs)
    ny, nx = e.shape
    cy, cx = (ny - 1) / 2.0, (nx - 1) / 2.0
    c = size // 2
    v, u = np.mgrid[0:size, 0:size].astype(float)
    u -= c
    v -= c
    xi = -u * pixscale_arcsec            # +u (columns) points west on the grid
    eta = v * pixscale_arcsec
    jinv = np.linalg.inv(jac)
    dx = jinv[0, 0] * xi + jinv[0, 1] * eta
    dy = jinv[1, 0] * xi + jinv[1, 1] * eta
    out = map_coordinates(e, [cy + ovs * dy, cx + ovs * dx], order=1,
                          mode="constant", cval=0.0)
    out = np.clip(out, 0.0, None)
    s = out.sum()
    if not np.isfinite(s) or s <= 0:
        raise ValueError("empty resampled PSF")
    return out / s


def psf_from_hdul(hdul, img_header, wcs, xy=None):
    """
    Build the target-grid PSF for one cutout.  ``xy`` = 0-based cutout pixel to
    pick the zone at (default: cutout centre).  Falls back to a Gaussian with
    ``config.PSF_SIGMA_PX_FALLBACK`` when anything is missing.
    """
    if "EPSF" not in hdul:
        return fallback_psf_info("no EPSF extension")
    try:
        tab = hdul["EPSF"]
        hdr = tab.header
        data = tab.data
        ovs = int(hdr.get("OVSMPX", 1) or 1)
        if int(hdr.get("OVSMPY", ovs) or ovs) != ovs:
            return fallback_psf_info("anisotropic EPSF oversampling")
        off = detector_offset(img_header)
        if off is None:
            return fallback_psf_info("no detector offset (CRPIX1A/CRPIX2A)")
        ny, nx = img_header.get("NAXIS2"), img_header.get("NAXIS1")
        if xy is None:
            xy = ((nx - 1) / 2.0, (ny - 1) / 2.0)
        x, y = xy
        row, dist = select_epsf_zone(data, x + off[0], y + off[1])
        stamp = np.asarray(data["EPSF"][row], dtype=float)
        if stamp.ndim == 1:
            n = int(round(math.sqrt(stamp.size)))
            stamp = stamp.reshape(n, n)       # ORDERING = 'x-fast then y'
        jac = local_jacobian_arcsec(wcs, x, y)
        native_arcsec = math.sqrt(abs(np.linalg.det(jac)))
        psf = epsf_to_grid(stamp, ovs, jac)
        fwhm = fwhm_from_array(stamp, native_arcsec / ovs)
        zone = {"row": row, "binx": int(data["BINX"][row]),
                "biny": int(data["BINY"][row]),
                "x_det": round(x + off[0], 1), "y_det": round(y + off[1], 1),
                "dist_px": round(dist, 1)}
        for k in ("NSTAR", "CWAVE"):
            if k in data.columns.names:
                zone[k.lower()] = float(data[k][row])
        return {"psf": psf, "psf_fwhm_arcsec": fwhm,
                "psf_grid_fwhm_arcsec": fwhm_from_array(psf, config.PIXSCALE_ARCSEC),
                "psf_source": "epsf", "psf_zone": zone,
                "axis_angles_deg": target_axis_angles(jac), "psf_note": None}
    except Exception as exc:                    # never fail an exposure on its PSF
        return fallback_psf_info(f"EPSF unusable: {type(exc).__name__}: {exc}")


def psf_fft_kernel(psf, shape):
    """Embed a small centred PSF in an image-sized array with its centre at [0, 0]."""
    ny, nx = shape
    p = np.asarray(psf, dtype=float)
    py, px = p.shape
    if py > ny or px > nx:
        p = p[(py - ny) // 2:(py - ny) // 2 + ny, (px - nx) // 2:(px - nx) // 2 + nx]
        py, px = p.shape
    big = np.zeros((ny, nx))
    y0, x0 = ny // 2 - py // 2, nx // 2 - px // 2
    big[y0:y0 + py, x0:x0 + px] = p
    big /= big.sum()
    return ifftshift(big)


def combine_psfs(psfs, weights):
    """Weighted mean of equally-sized normalised PSFs (renormalised)."""
    psfs = [np.asarray(p, dtype=float) for p in psfs]
    if not psfs:
        return None
    size = max(p.shape[0] for p in psfs) | 1
    acc = np.zeros((size, size))
    wsum = 0.0
    for p, w in zip(psfs, weights):
        if not (w > 0) or not np.isfinite(w):
            continue
        pad = (size - p.shape[0]) // 2
        acc[pad:pad + p.shape[0], pad:pad + p.shape[1]] += w * p
        wsum += w
    if wsum <= 0:
        acc = sum(psfs) / len(psfs)
    s = acc.sum()
    return acc / s if s > 0 else None


def psf_sigma_px(fwhm_arcsec, pixscale_arcsec=None):
    """Gaussian sigma (target-grid pixels) with the given FWHM."""
    pixscale_arcsec = pixscale_arcsec or config.PIXSCALE_ARCSEC
    if not fwhm_arcsec:
        return config.PSF_SIGMA_PX_FALLBACK
    return float(fwhm_arcsec) / GAUSS_FWHM_FACTOR / pixscale_arcsec


# ---------------------------------------------------------------------------
# Exposure quality (phase 3.2)
# ---------------------------------------------------------------------------
def robust_noise_ratio(img, var, valid):
    """
    Pixel-to-pixel noise divided by the noise the VARIANCE layer predicts.

    Differences of horizontal neighbours remove smooth structure (zodi,
    nebulosity, source confusion on large scales); the MAD of those, /sqrt(2),
    is the white-noise sigma.  Normalising by sqrt(VARIANCE) makes the number
    comparable across detector bands (~1.05-2.4 on real DR1 cutouts).
    """
    ok = valid[:, 1:] & valid[:, :-1]
    if np.count_nonzero(ok) < 100:
        return None
    d = (img[:, 1:] - img[:, :-1])[ok]
    mad = 1.4826 * np.median(np.abs(d - np.median(d))) / math.sqrt(2.0)
    expect = np.median(np.sqrt(var[valid]))
    if not (expect > 0):
        return None
    return float(mad / expect)


def _ramp(x, ok, bad):
    """1 at/below ``ok``, 0 at/above ``bad``, linear between."""
    if x is None or not np.isfinite(x):
        return 1.0
    return float(np.clip(1.0 - (x - ok) / (bad - ok), 0.0, 1.0))


def exposure_metrics(img, flags, var, header, bad_bits):
    """Raw per-exposure quality numbers (no comparison with other exposures)."""
    finite = np.isfinite(img)
    valid = ((flags & bad_bits) == 0) & finite & (var > 0)
    m = {"flag_frac": float(1.0 - np.count_nonzero(valid) / max(valid.size, 1)),
         "noise_ratio": robust_noise_ratio(img, var, valid)}
    solved = header.get("ASTRONETSOLVED") if header is not None else None
    dqa = [str(header.get(k)).strip() for k in ("L1DQAFLG", "L2DQAFLG")
           if header is not None and header.get(k) is not None]
    m["astrometry_solved"] = None if solved is None else bool(solved)
    m["dqa_pass"] = all(v.lower() == "pass" for v in dqa) if dqa else None
    return m


def score_exposures(entries, quality_min=None):
    """
    Combine per-exposure metrics into a 0..1 score and decide which to keep.

    ``entries``: list of dicts with keys epoch, band, obs_id, metrics,
    psf_fwhm_arcsec.  Mutates and returns them with score/kept/reasons.

        s_flag  = ramp(flag_frac, EQ_FLAG_OK, EQ_FLAG_BAD)
        s_noise = ramp(max(noise/epoch_median, noise/EQ_NOISE_ABS_REF),
                       EQ_NOISE_OK, EQ_NOISE_BAD)
        s_psf   = ramp(fwhm/median fwhm of the same detector band, EQ_PSF_OK, EQ_PSF_BAD)
        s_astro = 1, or EQ_ASTRO_UNSOLVED if the pipeline's astrometric solve
                  failed (ASTRONETSOLVED False), times EQ_DQA_FAIL if the
                  L1/L2 data-quality flags are not 'Pass'
        score   = s_flag * s_noise * s_psf * s_astro

    An exposure is kept when score >= quality_min (default
    config.EXPOSURE_QUALITY_MIN); if that would empty
    an epoch, its best exposure is kept anyway and says so.
    """
    quality_min = config.EXPOSURE_QUALITY_MIN if quality_min is None else quality_min
    by_epoch, by_band = {}, {}
    for e in entries:
        by_epoch.setdefault(e["epoch"], []).append(e)
        by_band.setdefault(e.get("band"), []).append(e)

    def med(vals):
        vals = [v for v in vals if v is not None and np.isfinite(v)]
        return float(np.median(vals)) if vals else None

    for ep, group in by_epoch.items():
        noise_med = med([g["metrics"].get("noise_ratio") for g in group])
        for e in group:
            m = e["metrics"]
            reasons = []
            ff = m.get("flag_frac", 0.0)
            s_flag = _ramp(ff, config.EQ_FLAG_OK, config.EQ_FLAG_BAD)
            reasons.append(f"flagged {100 * ff:.0f}%")
            nr = m.get("noise_ratio")
            rel = None
            if nr is not None:
                rel = max(nr / noise_med if noise_med else 1.0,
                          nr / config.EQ_NOISE_ABS_REF)
                reasons.append(f"noise {nr:.2f}x expected ({nr / noise_med:.2f}x epoch median)"
                               if noise_med else f"noise {nr:.2f}x expected")
            s_noise = _ramp(rel, config.EQ_NOISE_OK, config.EQ_NOISE_BAD)
            fw = e.get("psf_fwhm_arcsec")
            band_med = med([b.get("psf_fwhm_arcsec") for b in by_band.get(e.get("band"), [])])
            prel = fw / band_med if fw and band_med else None
            s_psf = _ramp(prel, config.EQ_PSF_OK, config.EQ_PSF_BAD)
            if fw:
                reasons.append(f"psf fwhm {fw:.1f}\" ({fw / config.PIXSCALE_ARCSEC:.1f} px)")
            s_astro = 1.0
            if m.get("astrometry_solved") is False:
                s_astro *= config.EQ_ASTRO_UNSOLVED
                reasons.append("astrometric solution failed")
            if m.get("dqa_pass") is False:
                s_astro *= config.EQ_DQA_FAIL
                reasons.append("pipeline data-quality flag not 'Pass'")
            score = s_flag * s_noise * s_psf * s_astro
            e.update({"score": round(float(score), 3),
                      "components": {"flags": round(s_flag, 3), "noise": round(s_noise, 3),
                                     "psf": round(s_psf, 3), "astrometry": round(s_astro, 3)},
                      "kept": bool(score >= quality_min),
                      "reasons": reasons})
            if not e["kept"]:
                e["reasons"].append(f"dropped: score below {quality_min}")
        if group and not any(g["kept"] for g in group):
            best = max(group, key=lambda g: g["score"])
            best["kept"] = True
            best["reasons"] = [r for r in best["reasons"] if not r.startswith("dropped")]
            best["reasons"].append("kept although below threshold: best exposure of "
                                   "an otherwise empty epoch")
    return entries


# ---------------------------------------------------------------------------
# Masks (phase 3.3)
# ---------------------------------------------------------------------------
def footprint_edge_map(footprints, n_px=None):
    """
    Count, per target pixel, how many exposure footprints have an edge within
    ``n_px`` pixels (on either side of the edge).  The border of the target
    grid itself is not an edge.
    """
    n_px = config.EDGE_MASK_PX if n_px is None else n_px
    out = None
    for fp in footprints:
        fp = np.asarray(fp, dtype=bool)
        if out is None:
            out = np.zeros(fp.shape, dtype=np.int16)
        if fp.all() or not fp.any():
            continue
        inside = distance_transform_edt(fp)        # distance to nearest outside px
        outside = distance_transform_edt(~fp)      # distance to nearest inside px
        near = (fp & (inside <= n_px)) | (~fp & (outside <= n_px))
        out += near.astype(np.int16)
    return out


def edge_hit(edge_map, positions):
    """Max edge count over the given (y, x) positions (0 if none / no map)."""
    if edge_map is None:
        return 0
    ny, nx = edge_map.shape
    best = 0
    for p in positions:
        if not p:
            continue
        y, x = int(p[0]), int(p[1])
        if 0 <= y < ny and 0 <= x < nx:
            best = max(best, int(edge_map[y, x]))
    return best


def spike_hit(positions, stars, angles_deg, half_width_px=None, length_factor=None):
    """
    Is any detection position on a bright star's spike / readout cross?

    stars      -- [{"x", "y", "mask_arcsec", "mag", "cat", "ra", "dec"}] on the
                  target grid; ``mask_arcsec`` from catalog_tools.bright_mask_radius
    angles_deg -- spike directions on the target grid (detector x/y axes of every
                  exposure, see target_axis_angles)

    SPHEREx is an unobscured off-axis telescope, so it has no spider spikes;
    what bright stars do show are artefacts along the detector rows and
    columns (bleeding, readout crosstalk, persistence streaks).  The mask is
    therefore a cross along each exposure's detector axes, of length
    SPIKE_LENGTH_FACTOR x the star's bright-star mask radius and half-width
    SPIKE_HALF_WIDTH_ARCSEC.  Inside the circular mask itself the existing
    bright-star rule applies, so only the arms beyond it are tested.
    """
    half_width_px = (config.SPIKE_HALF_WIDTH_ARCSEC / config.PIXSCALE_ARCSEC
                     if half_width_px is None else half_width_px)
    length_factor = config.SPIKE_LENGTH_FACTOR if length_factor is None else length_factor
    angles = sorted({round(float(a) % 180.0, 1) for a in angles_deg or []})
    if not angles or not stars:
        return None
    best = None
    for s in stars:
        r_core = s["mask_arcsec"] / config.PIXSCALE_ARCSEC
        r_len = length_factor * r_core
        for p in positions:
            if not p:
                continue
            dy, dx = float(p[0]) - s["y"], float(p[1]) - s["x"]
            r = math.hypot(dx, dy)
            if r <= r_core or r > r_len:
                continue
            for a in angles:
                t = math.radians(a)
                perp = abs(-dx * math.sin(t) + dy * math.cos(t))
                if perp <= half_width_px and (best is None or s["mag"] < best["mag"]):
                    best = {"cat": s["cat"], "mag": s["mag"], "ra": s["ra"], "dec": s["dec"],
                            "sep_arcsec": round(r * config.PIXSCALE_ARCSEC, 1),
                            "angle_deg": a,
                            "length_arcsec": round(r_len * config.PIXSCALE_ARCSEC, 1)}
    return best


def bright_stars_on_grid(catalogs, wcs, npix, mask_radius_fn):
    """Bright (mask-worthy) catalogue stars with target-grid pixel positions."""
    out = []
    margin = 0
    for cat, rows in (catalogs or {}).items():
        for s in rows or []:
            r = mask_radius_fn(cat, s)
            if r is None or s.get("ra") is None or s.get("dec") is None:
                continue
            try:
                x, y = (float(v) for v in wcs.world_to_pixel_values(s["ra"], s["dec"]))
            except Exception:
                continue
            reach = config.SPIKE_LENGTH_FACTOR * r / config.PIXSCALE_ARCSEC
            if -reach - margin <= x <= npix + reach and -reach <= y <= npix + reach:
                mag = s.get("gmag") if cat == "gaia" else s.get("w1")
                out.append({"x": x, "y": y, "mask_arcsec": r, "cat": cat,
                            "mag": 99.0 if mag is None else float(mag),
                            "ra": s["ra"], "dec": s["dec"]})
    return out


# ---------------------------------------------------------------------------
# De-duplication across epoch pairs (phase 3.6)
# ---------------------------------------------------------------------------
def _det_pos(d):
    return d.get("pos_plus") or d.get("pos") or (0, 0)


def dedup_detections(raw, radius_px=None):
    """
    Merge the same object found in several epoch pairs: greedy in descending
    SNR, a detection is dropped when a stronger kept one lies within
    ``radius_px`` in both x and y (Chebyshev distance, as the old 2-pixel loop).
    Kept detections gain ``n_pairs_seen`` (distinct pairs among the merged
    group, itself included) and ``pairs_seen``.
    """
    radius_px = config.MERGE_RADIUS_PX if radius_px is None else radius_px
    if not raw:
        return []
    order = sorted(range(len(raw)), key=lambda i: -raw[i]["snr"])
    pts = np.array([_det_pos(raw[i]) for i in order], dtype=float)
    tree = cKDTree(pts)
    owner = [-1] * len(order)
    kept = []
    for k, i in enumerate(order):
        if owner[k] != -1:
            continue
        owner[k] = k
        d = raw[i]
        group_pairs = []
        for j in sorted(tree.query_ball_point(pts[k], r=radius_px + 1e-9, p=np.inf)):
            if owner[j] == -1 or j == k:
                owner[j] = k
                pr = raw[order[j]].get("pair")
                if pr is not None and pr not in group_pairs:
                    group_pairs.append(pr)
        d["pairs_seen"] = group_pairs
        d["n_pairs_seen"] = max(1, len(group_pairs))
        kept.append(d)
    return kept
