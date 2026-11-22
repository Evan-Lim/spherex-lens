"""Phase 4: null test, strict injection, threshold, answer key, metrics, routes, gate."""
import json
import os

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import gaussian_filter

import config
from spherexlens.eval import answer_key, gate, harness, injection, null_test, threshold

RA, DEC = 150.0, 2.0


def _write_cutout(path, rng, npix=48, scale_arcsec=6.2, sigma=1.0):
    w = WCS(naxis=2)
    w.wcs.crpix = [npix / 2.0, npix / 2.0]
    w.wcs.cdelt = [-scale_arcsec / 3600.0, scale_arcsec / 3600.0]
    w.wcs.crval = [RA, DEC]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    hdr = w.to_header()
    img = rng.normal(0, sigma, (npix, npix)).astype("f4")
    hdul = fits.HDUList([fits.PrimaryHDU(),
                         fits.ImageHDU(img, hdr, name="IMAGE"),
                         fits.ImageHDU(np.zeros((npix, npix), "i4"), hdr, name="FLAGS"),
                         fits.ImageHDU(np.full((npix, npix), sigma ** 2, "f4"), hdr,
                                       name="VARIANCE")])
    hdul.writeto(path, overwrite=True)


@pytest.fixture
def noise_cache(tmp_path):
    """A pure-noise field: epoch 1 has 4 D1 exposures, epoch 2 has 2."""
    rng = np.random.default_rng(3)
    paths, sources = [], []
    for i, epoch in enumerate([1, 1, 1, 1, 2, 2]):
        p = str(tmp_path / f"level2_x{i}_1D1_spx_cut.fits")
        _write_cutout(p, rng)
        paths.append(p)
        sources.append({"path": p, "epoch": epoch, "band": "SPHEREx-D1",
                        "mjd": 60000.0 + (0 if epoch == 1 else 365) + i, "obs_id": f"o{i}"})
    meta = {"meta": {"ra": RA, "dec": DEC}, "paths": paths,
            "epochs": [s["epoch"] for s in sources], "sources": sources, "catalogs": {}}
    with open(tmp_path / "Noise_meta.json", "w") as f:
        json.dump(meta, f)
    return str(tmp_path)


def test_null_test_on_pure_noise_gives_few_false_alarms(noise_cache):
    field = harness.load_field(noise_cache, "Noise", npix=80)
    res = null_test.run_null_test(field, n_splits=2, sigma=5.0, seed=1)
    assert res["realisations"], "the 4-exposure group must be split"
    assert {(s["epoch"], s["n_exposures"]) for s in res["skipped"]} == set()
    total = sum(r["n_false"] for r in res["realisations"])
    assert total <= 2
    summary = null_test.summarise([res], n_boot=50)
    assert summary["n_realisations"] == len(res["realisations"])
    assert summary["false_alarms_per_deg2"] is not None


def test_null_test_skips_single_exposure_groups(noise_cache):
    field = harness.load_field(noise_cache, "Noise", npix=80)
    field["exposures"] = field["exposures"][:1] + field["exposures"][4:5]
    res = null_test.run_null_test(field, n_splits=1, sigma=5.0)
    assert res["realisations"] == []
    assert len(res["skipped"]) == 2


def _noise_pair(n=120, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 1.0, (n, n)), rng.normal(0, 1.0, (n, n))


def test_strict_injection_recovers_a_bright_mover():
    a, b = _noise_pair()
    res = injection.run_strict_injection(
        a, b, 1.0, 1.0, snr_grid=[60], mu_grid=[20], categories=("mover", "brightening"),
        n_trials=4, seed=2, dt_years=1.0, per_image=2, normalisation="pipeline")
    assert res["by_category"]["mover"]["recall"] >= 0.75
    assert res["by_category"]["brightening"]["recall"] >= 0.75
    cell = res["grid"][0]
    assert cell["n"] == 4 and 0 <= cell["ci95"][0] <= cell["recall"] <= cell["ci95"][1] <= 1


def test_strict_match_rejects_wrong_position_and_wrong_kind():
    det = {"kind": "dipole", "pos_plus": (50, 54), "pos_minus": (50, 40), "snr": 30.0}
    assert injection.match_detection([det], 50.0, 50.0, "dipole") is None      # 4 px away
    assert injection.match_detection([det], 54.0, 50.5, "dipole") is det        # x=54, y=50.5
    assert injection.match_detection([det], 54.0, 50.0, "monopole") is None    # wrong kind


def test_wilson_interval():
    lo, hi = injection.wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
    assert injection.wilson(0, 0) == [0.0, 1.0]


def test_step6_keeps_interface_and_uses_detection_sigma(monkeypatch):
    import step6_injection
    a, b = _noise_pair(100, 5)
    res = step6_injection.run_injection_recovery(a, b, 1.0, 1.0, 1.4, snr_grid=[40],
                                                 mu_grid=[20], n_trials=3, dt_years=1.0)
    assert set(res) == {(40, 20)} and res[(40, 20)] > 0
    monkeypatch.setattr(config, "DETECTION_SIGMA", 1e6)
    res = step6_injection.run_injection_recovery(a, b, 1.0, 1.0, 1.4, snr_grid=[40],
                                                 mu_grid=[20], n_trials=3, dt_years=1.0)
    assert res[(40, 20)] == 0.0


