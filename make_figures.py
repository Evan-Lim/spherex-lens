"""
make_figures.py

OPTIONAL developer utility -- this file is NOT one of the ten blueprint files.

Runs the six-step pipeline on the cached demo data (make_demo_data.py, or real
IRSA cutouts) and writes the charts the written submission needs into
figures/:

  fig01_band_coverage.png       102-band spectra of the five targets + D1-D6
  fig02_coadd_panels.png        epoch 1, epoch 2, ZOGY D, significance S
  fig03_detections.png          detections marked on the significance map
  fig04_spectrum_classification.png  spectrum with the index windows drawn
  fig05_recovery.png            injection-recovery curves and heat map
  fig06_flags_masking.png       FLAGS bit coverage and masked fraction
  fig07_targets_summary.png     per-target detections and SNR
  fig08_photometry.png          per-exposure photometry of a mover + variable
  summary.json / summary.csv    every number behind figures 2-7

Run:  python make_figures.py [--outdir figures] [--detail-target WISE_0855]
"""

import os
import csv
import json
import argparse

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from astropy.io import fits

from step2_coadd import (coadd_detector, make_target_wcs, load_exposure,
                         BAD_BITS, BIT_SATURATED, BIT_COSMIC_RAY, BIT_HOT_PIXEL,
                         BIT_DEAD, BIT_STRAY_LIGHT)
from step3_zogy import zogy, find_detections
from step4_triage import triage
from step5_spectrum import extract_spectrum_from_cutout, three_index_classifier
from step6_injection import run_injection_recovery

CACHE = "data_cache"
NPIX = 180
PIXSCALE = 3.0
DT_YEARS = 0.5

plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": "#94a3b8",
    "axes.labelcolor": "#334155",
    "axes.titlesize": 11,
    "axes.labelsize": 9,
    "xtick.color": "#475569",
    "ytick.color": "#475569",
    "font.size": 9,
    "figure.dpi": 130,
    "savefig.dpi": 130,
    "savefig.bbox": "tight",
})

BLUE, GREEN, RED, AMBER = "#0ea5e9", "#16a34a", "#dc2626", "#f59e0b"


def load_meta(target):
    with open(os.path.join(CACHE, f"{target}_meta.json")) as f:
        return json.load(f)


def available_targets():
    if not os.path.isdir(CACHE):
        return []
    return sorted(f.replace("_meta.json", "") for f in os.listdir(CACHE)
                  if f.endswith("_meta.json"))


def read_spectrum(path):
    with fits.open(path) as hdul:
        tab = hdul["SPECTRUM"].data
        return (np.asarray(tab["WAVELENGTH"], float),
                np.asarray(tab["FLUX"], float),
                np.asarray(tab["ERROR"], float))


