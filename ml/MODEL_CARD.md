# Model card: SPHEREx-Lens real/bogus classifier `rb-2026.11.13-1`

Generated 2026-11-13T10:33:11Z by pipeline 2.0.0-dev (config d8d398effb40).

## Intended use
Ranks ZOGY difference-image detections by the calibrated probability that they are real astrophysical changes (movers, variables) rather than artefacts. It is a *ranking aid* for human review and public voting: it never changes the rule-based KNOWN/CANDIDATE verdict, and a low score is not grounds to discard a detection without looking. Not validated for crowded Galactic-plane fields or for extended / saturated sources.

## Model
HistGradientBoostingClassifier on 22 features (spherexlens/ml/features.py, fixed order) + isotonic calibration fitted on out-of-fold scores. Missing features are NaN and handled natively.

## Data
12150 labelled detections (6519 real, 5631 bogus) from 18 fields. Training fields: 14; held-out fields (never used for training or calibration): Betelgeuse, Sombrero_M104, TRAPPIST_1, WISE_0855.

Label sources (count): answer_key:real 7, injection:real 6512, mask:bogus 278, null_test:bogus 5353

## Label sources and their biases
- **injection (real)** -- synthetic point sources added to real co-adds with the measured PSF and recovered by the strict test. Biased towards clean, isolated, PSF-perfect sources; real movers with odd colours or blends are under-represented.
- **answer_key (real)** -- catalogued high-proper-motion stars, VSX variables and known asteroids that were detected. Biased towards bright, catalogued objects.
- **null_test (bogus)** -- detections in half-vs-half subtractions of one epoch. Representative of noise and wavelength-sampling systematics, but not of artefacts that only appear between epochs (persistence, ghosts at new roll angles).
- **mask (bogus)** -- detections on masked or edge pixels; defined using `masked_frac`, which is also a feature, so that feature's importance is inflated.
- **vote** -- public votes (majority, ties dropped) when available.

## Validation
Split: GroupKFold(5) by field on 14 training fields + 4 held-out fields. Decision threshold P >= 0.5.

| metric | held-out fields | GroupKFold out-of-fold (training fields) |
|---|---|---|
| precision | 0.957 (95% CI 0.736-1.000) | 0.991 (95% CI 0.985-0.995) |
| recall | 0.930 (95% CI 0.797-0.998) | 0.971 (95% CI 0.932-0.997) |
| f1 | 0.943 (95% CI 0.830-0.993) | 0.981 (95% CI 0.960-0.994) |
| pr_auc | 0.987 (95% CI 0.920-1.000) | 0.997 (95% CI 0.993-1.000) |

Held-out rows: 2287 (1126 real). Confusion [[TP, FN], [FP, TN]]: [[1047, 79], [47, 1114]].

Top permutation importances (drop in average precision on held-out data): `dipole_sep_px` +0.325, `psf_chi2` +0.051, `exposure_quality` +0.045, `snr` +0.044, `fwhm_ratio` +0.016, `noise_ratio` +0.007, `masked_frac` +0.004, `ellipticity` +0.002

## Limitations
- Small number of fields: the confidence intervals are wide and per-field behaviour (zodiacal level, crowding, ecliptic latitude) is barely sampled.
- Labels are proxies (see biases). Real precision on genuinely new sources is unknown until candidates are followed up.
- Features that the pipeline does not yet supply (cross-detector colour, spectral indices, exposure quality, n_pairs_seen) are NaN and carry no weight.
- Retrain whenever the detection pipeline or the S-map normalisation changes; the model is tied to the config hash above.

Version `rb-2026.11.13-1`; artefacts `ml/models/real_bogus-rb-2026.11.13-1.joblib/.json`.


## Held-out accuracy by detection kind (read this first)

The headline held-out numbers above are **inflated by dipoles**: the injected
movers are dipoles, while the null-test "bogus" examples almost never are, so
"is it a dipole" alone separates them. Per kind, on the 4 held-out fields
(Betelgeuse, Sombrero_M104, TRAPPIST_1, WISE_0855), at P(real) >= 0.5:

| kind | n | real | bogus | precision | recall |
|---|---|---|---|---|---|
| dipole | 814 | 797 | 17 | 1.0 | 1.0 |
| brightening/monopole | 797 | 229 | 568 | 0.895 | 0.817 |
| fading | 676 | 100 | 576 | 0.716 | 0.63 |

* Brightening and fading are the honest measure of real/bogus skill.
* The perfect dipole score is not evidence of skill: only 17 bogus dipoles
  exist in the held-out data. Fake dipoles (e.g. misregistered bright stars
  across epochs) need their own labels -- public votes on movers are the
  intended source.
* The model only ranks candidates; it never changes KNOWN / CANDIDATE.
