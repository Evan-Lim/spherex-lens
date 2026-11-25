# Model card: SPHEREx-Lens real/bogus model `rb-v18-2026.11.25` (default)

Generated 2026-11-25 by pipeline 2.0.0-dev (config 56d16c0f08c5). Full history v1-v18: `docs/ml-report-v1-v18.html`.

## Intended use
Ranks detections in the difference of two epoch co-adds by how likely they are real astrophysical changes (moving objects, variable stars) rather than artefacts. It is a *ranking aid*: it never changes the rule-based KNOWN/CANDIDATE verdict, and a low score is not grounds to discard a detection without looking.

Each scored detection gets a **tier**: `candidate` (score >= 0.5), `review` (0.2-0.5, sent to the vote queue) or `low`.

## Model
Three LightGBM "experts" (all examples / movers / brightness changes) and a small "judge" averaged over 5 seeds, which sees the experts' opinions plus 61 numbers per detection: 16 production features, 7 stamp numbers, 6 residual shapes, 7 brightness-vs-shift numbers, 10 field-context numbers, 9 colour numbers and 6 missing-value flags. The trees are exported to plain NumPy arrays, so the website needs no LightGBM. Serving code: `spherexlens/ml/v18.py` + `spherexlens/ml/imagefeat.py`; it reproduces the training features and scores exactly (tests/test_ml_v18.py).

## Data
- **Real sky:** SPHEREx DR1 + QR3 (surveys 1-4), 42 sky patches, compared at matched wavelength in multi-detector epoch co-adds (as the website builds them). 281 real brightness changes of 77 catalogued stars (VSX / Gaia / ASAS-SN / OGLE; change >= 10% at >= 3 sigma), 342 real non-changing stars (steady Gaia stars and catalogued variables that did not change). Each star is weighted equally (1/sqrt(rows per star)).
- **Simulated:** 84,354 rows: injected sources, null tests, star variability, star movers, asteroid-like movers and fake movers.

## Validation (every number from sky the model never trained on)
Split: nested leave-patches-out (GroupKFold(6) by patch, inner GroupKFold(3)).

| test | result |
|---|---|
| real changing stars, ranking (AUC) | 0.989 |
| real changing stars caught at 0.5 | 259/281 = 92.2% |
| real non-changing stars flagged at 0.5 | 8/342 = 2.3% |
| precision / recall / PR-AUC at 0.5 | 0.970 / 0.922 / 0.990 (patch-bootstrap 95%: 0.90-0.99 / 0.70-0.97 / 0.93-1.00) |
| real-sky asteroid exam (co-add), caught at 0.5 | 61/65, with 0/76 steady stars flagged |
| simulated asteroids caught / simulated glitches passed | 92.2% / 3.3% |
| same-brightness check (AUC within brightness bins) | 0.91-0.995 |

## Limitations
- Tested on archived SPHEREx data only: **not yet on live data and not by independent reviewers**.
- Variable-star positives are narrow: 86% are in the Large Magellanic Cloud, mostly red giants; only 6 pulsating and 11 eclipsing examples; only 12 non-changing stars as faint as G 15.5-17.
- Catalogued-but-quiet variables score slightly higher than ordinary steady stars (AUC 0.62, ideal 0.5): the model still partly recognises "variable-type star".
- v18's separate single-exposure asteroid model (trained on 1,254 real asteroids) is **not** served: the website compares multi-detector co-adds, not single exposures.
- Retrain when the detection pipeline or co-add method changes.

Artefacts: `ml/models/real_bogus-rb-v18-2026.11.25.joblib` + `.json` (named by `ml/models/LATEST.json`).

---

## Previous model: `rb-2026.11.13-1` (v1)
Still available with `SPHEREX_ML_MODEL=v1`; its own card is `ml/models/real_bogus-rb-2026.11.13-1.json`.
HistGradientBoosting on 22 features with isotonic calibration, trained on 12,150 mostly synthetic labels (6,519 injected "real", 5,631 null-test and masked "bogus") from 18 fields. Held-out precision 0.957 and recall 0.930 were **inflated by dipoles**: injected movers were dipoles and almost no bogus examples were, so "is it a dipole" alone separated them (per kind: dipoles 1.0 / 1.0; brightening 0.895 / 0.817; fading 0.716 / 0.63). The "mask" bogus label was defined by a feature (`masked_frac`), and 6 of its 22 features were always empty.
