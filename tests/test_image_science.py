"""
Image-science tests (v2 phases 1.2, 1.3, 1.4, 3.2, 3.3, 3.6) on the two real
DR1 cutouts in tests/fixtures (WISE 0855, detector D1, 2025 W19 / 2026 W19).
"""
import io
import os

import numpy as np
import pytest
from astropy.io import fits
from scipy.ndimage import maximum_filter, minimum_filter

import config
from step2_coadd import (coadd_matched, load_exposure, load_exposure_full,
                         make_target_wcs, prepare_exposure)
from step3_zogy import find_detections, zogy
from step4_triage import triage
from spherexlens.imaging import (dedup_detections, detector_offset,
                                 footprint_edge_map, fwhm_from_array,
                                 gaussian_kernel, score_exposures, spike_hit)

HERE = os.path.dirname(__file__)
FIX_A = os.path.join(HERE, "fixtures",
                     "level2_2025W19_1B_0241_1D1_spx_l2b-v27-2026-197_cut.fits.gz")
FIX_B = os.path.join(HERE, "fixtures",
                     "level2_2026W19_1A_0227_1D1_spx_l2b-v27-2026-245_cut.fits.gz")
WISE0855 = (133.7896, -7.2441)


def _bytes(path, mutate=None, drop=()):
    """The fixture as in-memory FITS bytes, optionally modified."""
    with fits.open(path) as hdul:
        hdus = [h.copy() for h in hdul if h.name not in drop]
    out = fits.HDUList(hdus)
    if mutate:
        mutate(out)
    buf = io.BytesIO()
    out.writeto(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1.3 PSF
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [FIX_A, FIX_B])
def test_psf_from_epsf_is_normalised_centred_and_matches_header(path):
    ex = load_exposure_full(path, target_radec=WISE0855)
    psf = ex["psf"]
    assert ex["psf_source"] == "epsf"
    assert psf.ndim == 2 and psf.shape[0] == psf.shape[1] and psf.shape[0] % 2 == 1
    assert abs(psf.sum() - 1.0) < 1e-9
    c = psf.shape[0] // 2
    assert np.unravel_index(np.argmax(psf), psf.shape) == (c, c)
    hdr_fwhm = ex["header_psf_fwhm_arcsec"]
    assert hdr_fwhm and abs(ex["psf_fwhm_arcsec"] - hdr_fwhm) / hdr_fwhm < 0.15
    # the co-add kernel is broader (bilinear reprojection blur) but sane
    assert hdr_fwhm < ex["psf_grid_fwhm_arcsec"] < 1.5 * hdr_fwhm
    assert ex["mjd"] > 60000 and ex["detector"] == 1 and ex["obsid"]
    assert ex["zodi"] is not None


def test_cutout_to_detector_mapping_picks_the_containing_zone():
    with fits.open(FIX_A) as hdul:
        hdr = hdul["IMAGE"].header
        tab = hdul["EPSF"].data
    dx, dy = detector_offset(hdr)
    # IRSA tutorial: x_det = 1 + x_cut - CRPIX1A; sky CRPIX1 is the detector centre
    assert (dx, dy) == (1 - hdr["CRPIX1A"], 1 - hdr["CRPIX2A"])
    assert abs((hdr["CRPIX1"] - 1 + dx) - 1019.5) < 1e-6
    ex = load_exposure_full(FIX_A)
    zone = ex["psf_zone"]
    half = tab["XWIDTH"][zone["row"]] / 2.0
    assert abs(zone["x_det"] - tab["XCENTER"][zone["row"]]) <= half + 1e-3
    assert abs(zone["y_det"] - tab["YCENTER"][zone["row"]]) <= half + 1e-3


def test_missing_epsf_falls_back_to_gaussian():
    ex = load_exposure_full(_bytes(FIX_A, drop=("EPSF",)))
    assert ex["psf_source"] == "gaussian_fallback"
    assert abs(ex["psf"].sum() - 1.0) < 1e-9
    want = 2.3548 * config.PSF_SIGMA_PX_FALLBACK * config.PIXSCALE_ARCSEC
    assert abs(ex["psf_fwhm_arcsec"] - want) < 0.01
    # the old 4-tuple reader is untouched
    img, flags, var, wcs = load_exposure(FIX_A)
    assert img.shape == flags.shape == var.shape


