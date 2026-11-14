"""
step3_zogy.py

Implements the Zackay, Ofek, & Gal-Yam (2016) proper image subtraction
algorithm and detects positive/negative dipoles (moving sources) versus
stationary monopoles (variable sources: brighter = ``monopole``, fainter =
``monopole_negative``) in the significance map.
"""

import numpy as np
from numpy.fft import fft2, ifft2, fftshift
from scipy.ndimage import maximum_filter, minimum_filter
from scipy.spatial import cKDTree


def gaussian_psf(shape, sigma_px):
    """
    Normalized 2D Gaussian PSF centred for FFT convolution.
    """
    ny, nx = shape
    y, x = np.mgrid[0:ny, 0:nx]
    psf = np.exp(-((x - nx / 2.0) ** 2 + (y - ny / 2.0) ** 2) / (2.0 * sigma_px ** 2))
    psf /= psf.sum()
    return fftshift(psf)


def _psf_hat(psf, shape, psf_sigma):
    """FFT of the PSF: a measured array if given, else the Gaussian fallback."""
    if psf is not None:
        from spherexlens.imaging import psf_fft_kernel
        return fft2(psf_fft_kernel(psf, shape))
    return fft2(gaussian_psf(shape, psf_sigma))


def zogy(img1, img2, sigma1, sigma2, psf_sigma1=1.4, psf_sigma2=1.4,
         psf1=None, psf2=None, astrometric_sigma_px=None):
    """
    Compute the ZOGY proper difference image D and the unit-variance
    significance map S.

    psf1/psf2 -- measured PSFs (small, centred, odd-sized arrays on the image
        grid, e.g. step2_coadd.coadd_matched(..., return_psf=True)).  When
        omitted, a Gaussian of psf_sigma1/psf_sigma2 pixels is used.
    astrometric_sigma_px -- registration noise (pixels, per axis) for the
        Zackay, Ofek & Gal-Yam (2016) astrometric-noise correction
        (their section 3.3, eqs. 30-33):

            S_corr = S / sqrt(V_S + V_ast),
            V_ast  = sigma_ast^2 * (|grad S_N|^2 + |grad S_R|^2)

        S = S_N - S_R splits into the new- and reference-image parts.  Where
        the image is steep (bright stars), a fraction of a pixel of
        misregistration -- unavoidable when an undersampled SPHEREx frame
        (6.2" pixels, ~7" PSF) is resampled -- leaves residuals of a few % of
        the star; V_ast down-weights exactly those pixels.  None ->
        config.ZOGY_ASTROMETRIC_SIGMA_PX (0 = off, the original behaviour);
        the value is calibrated on the null test (spherexlens.eval).
    """
    p1_hat = _psf_hat(psf1, img1.shape, psf_sigma1)
    p2_hat = _psf_hat(psf2, img2.shape, psf_sigma2)

    i1_hat = fft2(img1)
    i2_hat = fft2(img2)

    denominator = np.sqrt(
        sigma1 ** 2 * np.abs(p2_hat) ** 2 +
        sigma2 ** 2 * np.abs(p1_hat) ** 2 +
        1e-30
    )

    d_hat = (p1_hat * i2_hat - p2_hat * i1_hat) / denominator
    d_img = np.real(ifft2(d_hat))

    f_d = 1.0 / np.sqrt(sigma1 ** 2 + sigma2 ** 2)
    p_d_hat = (p1_hat * p2_hat) / (f_d * denominator)
    s_hat = f_d * d_hat * np.conj(p_d_hat)
    s_img = np.real(ifft2(s_hat))

    if astrometric_sigma_px is None:
        import config
        astrometric_sigma_px = float(getattr(config, "ZOGY_ASTROMETRIC_SIGMA_PX", 0.0))
    scale = s_noise_scale(s_img) + 1e-12
    if astrometric_sigma_px and astrometric_sigma_px > 0:
        # S_N (from the new image) and S_R (from the reference) such that
        # s_img = S_N - S_R exactly (same f_d / denominator as above).
        den2 = denominator ** 2
        sn = np.real(ifft2(i2_hat * np.abs(p1_hat) ** 2 * np.conj(p2_hat) / den2))
        sr = np.real(ifft2(i1_hat * np.abs(p2_hat) ** 2 * np.conj(p1_hat) / den2))
        gny, gnx = np.gradient(sn)
        gry, grx = np.gradient(sr)
        v_ast = astrometric_sigma_px ** 2 * (gnx ** 2 + gny ** 2 + grx ** 2 + gry ** 2)
        s_img = s_img / np.sqrt(scale ** 2 + v_ast)
    else:
        s_img = s_img / scale
    return d_img, s_img


def s_noise_scale(s_img, mode=None):
    """
    The number S is divided by so that noise-only pixels have unit spread.

    "std" (original): the plain standard deviation.  One bright-star residual
    (thousands of sigma) inflates it, which scales every real detection in the
    map down -- the Phase 4 strict injection test recovered 0.2 % of planted
    sources this way.
    "robust": 1.4826 x the median absolute deviation, which ignores a few
    extreme pixels and gives unit noise in a noise-limited map.

    config.ZOGY_S_NORMALISATION selects it; switch to "robust" only together
    with a detection threshold calibrated on the null test (eval/threshold.json).
    """
    import config
    mode = mode or getattr(config, "ZOGY_S_NORMALISATION", "std")
    if mode == "robust":
        v = s_img[np.isfinite(s_img)]
        if v.size >= 10:
            mad = 1.4826 * float(np.median(np.abs(v - np.median(v))))
            if mad > 0:
                return mad
    return float(np.nanstd(s_img))