def analyse(target):
    """Run stages 2-5 on one cached target and collect everything we plot."""
    meta = load_meta(target)
    ra, dec = meta["meta"]["ra"], meta["meta"]["dec"]
    paths = meta["paths"]
    wcs = make_target_wcs(ra, dec, npix=NPIX, pixscale_arcsec=PIXSCALE)

    half = max(1, len(paths) // 2)
    img1, noise1 = coadd_detector(paths[:half], wcs, npix=NPIX)
    img2, noise2 = coadd_detector(paths[half:], wcs, npix=NPIX)
    sigma1 = float(np.nanmedian(noise1))
    sigma2 = float(np.nanmedian(noise2))

    D, S = zogy(img1, img2, sigma1, sigma2)
    detections = find_detections(S, PIXSCALE, DT_YEARS)

    rows = []
    for d in detections:
        t = triage(d, ra, dec, NPIX, PIXSCALE, meta["catalogs"])
        t["kind"] = d["kind"]
        t["snr"] = d["snr"]
        t["position"] = d.get("pos_plus") or d.get("pos")
        if "pos_minus" in d:
            t["pos_minus"] = d["pos_minus"]
        if "proper_motion_arcsec_yr" in d:
            t["proper_motion_arcsec_yr"] = d["proper_motion_arcsec_yr"]
        rows.append(t)

    result = {
        "target": target,
        "common": meta["meta"].get("common", target),
        "ra": ra, "dec": dec,
        "paths": paths,
        "img1": img1, "img2": img2, "D": D, "S": S,
        "sigma1": sigma1, "sigma2": sigma2,
        "detections": rows,
        "n_epoch1": half, "n_epoch2": len(paths) - half,
    }

    if meta.get("spectrum_fits") and os.path.exists(meta["spectrum_fits"]):
        lam, flux, err = read_spectrum(meta["spectrum_fits"])
        result["spectrum"] = {
            "wavelengths": lam, "flux": flux, "error": err,
            "classification": three_index_classifier(lam, flux),
        }
    return result


def extent(ax, npix=NPIX, pixscale=PIXSCALE):
    half = npix * pixscale / 2.0
    ax.set_xlabel("RA offset (arcsec)")
    ax.set_ylabel("Dec offset (arcsec)")
    return [-half, half, -half, half]


def image_panel(ax, data, title, cmap="viridis", tag=None, sigma=None):
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        vmin, vmax = 0.0, 1.0
    else:
        vmin, vmax = np.percentile(finite, [1, 99])
        if vmax <= vmin:
            vmax = vmin + 1e-6
    im = ax.imshow(data, origin="lower", cmap=cmap, vmin=vmin, vmax=vmax,
                   extent=extent(ax))
    ax.set_title(title)
    ax.tick_params(labelsize=7)
    return im


def fig_band_coverage(targets, outdir):
    """The 102 SPHEREx channels and each target's spectrum."""
    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(9, 6.4), gridspec_kw={"height_ratios": [1, 3]})

    spans = {"D1": (0.75, 1.09), "D2": (1.10, 1.62), "D3": (1.63, 2.41),
             "D4": (2.42, 3.82), "D5": (3.83, 4.41), "D6": (4.42, 5.00)}
    colors = plt.cm.plasma(np.linspace(0.05, 0.85, len(spans)))
    for (name, (l0, l1)), color in zip(spans.items(), colors):
        ax.add_patch(Rectangle((l0, 0.15), l1 - l0, 0.7, color=color, alpha=0.8))
        ax.text((l0 + l1) / 2.0, 0.5, name, ha="center", va="center",
                color="white", fontsize=9, fontweight="bold")
    ax.set_xlim(0.7, 5.05)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_title("SPHEREx: six detector arrays x 17 channels = 102 bands "
                 "(0.75-5.00 um)")
    ax.set_xticks([])

    for name in targets:
        meta = load_meta(name)
        if not meta.get("spectrum_fits"):
            continue
        lam, flux, _ = read_spectrum(meta["spectrum_fits"])
        ax2.plot(lam, flux / flux.max(), lw=1.6,
                 label=meta["meta"].get("common", name))
    for centre, label in ((1.72, "CH$_4$"), (2.32, "CH$_4$"),
                          (1.50, "H$_2$O"), (2.00, "H$_2$O"), (3.00, "H$_2$O")):
        ax2.axvline(centre, color="#cbd5e1", lw=0.8, ls=":", zorder=0)
        ax2.text(centre, 1.03, label, ha="center", fontsize=7, color="#64748b")
    ax2.set_xlabel("Wavelength (um)")
    ax2.set_ylabel("Flux (normalized)")
    ax2.set_xlim(0.7, 5.05)
    ax2.grid(alpha=0.25)
    ax2.legend(frameon=False, fontsize=8, ncol=2)
    ax2.set_title("Demo spectra (synthetic, 102 channels each)")

    path = os.path.join(outdir, "fig01_band_coverage.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_detections(res, outdir):
    """Dipoles (movers) and monopoles (variables) marked on the S map."""
    fig, ax = plt.subplots(figsize=(7.2, 6.4))
    im = ax.imshow(res["S"], origin="lower", cmap="magma", vmin=-5, vmax=12,
                   extent=extent(ax), interpolation="nearest")
    ax.set_title("ZOGY significance map with detections")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02, label="sigma")

    n_dipole = n_monopole = 0
    for det in res["detections"]:
        py, px = det["position"]
        x = (px - NPIX / 2.0) * PIXSCALE
        y = (py - NPIX / 2.0) * PIXSCALE
        if det["kind"] == "dipole":
            n_dipole += 1
            my, mx = det["pos_minus"]
            xm = (mx - NPIX / 2.0) * PIXSCALE
            ym = (my - NPIX / 2.0) * PIXSCALE
            ax.plot([xm, x], [ym, y], color=GREEN, lw=1.2, alpha=0.9, zorder=4)
            ax.plot(x, y, "o", mfc="none", mec=GREEN, ms=11, mew=1.6, zorder=5)
            ax.plot(xm, ym, "x", color=GREEN, ms=7, mew=1.6, zorder=5)
        else:
            n_monopole += 1
            ax.plot(x, y, "s", mfc="none", mec=AMBER, ms=11, mew=1.6, zorder=5)

        # Keep the SNR tag inside the axes when the peak sits near an edge.
        dx = -12 if x > 0.35 * NPIX * PIXSCALE else 9
        ax.annotate(f"{det['snr']:.1f}σ", (x, y), textcoords="offset points",
                    xytext=(dx, 5), ha="right" if dx < 0 else "left",
                    fontsize=6.5, color="white",
                    bbox=dict(boxstyle="round,pad=0.12", fc="#0f172a", alpha=0.55,
                              lw=0), zorder=6)

    handles = [
        plt.Line2D([], [], color=GREEN, marker="o", mfc="none", ls="",
                   label=f"dipole / mover (+, -): {n_dipole}"),
        plt.Line2D([], [], color=GREEN, marker="x", ls="",
                   label="negative lobe (previous position)"),
        plt.Line2D([], [], color=AMBER, marker="s", mfc="none", ls="",
                   label=f"monopole / variable: {n_monopole}"),
    ]
    ax.legend(handles=handles, frameon=False, fontsize=7.5, loc="upper center",
              bbox_to_anchor=(0.5, -0.11), ncol=3)
    ax.set_title(f"{res['common']}: {len(res['detections'])} detections at "
                 f">5σ (3\"/pix, dt = {DT_YEARS} yr)")
    path = os.path.join(outdir, "fig03_detections.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_spectrum(res, outdir):
    """Spectrum with the three classifier windows drawn on it."""
    spec = res.get("spectrum")
    if spec is None:
        return None
    lam, flux = spec["wavelengths"], spec["flux"]
    cls = spec["classification"]

    fig, ax = plt.subplots(figsize=(8.6, 5.0))
    ax.plot(lam, flux, color="#0f172a", lw=1.5, zorder=3)
    if "error" in spec:
        ax.fill_between(lam, flux - spec["error"], flux + spec["error"],
                        color=BLUE, alpha=0.25, lw=0)

    windows = [
        (1.56, 1.64, "CH$_4$ band", BLUE),
        (1.70, 1.78, "CH$_4$ ref", BLUE),
        (2.05, 2.15, "CH$_4$ band", BLUE),
        (2.28, 2.38, "CH$_4$ ref", BLUE),
        (1.24, 1.34, "cont.", GREEN),
        (2.12, 2.22, "cont.", GREEN),
        (1.46, 1.54, "H$_2$O", RED),
        (1.96, 2.04, "H$_2$O", RED),
        (2.95, 3.15, "H$_2$O", RED),
        (1.00, 1.20, "NIR", AMBER),
        (4.40, 4.80, "NIR", AMBER),
    ]
    seen = set()
    for l0, l1, label, color in windows:
        ax.axvspan(l0, l1, color=color, alpha=0.14, lw=0)
        ax.axvline(l0, color=color, lw=0.6, alpha=0.5)
        ax.axvline(l1, color=color, lw=0.6, alpha=0.5)
        if label not in seen:
            seen.add(label)
            ax.plot([], [], color=color, alpha=0.5, lw=8, label=label)

    ax.set_xlabel("Wavelength (um)")
    ax.set_ylabel("Flux (MJy/sr)")
    ax.set_xlim(0.7, 5.05)
    ax.grid(alpha=0.2)
    ax.legend(frameon=False, fontsize=7.5, ncol=4, loc="upper center")

    text = (f"CH$_4$ = {cls['ch4']:.2f}\n"
            f"H$_2$O = {cls['h2o']:.2f}\n"
            f"NIR slope = {cls['nir_slope']:.2f}\n\n"
            f"verdict: {cls['label']}")
    ax.text(0.985, 0.33, text, transform=ax.transAxes, ha="right", va="top",
            fontsize=8.5, family="monospace",
            bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="#cbd5e1"))
    ax.set_title(f"{res['common']}: three-index spectral classification")
    path = os.path.join(outdir, "fig04_spectrum_classification.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_coadd_panels(res, outdir):
    """Epoch 1, epoch 2, ZOGY difference and significance map."""
    fig, axes = plt.subplots(2, 2, figsize=(9, 8.2))
    im0 = image_panel(axes[0, 0], res["img1"],
                      f"Epoch 1 co-add ({res['n_epoch1']} exposures)")
    fig.colorbar(im0, ax=axes[0, 0], fraction=0.046, pad=0.02,
                 label="MJy/sr")
    im1 = image_panel(axes[0, 1], res["img2"],
                      f"Epoch 2 co-add ({res['n_epoch2']} exposures)")
    fig.colorbar(im1, ax=axes[0, 1], fraction=0.046, pad=0.02,
                 label="MJy/sr")

    lim = np.nanpercentile(np.abs(res["D"]), 99.5) or 1e-6
    im2 = axes[1, 0].imshow(res["D"], origin="lower", cmap="RdBu_r",
                            vmin=-lim, vmax=lim, extent=extent(axes[1, 0]))
    axes[1, 0].set_title("ZOGY difference image $D$")
    fig.colorbar(im2, ax=axes[1, 0], fraction=0.046, pad=0.02)

    im3 = image_panel(axes[1, 1], res["S"], "ZOGY significance $S$ (sigma)",
                      cmap="magma")
    fig.colorbar(im3, ax=axes[1, 1], fraction=0.046, pad=0.02, label="sigma")

    fig.suptitle(f"{res['common']}: {res['n_epoch1']}+{res['n_epoch2']} "
                 f"exposures, sigma1={res['sigma1']:.2f}, "
                 f"sigma2={res['sigma2']:.2f}", fontsize=11)
    fig.tight_layout()
    path = os.path.join(outdir, "fig02_coadd_panels.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_recovery(res, outdir, n_trials=10):
    """Injection-recovery curves and the underlying SNR x mu grid."""
    results = run_injection_recovery(
        res["img1"], res["img2"], res["sigma1"], res["sigma2"], 1.4,
        snr_grid=[3, 5, 7, 10, 15, 20], mu_grid=[1, 2, 5, 10],
        n_trials=n_trials,
    )

    snrs = sorted({k[0] for k in results})
    mus = sorted({k[1] for k in results})

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.2),
                                  gridspec_kw={"width_ratios": [1.15, 1]})

    for mu, color in zip(mus, plt.cm.viridis(np.linspace(0, 0.85, len(mus)))):
        ys = [results[(s, mu)] * 100.0 for s in snrs]
        ax.plot(snrs, ys, "o-", color=color, lw=1.6, ms=4,
                label=f"mu = {mu}\"/yr")
    ax.axhline(50, color="#94a3b8", ls="--", lw=0.8)
    ax.set_xlabel("Injected SNR")
    ax.set_ylabel("Recovered at >5 sigma (%)")
    ax.set_xticks(snrs, [str(s) for s in snrs])
    ax.set_ylim(-3, 103)
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, fontsize=7.5, title="proper motion",
              title_fontsize=7.5)
    ax.set_title(f"{res['common']}: injection-recovery "
                 f"({n_trials} trials per cell)")

    grid = np.array([[results[(s, mu)] * 100.0 for s in snrs] for mu in mus])
    im = ax2.imshow(grid, origin="lower", cmap="viridis", vmin=0, vmax=100,
                    aspect="auto")
    ax2.set_xticks(range(len(snrs)), [str(s) for s in snrs])
    ax2.set_yticks(range(len(mus)), [str(m) for m in mus])
    ax2.set_xlabel("Injected SNR")
    ax2.set_ylabel("Proper motion (\"/yr)")
    ax2.set_title("Recovery fraction (%)")
    for i in range(len(mus)):
        for j in range(len(snrs)):
            ax2.text(j, i, f"{grid[i, j]:.0f}", ha="center", va="center",
                     fontsize=7,
                     color="white" if grid[i, j] < 60 else "#0f172a")
    fig.colorbar(im, ax=ax2, fraction=0.046, pad=0.02)

    fig.tight_layout()
    path = os.path.join(outdir, "fig05_recovery.png")
    fig.savefig(path)
    plt.close(fig)
    return path, results