def test_coadd_return_psf_and_backward_compatible_shape():
    wcs = make_target_wcs(*WISE0855)
    groups = {1: [(FIX_A, "D1")], 2: [(FIX_B, "D1")]}
    plain = coadd_matched(groups, wcs)
    assert len(plain) == 4
    stacks, noise, excl, nb, extra = coadd_matched(groups, wcs, return_psf=True)
    assert set(extra["psfs"]) == {1, 2}
    for e in (1, 2):
        assert abs(extra["psfs"][e].sum() - 1.0) < 1e-9
        assert extra["psf_info"][e]["source"] == "epsf"
        np.testing.assert_allclose(stacks[e], plain[0][e])
    assert [q["kept"] for q in extra["exposure_quality"]] == [True, True]
    assert all(q["zodi_subtracted"] for q in extra["exposure_quality"])


def test_zogy_accepts_psf_arrays_and_matches_gaussian_path():
    rng = np.random.default_rng(1)
    a, b = rng.normal(0, 1, (96, 96)), rng.normal(0, 1, (96, 96))
    _, s_sigma = zogy(a, b, 1.0, 1.0, psf_sigma1=1.4, psf_sigma2=1.4)
    k = gaussian_kernel(31, 1.4)
    _, s_arr = zogy(a, b, 1.0, 1.0, psf1=k, psf2=k)
    assert np.max(np.abs(s_sigma - s_arr)) < 1e-6


# ---------------------------------------------------------------------------
# 1.4 ZODI
# ---------------------------------------------------------------------------
def test_zodi_subtraction_changes_the_background():
    ex = load_exposure_full(FIX_A)
    raw, valid, applied0 = prepare_exposure(ex, subtract_sky=False, subtract_zodi=False)
    sub, _, applied1 = prepare_exposure(ex, subtract_sky=False, subtract_zodi=True)
    assert not applied0 and applied1
    zmed = float(np.nanmedian(ex["zodi"][valid]))
    assert zmed > 0.05                              # ~0.2 MJy/sr at 1 um
    shift = float(np.median(raw[valid]) - np.median(sub[valid]))
    assert abs(shift - zmed) < 0.2 * zmed
    # no ZODI layer -> no-op
    ex2 = load_exposure_full(_bytes(FIX_A, drop=("ZODI",)))
    assert ex2["zodi"] is None
    same, _, applied2 = prepare_exposure(ex2, subtract_sky=False, subtract_zodi=True)
    assert not applied2 and np.array_equal(np.nan_to_num(same), np.nan_to_num(ex2["img"]))


# ---------------------------------------------------------------------------
# 1.2 fading sources / KD-tree pairing
# ---------------------------------------------------------------------------
def test_fading_source_is_reported_as_monopole_negative():
    rng = np.random.default_rng(3)
    n = 128
    y, x = np.mgrid[0:n, 0:n]
    star = 40 * np.exp(-((x - 70) ** 2 + (y - 50) ** 2) / (2 * 1.4 ** 2))
    img1 = rng.normal(0, 1, (n, n)) + star      # there in epoch 1
    img2 = rng.normal(0, 1, (n, n))             # gone in epoch 2
    k = gaussian_kernel(15, 1.4)
    _, S = zogy(img1, img2, 1.0, 1.0, psf1=k, psf2=k)
    dets = find_detections(S, 3.0, 1.0, sigma_thresh=5.0)
    fading = [d for d in dets if d["kind"] == "monopole_negative"]
    assert len(fading) == 1
    d = fading[0]
    assert d["sign"] == -1 and d["snr"] > 5 and d["pos"] == (50, 70)
    assert not any(d["kind"] == "monopole" for d in dets)
    # opt-out keeps the old output
    assert find_detections(S, 3.0, 1.0, report_fading=False) == []


def _old_find_detections(S, pix_scale_arcsec, dt_years, sigma_thresh=5.0,
                         min_sep_px=0.5, max_sep_px=25.0):
    """The pre-v2 all-pairs loop, verbatim, as an oracle."""
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
            fc = abs(pv[i] + tv[best_j]) / (pv[i] - tv[best_j] + 1e-9)
            detections.append({"kind": "dipole", "pos_plus": (int(py[i]), int(px[i])),
                               "pos_minus": (int(ty[best_j]), int(tx[best_j])),
                               "snr": float(pv[i]), "proper_motion_arcsec_yr": float(mu),
                               "flux_consistency": float(fc)})
        else:
            detections.append({"kind": "monopole", "pos": (int(py[i]), int(px[i])),
                               "snr": float(pv[i])})
    return detections


