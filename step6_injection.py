"""
step6_injection.py

Injects synthetic moving sources into real SPHEREx co-adds, runs the ZOGY
detection pipeline, and measures the fraction recovered.  Repeating this over
a grid of signal-to-noise and proper motion produces the recovery curve
displayed in the UI.

v2 (Phase 4.2): the test is now *strict* -- a source counts as recovered only
if a "dipole" detection has its positive lobe within 1.5 px of the injected
new position (the old test accepted any |S| > 5 in a 10x10 box), and every
mover moves in a uniformly random direction (they all used to move the same
way).  The richer evaluation -- real PSF, brightening/fading variables,
Wilson intervals -- is spherexlens.eval.injection.run_strict_injection; this
function keeps its original signature and return value for the dashboard.
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
                           progress=None, dt_years=0.5, pixscale_arcsec=3.0,
                           sigma_thresh=None):
    """
    For each (snr, mu) pair, inject n_trials moving sources (random position,
    random direction) and count how many come back as a dipole whose positive
    lobe is within 1.5 px of the injected position.

    snr is the legacy peak-pixel SNR (peak = snr * noise1).  Returns
    {(snr, mu): fraction}.
    """
    import config
    from spherexlens.eval import harness
    from spherexlens.eval.injection import run_strict_injection

    sigma_thresh = sigma_thresh or config.DETECTION_SIGMA
    psf = {"stamp": harness.gaussian_stamp(psf_sigma_px), "sigma_px": psf_sigma_px,
           "fwhm_px": 2.3548 * psf_sigma_px, "model": "gaussian"}
    s1 = float(np.nanmean(noise1)) if np.ndim(noise1) else float(noise1)
    s2 = float(np.nanmean(noise2)) if np.ndim(noise2) else float(noise2)

    def find(S, dt, thresh, pixscale):
        return find_detections(S, pix_scale_arcsec=pixscale, dt_years=dt,
                               sigma_thresh=thresh)

    res = run_strict_injection(
        img1, img2, s1, s2, psf=psf, snr_grid=snr_grid, mu_grid=mu_grid,
        categories=("mover",), n_trials=n_trials, seed=seed, dt_years=dt_years,
        pixscale=pixscale_arcsec, sigma_thresh=sigma_thresh, per_image=1,
        snr_mode="peak", margin=30, zogy_fn=zogy, find_fn=find, progress=progress,
        normalisation="pipeline")
    return {(c["snr"], c["mu"]): (c["n_recovered"] / float(n_trials))
            for c in res["grid"]}


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
