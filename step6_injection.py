"""
step6_injection.py

Injects synthetic moving sources into real SPHEREx cutouts, runs the
full ZOGY detection pipeline, and measures the fraction of injected
sources recovered at >5 sigma. Repeating this over a grid of signal-to-
noise and proper motion produces the recovery curve displayed in the UI.
"""

import numpy as np
from step3_zogy import zogy, find_detections


def inject_point_source(img, psf_sigma_px, amplitude, cx, cy):
    """Add a PSF-convolved point source at (cx, cy) to the image."""
    ny, nx = img.shape
    y, x = np.mgrid[0:ny, 0:nx]
    profile = amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * psf_sigma_px ** 2))
    return img + profile


def run_injection_recovery(img1, img2, noise1, noise2, psf_sigma_px,
                           snr_grid, mu_grid, n_trials=20, seed=42,
                           progress=None):
    """
    For each (snr, mu) pair, inject n_trials synthetic sources at random
    positions and count how many are recovered at >5 sigma.
    """
    rng = np.random.default_rng(seed)
    results = {}

    ny, nx = img1.shape
    margin = 30
    total_cells = len(snr_grid) * len(mu_grid)
    completed_cells = 0

    for snr in snr_grid:
        for mu in mu_grid:
            recovered = 0
            for trial in range(n_trials):
                cx = rng.uniform(margin, nx - margin)
                cy = rng.uniform(margin, ny - margin)

                amplitude = snr * float(np.mean(noise1))
                shift_px = (mu * 0.5) / 3.0

                i1_inj = inject_point_source(img1, psf_sigma_px, amplitude, cx, cy)
                i2_inj = inject_point_source(img2, psf_sigma_px, amplitude,
                                             cx + shift_px, cy + shift_px * 0.3)

                try:
                    _, S = zogy(i1_inj, i2_inj,
                                float(np.mean(noise1)),
                                float(np.mean(noise2)),
                                psf_sigma_px, psf_sigma_px)
                except Exception:
                    continue

                cyi, cxi = int(cy), int(cx)
                window = S[max(0, cyi-5):cyi+5, max(0, cxi-5):cxi+5]
                if window.size > 0 and np.abs(window).max() > 5.0:
                    recovered += 1

            results[(snr, mu)] = recovered / float(n_trials)
            completed_cells += 1
            if progress:
                progress({
                    "completed": completed_cells,
                    "total": total_cells,
                    "snr": float(snr),
                    "mu": float(mu),
                    "recovered": recovered,
                    "trials": n_trials,
                })

    return results


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    img1 = rng.normal(0, 1, (180, 180))
    img2 = rng.normal(0, 1, (180, 180))

    results = run_injection_recovery(
        img1, img2, 1.0, 1.0, 1.4,
        snr_grid=[5, 7, 10, 15],
        mu_grid=[1, 2, 5, 10],
        n_trials=10,
    )

    for key, val in sorted(results.items()):
        snr, mu = key
        print(f"SNR={snr:>2}, mu={mu:>2}\"/yr: recovery {val*100:>5.1f}%")