@pytest.mark.parametrize("seed", [0, 7, 42])
def test_kdtree_pairing_equals_old_algorithm(seed):
    rng = np.random.default_rng(seed)
    S = rng.normal(0, 1.7, (180, 180))          # many >2.5 sigma extrema
    old = _old_find_detections(S, 3.0, 0.5, sigma_thresh=2.5, max_sep_px=12.0)
    new = find_detections(S, 3.0, 0.5, sigma_thresh=2.5, max_sep_px=12.0)
    assert len(old) > 50 and any(d["kind"] == "dipole" for d in old)
    pos = [{k: v for k, v in d.items() if k != "sign"}
           for d in new if d["kind"] != "monopole_negative"]
    assert pos == old
    # every unpaired trough is reported once as fading
    n_troughs = int(((S == minimum_filter(S, size=5)) & (S < -2.5)).sum())
    n_pairs = sum(d["kind"] == "dipole" for d in old)
    assert sum(d["kind"] == "monopole_negative" for d in new) == n_troughs - n_pairs


# ---------------------------------------------------------------------------
# 3.2 exposure quality
# ---------------------------------------------------------------------------
def test_quality_score_drops_a_corrupted_exposure():
    def corrupt(hdul):
        rng = np.random.default_rng(5)
        var = hdul["VARIANCE"].data.astype(float)
        noise = 10 * np.sqrt(np.clip(var, 0, None)) * rng.normal(size=var.shape)
        hdul["IMAGE"].data = (hdul["IMAGE"].data + noise).astype(np.float32)

    def retag(obsid):
        def f(hdul):
            hdul["IMAGE"].header["OBSID"] = obsid
        return f

    good1, good2 = _bytes(FIX_A, mutate=retag("good1")), _bytes(FIX_A, mutate=retag("good2"))
    bad = _bytes(FIX_A, mutate=lambda h: (corrupt(h), retag("bad")(h)))
    wcs = make_target_wcs(*WISE0855)
    groups = {1: [(good1, "D1"), (good2, "D1"), (bad, "D1")], 2: [(FIX_B, "D1")]}
    *_, extra = coadd_matched(groups, wcs, return_psf=True)
    q = {r["obs_id"]: r for r in extra["exposure_quality"]}
    assert q["badD1"]["kept"] is False
    assert q["badD1"]["score"] < config.EXPOSURE_QUALITY_MIN
    assert any(r.startswith("dropped") for r in q["badD1"]["reasons"])
    assert q["good1D1"]["kept"] and q["good2D1"]["kept"]
    assert q["good1D1"]["score"] > 0.9
    assert extra["psf_info"][1]["n_exposures"] == 2


def test_quality_never_empties_an_epoch():
    rows = [{"epoch": 1, "band": "D1", "psf_fwhm_arcsec": 7.0,
             "metrics": {"flag_frac": 0.9, "noise_ratio": 1.1}},
            {"epoch": 2, "band": "D1", "psf_fwhm_arcsec": 7.0,
             "metrics": {"flag_frac": 0.02, "noise_ratio": 1.1}}]
    score_exposures(rows)
    assert rows[0]["score"] < config.EXPOSURE_QUALITY_MIN and rows[0]["kept"]
    assert "otherwise empty epoch" in rows[0]["reasons"][-1]
    assert rows[1]["kept"] and rows[1]["score"] == 1.0


# ---------------------------------------------------------------------------
# 3.3 masks
# ---------------------------------------------------------------------------
def test_edge_map_marks_footprint_edges_only():
    fp = np.zeros((60, 60), dtype=bool)
    fp[:, :30] = True
    edge = footprint_edge_map([fp, np.ones((60, 60), dtype=bool)], n_px=3)
    assert edge[30, 29] == 1 and edge[30, 31] == 1
    assert edge[30, 10] == 0 and edge[30, 50] == 0
    assert edge[0, 0] == 0                      # the grid border is not an edge


