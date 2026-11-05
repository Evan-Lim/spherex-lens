"""
step3_zogy.py

Implements the Zackay, Ofek, & Gal-Yam (2016) proper image subtraction
algorithm and detects positive/negative dipoles (moving sources) versus
stationary monopoles (variable sources) in the significance map.
"""

import numpy as np
from numpy.fft import fft2, ifft2, fftshift
from scipy.ndimage import maximum_filter, minimum_filter


def gaussian_psf(shape, sigma_px):
    """
    Normalized 2D Gaussian PSF centred for FFT convolution.
    """
    ny, nx = shape
    y, x = np.mgrid[0:ny, 0:nx]
    psf = np.exp(-((x - nx / 2.0) ** 2 + (y - ny / 2.0) ** 2) / (2.0 * sigma_px ** 2))
    psf /= psf.sum()
    return fftshift(psf)


def zogy(img1, img2, sigma1, sigma2, psf_sigma1=1.4, psf_sigma2=1.4):
    """
    Compute the ZOGY proper difference image D and the unit-variance
    significance map S.
    """
    p1 = gaussian_psf(img1.shape, psf_sigma1)
    p2 = gaussian_psf(img2.shape, psf_sigma2)

    i1_hat = fft2(img1)
    i2_hat = fft2(img2)
    p1_hat = fft2(p1)
    p2_hat = fft2(p2)

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

    s_img = s_img / (s_img.std() + 1e-12)
    return d_img, s_img


def find_detections(S, pix_scale_arcsec, dt_years, sigma_thresh=5.0,
                    min_sep_px=0.5, max_sep_px=25.0):
    """
    Detect significant peaks and troughs in the ZOGY significance map.
    """
    peaks = (S == maximum_filter(S, size=5)) & (S > sigma_thresh)
    troughs = (S == minimum_filter(S, size=5)) & (S < -sigma_thresh)

    py, px = np.where(peaks)
    pv = S[py, px]
    ty, tx = np.where(troughs)
    tv = S[ty, tx]

    detections = []
    used_troughs = set()

    for i in range(len(py)):
        best_j = None
        best_d = 1e9
        for j in range(len(ty)):
            if j in used_troughs:
                continue
            d = np.hypot(px[i] - tx[j], py[i] - ty[j])
            if min_sep_px < d < max_sep_px and d < best_d:
                best_d = d
                best_j = j

        if best_j is not None:
            used_troughs.add(best_j)
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