def dipole_max_sep_px(dt_years, pix_scale_arcsec):
    """Largest lobe separation a real mover can produce over dt_years.

    No star moves faster than config.DIPOLE_MAX_PM_ARCSEC_YR (Barnard's Star,
    the fastest, does 10.4"/yr); the pad covers centroid scatter of the two
    lobes.  Capped at the old fixed 25 px.
    """
    import config
    dt = dt_years if dt_years and dt_years > 0 else 0.5
    sep = config.DIPOLE_MAX_PM_ARCSEC_YR * dt / pix_scale_arcsec + config.DIPOLE_SEP_PAD_PX
    return float(min(25.0, max(config.DIPOLE_SEP_PAD_PX + 1.0, sep)))


def find_detections(S, pix_scale_arcsec, dt_years, sigma_thresh=5.0,
                    min_sep_px=0.5, max_sep_px=None, report_fading=True,
                    max_amplitude_ratio=None):
    """
    Detect significant peaks and troughs in the ZOGY significance map.

    Peaks are taken strongest first; each is paired with the nearest unused
    trough at min_sep_px < d < max_sep_px whose strength is comparable
    (max(|peak|, |trough|) / min(...) <= max_amplitude_ratio) -> ``dipole``.
    A mover's two lobes are the same star, so they have similar strength; a
    strong fading source next to an unrelated weak residual must not be
    swallowed into a fake "mover" (the strict injection test lost most fading
    sources that way with the old fixed 25 px, any-strength rule).
    A peak without a trough is a ``monopole`` (got brighter, ``sign: +1``); a
    trough without a peak is a ``monopole_negative`` (got fainter,
    ``sign: -1``, ``snr`` = |trough|).

    max_sep_px None -> dipole_max_sep_px(dt_years) (physical motion limit);
    max_amplitude_ratio None -> config.DIPOLE_MAX_AMPLITUDE_RATIO.
    """
    import config
    if max_sep_px is None:
        max_sep_px = dipole_max_sep_px(dt_years, pix_scale_arcsec)
    if max_amplitude_ratio is None:
        max_amplitude_ratio = float(getattr(config, "DIPOLE_MAX_AMPLITUDE_RATIO", 1e9))
    peaks = (S == maximum_filter(S, size=5)) & (S > sigma_thresh)
    troughs = (S == minimum_filter(S, size=5)) & (S < -sigma_thresh)

    py, px = np.where(peaks)
    pv = S[py, px]
    order = np.argsort(-pv, kind="stable")          # strongest peaks pair first
    py, px, pv = py[order], px[order], pv[order]
    ty, tx = np.where(troughs)
    tv = S[ty, tx]

    detections = []
    used = np.zeros(len(ty), dtype=bool)
    tree = cKDTree(np.column_stack([tx, ty]).astype(float)) if len(ty) else None

    for i in range(len(py)):
        best_j = None
        best_d = None
        if tree is not None:
            cand = tree.query_ball_point([float(px[i]), float(py[i])], r=max_sep_px)
            for j in sorted(cand):                  # raster order breaks ties
                if used[j]:
                    continue
                d = np.hypot(px[i] - tx[j], py[i] - ty[j])
                hi, lo = max(pv[i], -tv[j]), min(pv[i], -tv[j])
                if hi > max_amplitude_ratio * lo:
                    continue                        # not the same star
                if min_sep_px < d < max_sep_px and (best_d is None or d < best_d):
                    best_d = d
                    best_j = j

        if best_j is not None:
            used[best_j] = True
            mu = pix_scale_arcsec * best_d / dt_years
            flux_consistency = abs(pv[i] + tv[best_j]) / (pv[i] - tv[best_j] + 1e-9)
            detections.append({
                "kind": "dipole",
                "pos_plus": (int(py[i]), int(px[i])),
                "pos_minus": (int(ty[best_j]), int(tx[best_j])),
                "snr": float(pv[i]),
                "proper_motion_arcsec_yr": float(mu),
                "flux_consistency": float(flux_consistency),
            })
        else:
            detections.append({
                "kind": "monopole",
                "pos": (int(py[i]), int(px[i])),
                "snr": float(pv[i]),
                "sign": +1,
            })

    if report_fading:
        for j in np.flatnonzero(~used):
            detections.append({
                "kind": "monopole_negative",
                "pos": (int(ty[j]), int(tx[j])),
                "snr": float(abs(tv[j])),
                "sign": -1,
            })

    return detections


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    img1 = rng.normal(0, 1, (180, 180))
    img2 = rng.normal(0, 1, (180, 180))
    y, x = np.mgrid[0:180, 0:180]
    img1 += 20 * np.exp(-((x - 90) ** 2 + (y - 90) ** 2) / (2 * 1.4 ** 2))
    img2 += 20 * np.exp(-((x - 95) ** 2 + (y - 90) ** 2) / (2 * 1.4 ** 2))

    D, S = zogy(img1, img2, 1.0, 1.0)
    dets = find_detections(S, 3.0, 0.5)
    print(f"Detected {len(dets)} source(s).")
    for d in dets[:3]:
        print(f" {d['kind']} at SNR {d['snr']:.1f}")