def test_spike_hit_cross_along_detector_axes():
    star = {"x": 50.0, "y": 50.0, "mask_arcsec": 30.0, "mag": 5.0, "cat": "gaia",
            "ra": 1.0, "dec": 2.0}
    on_arm = [(50, 70)]                         # 20 px along +x, beyond the 10 px core
    off_arm = [(64, 64)]                        # diagonal
    core = [(50, 55)]
    assert spike_hit(on_arm, [star], [0.0, 90.0])["angle_deg"] == 0.0
    assert spike_hit(off_arm, [star], [0.0, 90.0]) is None
    assert spike_hit(core, [star], [0.0, 90.0]) is None
    assert spike_hit(off_arm, [star], [45.0]) is not None


def _triage(det, **ctx):
    return triage(det, 10.0, 10.0, 180, 3.0, {"gaia": [], "wise": [], "vsx": []},
                  context=ctx)


def test_triage_fading_edge_and_spike_paths():
    fade = _triage({"kind": "monopole_negative", "pos": (90, 90), "snr": 9.0, "sign": -1})
    assert fade["category"] == "CANDIDATE"
    assert any("got fainter" in r for r in fade["reasons"])
    edge = _triage({"kind": "monopole", "pos": (90, 90), "snr": 9.0},
                   edge={"n_exposures": 1, "edge_px": 4})
    assert edge["category"] == "MASK_ARTEFACT" and edge["confidence"] == "low"
    spike = {"cat": "gaia", "mag": 5.0, "ra": 10.01, "dec": 10.01, "sep_arcsec": 60.0,
             "angle_deg": 18.4, "length_arcsec": 200.0}
    sp = _triage({"kind": "monopole", "pos": (90, 90), "snr": 9.0}, spike=spike)
    assert sp["category"] == "BRIGHT_STAR_ARTEFACT"
    assert "detector-axis cross" in sp["reasons"][0]


# ---------------------------------------------------------------------------
# 3.6 de-duplication
# ---------------------------------------------------------------------------
def test_dedup_merges_across_pairs_and_counts_pairs():
    raw = [
        {"kind": "monopole", "pos": (50, 50), "snr": 8.0, "pair": "E1→E2"},
        {"kind": "monopole", "pos": (51, 49), "snr": 12.0, "pair": "E2→E3"},
        {"kind": "dipole", "pos_plus": (52, 51), "pos_minus": (55, 51), "snr": 6.0,
         "pair": "E1→E3"},
        {"kind": "monopole", "pos": (50, 51), "snr": 7.0, "pair": "E1→E2"},
        {"kind": "monopole", "pos": (90, 90), "snr": 6.0, "pair": "E1→E2"},
    ]
    kept = dedup_detections(raw, radius_px=2)
    assert [d["snr"] for d in kept] == [12.0, 6.0]
    assert kept[0]["n_pairs_seen"] == 3
    assert set(kept[0]["pairs_seen"]) == {"E1→E2", "E2→E3", "E1→E3"}
    assert kept[1]["n_pairs_seen"] == 1


def test_dedup_equals_old_loop_on_random_detections():
    rng = np.random.default_rng(11)
    raw = [{"kind": "monopole", "pos": (int(y), int(x)), "snr": float(s),
            "pair": f"P{p}"}
           for y, x, s, p in zip(rng.integers(0, 40, 300), rng.integers(0, 40, 300),
                                 rng.uniform(5, 50, 300), rng.integers(0, 3, 300))]
    old = []
    for d in sorted(raw, key=lambda d: -d["snr"]):
        pos = d["pos"]
        if any(abs(pos[0] - m["pos"][0]) <= 2 and abs(pos[1] - m["pos"][1]) <= 2
               for m in old):
            continue
        old.append(d)
    new = dedup_detections([dict(d) for d in raw], radius_px=2)
    assert [(d["pos"], d["snr"]) for d in new] == [(d["pos"], d["snr"]) for d in old]
    assert all(1 <= d["n_pairs_seen"] <= 3 for d in new)


def test_fwhm_helper_on_gaussian():
    k = gaussian_kernel(21, 2.0)
    assert abs(fwhm_from_array(k, 3.0) - 2.3548 * 2.0 * 3.0) / (2.3548 * 6.0) < 0.03