def fig_flags(res, outdir):
    """FLAGS bit coverage for one exposure and the masked fraction."""
    img, flags, var, wcs = load_exposure(res["paths"][0])

    bits = [(BIT_DEAD, "dead"), (BIT_SATURATED, "saturated"),
            (BIT_COSMIC_RAY, "cosmic ray"), (BIT_HOT_PIXEL, "hot pixel"),
            (BIT_STRAY_LIGHT, "stray light")]
    total = flags.size
    labels, fracs = [], []
    for bit, name in bits:
        labels.append(name)
        fracs.append(100.0 * np.count_nonzero(flags & bit) / total)

    clean = np.count_nonzero((flags & BAD_BITS) == 0) / total * 100.0

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.4))

    categorical = np.zeros(flags.shape)
    for idx, (bit, _) in enumerate(bits, start=1):
        categorical[(flags & bit) != 0] = idx
    cmap = matplotlib.colors.ListedColormap(
        ["#e2e8f0", "#0ea5e9", "#dc2626", "#f59e0b", "#8b5cf6", "#16a34a"])
    ax.imshow(categorical, origin="lower", cmap=cmap, vmin=0, vmax=5,
              interpolation="nearest")
    ax.set_title("FLAGS bit mask (one exposure)")
    ax.set_xlabel("pixel x")
    ax.set_ylabel("pixel y")
    handles = [plt.Line2D([], [], marker="s", ls="", ms=8, color=cmap(i),
                          label=name)
               for i, (_, name) in enumerate(bits, start=1)]
    handles.append(plt.Line2D([], [], marker="s", ls="", ms=8,
                              color=cmap(0), label="clean"))
    ax.legend(handles=handles, frameon=False, fontsize=7, loc="upper center",
              bbox_to_anchor=(0.5, -0.12), ncol=3)

    bars = ax2.bar(labels, fracs, color=[BLUE, RED, AMBER, "#8b5cf6", GREEN])
    ax2.set_ylabel("Pixels flagged (%)")
    ax2.set_title(f"Contamination: {clean:.1f}% of pixels survive the mask")
    ax2.tick_params(axis="x", rotation=20, labelsize=7.5)
    for bar, frac in zip(bars, fracs):
        ax2.annotate(f"{frac:.2f}%", (bar.get_x() + bar.get_width() / 2,
                                      bar.get_height()),
                     ha="center", va="bottom", fontsize=7)
    ax2.set_ylim(0, max(max(fracs) * 1.35, 0.5))
    ax2.grid(axis="y", alpha=0.25)

    fig.tight_layout()
    path = os.path.join(outdir, "fig06_flags_masking.png")
    fig.savefig(path)
    plt.close(fig)
    return path, clean