def test_threshold_hits_target_on_gaussian_null():
    rng = np.random.default_rng(11)
    valid = np.ones((100, 100), bool)

    def s_map():
        s = gaussian_filter(rng.normal(size=(100, 100)), 1.5, mode="wrap")
        return s / s.std()

    peaks = np.concatenate([null_test.null_extrema(s_map(), valid) for _ in range(40)])
    choice = threshold.choose_threshold(peaks, None, 40, target=0.5)
    assert choice is not None and 3.5 < choice["sigma"] < 5.5
    observed = np.mean([(null_test.null_extrema(s_map(), valid) > choice["sigma"]).sum()
                        for _ in range(300)])
    assert 0.2 < observed < 0.9


def test_bh_sigma_on_pure_noise_is_bonferroni_like():
    rng = np.random.default_rng(1)
    sig, k, m = threshold.bh_sigma(rng.normal(size=(100, 100)))
    assert m == 10000 and k <= 2 and 4.0 < sig < 5.5


def test_threshold_file_drives_config(tmp_path, monkeypatch):
    p = tmp_path / "threshold.json"
    p.write_text(json.dumps({"sigma": 5.35}))
    monkeypatch.delenv("SPHEREX_DETECTION_SIGMA", raising=False)
    assert config._load_eval_threshold(str(p)) == 5.35
    monkeypatch.setenv("SPHEREX_DETECTION_SIGMA", "4.5")
    assert config._load_eval_threshold(str(p)) is None
    assert config._load_eval_threshold(str(tmp_path / "missing.json")) is None


def test_answer_key_scores_a_detected_high_pm_star():
    wcs = harness.make_target_wcs(RA, DEC, npix=100)
    gaia = [{"ra": RA, "dec": DEC, "pm_ra": 0.0, "pm_de": 20000.0, "gmag": 10.0,
             "source_id": "x"}]
    mjd_a, mjd_b = 58000.0, 58365.25
    key = answer_key.build_answer_key({"gaia": gaia}, mjd_a, mjd_b, wcs, 100)
    assert len(key) == 1 and key[0]["class"] == "high_pm_star"
    o = key[0]
    hit = {"kind": "dipole", "snr": 9.0, "pos_plus": (round(o["y_b"]), round(o["x_b"])),
           "pos_minus": (round(o["y_a"]), round(o["x_a"]))}
    res = answer_key.score(key, [hit])
    assert res["recall"] == 1.0 and res["by_class"]["high_pm_star"] == 1.0
    key = answer_key.build_answer_key({"gaia": gaia}, mjd_a, mjd_b, wcs, 100)
    assert answer_key.score(key, [dict(hit, kind="monopole")])["recall"] == 0.0


def test_eval_run_end_to_end_writes_contract_metrics(noise_cache, tmp_path, monkeypatch):
    from spherexlens.eval import run
    monkeypatch.setattr(config, "NPIX", 64)
    monkeypatch.setattr(config, "EVAL_INJECT_PER_IMAGE", 1)
    out = tmp_path / "evalout"
    rc = run.main(["--cache", noise_cache, "--quick", "--out", str(out), "--write-threshold",
                   "--labels", str(tmp_path / "labels.parquet")])
    assert rc == 0
    m = json.loads((out / "metrics.json").read_text())
    for k in ("pipeline", "generated_utc", "threshold", "null_test", "injection", "answer_key"):
        assert k in m
    assert m["null_test"]["n_fields"] == 1
    assert {"snr", "mu", "category", "recall", "n"} <= set(m["injection"]["grid"][0])
    hist = json.loads((out / "metrics_history.json").read_text())
    assert hist[-1]["pipeline_version"] == m["pipeline"]["pipeline_version"]
    assert gate.compare(m, m) == []


def test_metrics_routes_404_then_serve(tmp_path, monkeypatch):
    from spherexlens.routes import metrics as routes
    monkeypatch.setattr(config, "EVAL_DIR", str(tmp_path))
    monkeypatch.setattr(config, "ML_MODEL_DIR", str(tmp_path / "models"))
    assert routes.get_metrics().status_code == 404
    assert routes.get_metrics_history() == []
    r = routes.get_model()
    assert r.status_code == 404 and b"ml.train" in r.body
    (tmp_path / "metrics.json").write_text(json.dumps({"null_test": {}}))
    assert routes.get_metrics() == {"null_test": {}}


def test_gate_passes_identical_and_flags_regression():
    base = {"classifier": {"precision": 0.9}, "null_test": {"false_alarms_per_field": 0.1}}
    assert gate.compare(base, base) == []
    worse = {"classifier": {"precision": 0.8}, "null_test": {"false_alarms_per_field": 0.1}}
    assert gate.compare(worse, base)