def fig_targets_summary(results, outdir):
    """Known vs candidate detections and max SNR for every target."""
    names = [r["common"] for r in results]
    known = [sum(1 for d in r["detections"] if d["verdict"] == "KNOWN")
             for r in results]
    cand = [sum(1 for d in r["detections"] if d["verdict"] == "CANDIDATE")
            for r in results]
    max_snr = [max([d["snr"] for d in r["detections"]] or [0]) for r in results]
    dipoles = [sum(1 for d in r["detections"] if d["kind"] == "dipole")
               for r in results]

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    x = np.arange(len(names))
    ax.bar(x, known, color=GREEN, label="KNOWN (catalog match)")
    ax.bar(x, cand, bottom=known, color=RED, label="CANDIDATE (follow-up)")
    ax.set_xticks(x, names, rotation=18, ha="right", fontsize=8)
    ax.set_ylabel("Detections at >5 sigma")
    ax.set_title("Triage verdicts per target")
    ax.legend(frameon=False, fontsize=7.5)
    ax.grid(axis="y", alpha=0.25)
    for xi, (k, c) in enumerate(zip(known, cand)):
        ax.annotate(f"{k + c}", (xi, k + c), ha="center", va="bottom",
                    fontsize=7.5)

    ax2.bar(x, max_snr, color=BLUE, label="max SNR (left axis)")
    ax2.set_ylabel("Max significance (sigma)")
    ax2.set_title("Detection significance and mover count")
    ax2.grid(axis="y", alpha=0.25)

    ax3 = ax2.twinx()
    ax3.plot(x, dipoles, "o-", color=AMBER, ms=6, lw=1.2,
             label="dipoles / movers (right axis)")
    ax3.set_ylabel("Dipole count")
    ax3.set_ylim(0, max(4, max(dipoles) + 1))
    ax2.set_xticks(x, names, rotation=18, ha="right", fontsize=8)
    handles = (ax2.get_legend_handles_labels()[0]
               + ax3.get_legend_handles_labels()[0])
    labels = (ax2.get_legend_handles_labels()[1]
              + ax3.get_legend_handles_labels()[1])
    ax2.legend(handles, labels, frameon=False, fontsize=7.5, loc="lower right")

    fig.tight_layout()
    path = os.path.join(outdir, "fig07_targets_summary.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def _aperture_series(res, det):
    """Aperture flux of one detection in every exposure."""
    return np.array([extract_spectrum_from_cutout(p, det["ra"], det["dec"])["flux"]
                     for p in res["paths"]], dtype=float)


def fig_photometry(res, outdir):
    """
    Per-exposure photometry of the strongest mover and the strongest variable.

    The aperture is placed at the *epoch-2* position reported by triage, so a
    real mover shows a flux step between the epochs while a static variable
    keeps a constant position and changes only in brightness.
    """
    dipoles = sorted([d for d in res["detections"] if d["kind"] == "dipole"],
                     key=lambda d: -d["snr"])[:4]
    monopoles = sorted([d for d in res["detections"] if d["kind"] == "monopole"],
                       key=lambda d: -d["snr"])[:4]
    if not dipoles and not monopoles:
        return None

    half = res["n_epoch1"]
    best_dipole = best_monopole = None
    best_jump = -1.0
    best_ratio = 0.0
    for det in dipoles:
        series = _aperture_series(res, det)
        jump = abs(np.mean(series[half:]) - np.mean(series[:half]))
        if jump > best_jump:
            best_jump, best_dipole = jump, (det, series)
    for det in monopoles:
        series = _aperture_series(res, det)
        jump = abs(np.mean(series[half:]) - np.mean(series[:half]))
        if jump > best_ratio:
            best_ratio, best_monopole = jump, (det, series)

    fig, ax = plt.subplots(figsize=(7.8, 4.6))
    idx = np.arange(1, len(res["paths"]) + 1)
    ax.axvspan(0.5, half + 0.5, color=BLUE, alpha=0.08, lw=0)
    ax.axvspan(half + 0.5, len(idx) + 0.5, color=AMBER, alpha=0.08, lw=0)

    series_list = []
    if best_dipole:
        det, series = best_dipole
        label = (f"mover (dipole): SNR {det['snr']:.1f}, {det['verdict']}, "
                 f"mu ~ {det.get('proper_motion_arcsec_yr', float('nan')):.1f}\"/yr")
        series_list.append((label, series, GREEN, "o"))
    if best_monopole:
        det, series = best_monopole
        e1, e2 = np.mean(series[:half]), np.mean(series[half:])
        label = (f"variable (monopole): SNR {det['snr']:.1f}, {det['verdict']}, "
                 f"flux x{e2 / e1:.2f}")
        series_list.append((label, series, RED, "s"))

    for label, series, color, marker in series_list:
        ax.plot(idx, series, marker=marker, color=color, lw=1.6, ms=6,
                label=label)

    ax.set_xticks(idx, [f"exp {i}" for i in idx])
    ax.set_ylabel("Aperture flux (MJy/sr)")
    ax.set_xlabel("Exposure (epoch 1 | epoch 2)")
    ax.set_ylim(bottom=min(0.0, float(min(s.min() for _, s, _, _ in series_list))
                           * 1.1))
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, fontsize=7.5, loc="upper center",
              bbox_to_anchor=(0.5, -0.16))
    ax.set_title(f"{res['common']}: aperture photometry at the epoch-2 "
                 f"position\n(3 px aperture = 4.5\" on the cutout grid)")
    path = os.path.join(outdir, "fig08_photometry.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def write_summary(results, extras, outdir):
    """Machine-readable summary of everything the figures show."""
    rows = []
    for res in results:
        cls = res.get("spectrum", {}).get("classification", {})
        rows.append({
            "target": res["target"],
            "common": res["common"],
            "ra": res["ra"],
            "dec": res["dec"],
            "n_epoch1": res["n_epoch1"],
            "n_epoch2": res["n_epoch2"],
            "sigma1": round(res["sigma1"], 4),
            "sigma2": round(res["sigma2"], 4),
            "detections": len(res["detections"]),
            "known": sum(1 for d in res["detections"] if d["verdict"] == "KNOWN"),
            "candidates": sum(1 for d in res["detections"]
                              if d["verdict"] == "CANDIDATE"),
            "dipoles": sum(1 for d in res["detections"] if d["kind"] == "dipole"),
            "monopoles": sum(1 for d in res["detections"]
                             if d["kind"] == "monopole"),
            "max_snr": round(max([d["snr"] for d in res["detections"]] or [0]), 2),
            "ch4": cls.get("ch4"),
            "h2o": cls.get("h2o"),
            "nir_slope": cls.get("nir_slope"),
            "spectral_label": cls.get("label"),
        })

    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump({"targets": rows, "extras": extras}, f, indent=2)

    with open(os.path.join(outdir, "summary.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Render the SPHEREx pipeline figures from cached data.")
    parser.add_argument("--outdir", default="figures")
    parser.add_argument("--detail-target", default="WISE_0855",
                        help="target used for the per-exposure detail panels")
    parser.add_argument("--trials", type=int, default=25,
                        help="injection-recovery trials per grid cell")
    parser.add_argument("--targets", nargs="*", default=None)
    args = parser.parse_args()

    targets = args.targets or available_targets()
    if not targets:
        raise SystemExit("No cached targets: run step1_ingest.py or "
                         "make_demo_data.py first.")

    os.makedirs(args.outdir, exist_ok=True)
    print(f"Analysing {len(targets)} target(s) from {CACHE}/ ...")
    results = []
    for name in targets:
        print(f" {name}")
        results.append(analyse(name))

    detail = next((r for r in results if r["target"] == args.detail_target),
                  results[0])

    print("Rendering figures ...")
    made = [fig_band_coverage(targets, args.outdir)]
    made.append(fig_coadd_panels(detail, args.outdir))
    made.append(fig_detections(detail, args.outdir))
    made.append(fig_spectrum(detail, args.outdir))
    recovery_path, recovery = fig_recovery(detail, args.outdir, args.trials)
    made.append(recovery_path)
    flags_path, clean_fraction = fig_flags(detail, args.outdir)
    made.append(flags_path)
    made.append(fig_targets_summary(results, args.outdir))
    made.append(fig_photometry(detail, args.outdir))

    extras = {
        "detail_target": detail["target"],
        "clean_pixel_fraction": round(float(clean_fraction), 3),
        "recovery_grid": [{"snr": s, "mu": m, "recovery": float(v)}
                          for (s, m), v in sorted(recovery.items())],
    }
    rows = write_summary(results, extras, args.outdir)

    for path in [p for p in made if p]:
        print(f"  {path}")
    print(f"  {os.path.join(args.outdir, 'summary.json')}")
    print(f"  {os.path.join(args.outdir, 'summary.csv')}")
    print("\nPer-target summary:")
    for row in rows:
        print(f"  {row['common']:<18} detections={row['detections']:>2} "
              f"(known={row['known']}, candidates={row['candidates']}) "
              f"max_snr={row['max_snr']:>5} label='{row['spectral_label']}'")


if __name__ == "__main__":
    main()
