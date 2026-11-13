# SPHEREx-Lens v2 — what changed and where to look

Branch `exec-plan-v1` (27 commits on top of `main` at `d5c47dd`), November 2026.
146 files changed. This page maps every part of the v2 execution plan to the
files that implement it, the tests that check it, and the commit that
introduced it.

Quick ways to examine the changes:

```powershell
git log --oneline --first-parent main..exec-plan-v1   # one line per step
git show --stat <commit>                              # files in one step
git diff main..exec-plan-v1 -- <path>                 # everything that changed in a file
```

---

## Headline results

Measured on all 18 SPHEREx DR1 fields (`eval/metrics.json`):

| | Before v2 | After v2 |
|---|---|---|
| Planted objects found (strict: right place, right kind) | 0.2 % | **69.3 %** (brightening 96.7 %, fading 95.3 %, movers 56.0 %) |
| Fake CANDIDATEs per field (null test, after triage) | not measured | **1.66** (0 in six star fields) |
| Asteroid check | claimed in README, not implemented | JPL SB Identification + SBDB + CADC SSOIS |
| ML real/bogus on unseen fields | none | brightening P 0.90 / R 0.82, fading 0.72 / 0.63 |
| Data release | `spherex_qr2` (retired by IRSA: 0 images) | `spherex_dr1` |

Honest limits are in `EXPLAINER.md` section 9 and `ml/MODEL_CARD.md`.

---

## Phase 0 — foundations (`2070f1d`)

| What | Files |
|---|---|
| Pipeline version + config hash stamped on every result | `version.py` |
| Central settings (all tunable numbers, env-overridable) | `config.py` |
| Enricher hook: subsystems add fields without editing the pipeline | `spherexlens/enrich.py`, call at end of `app.py: get_dashboard` |
| Auto-included API routers | `spherexlens/routes/__init__.py`, bottom of `app.py` |
| Archive adapter base (retries, backoff, rate limit, cache, provenance, fail-don't-fake) | `spherexlens/ingest/base.py` |
| JSON contract for every new field / endpoint | `spherexlens/CONTRACT.md` |
| CI (tests, dependency audit, metrics regression gate) | `.github/workflows/ci.yml`, `spherexlens/eval/gate.py` |
| Default collection `spherex_dr1` | `config.py: SPHEREX_COLLECTION`, `step1_ingest.py`, `live_ingest.py`, `cloud_refresh.py` |
| Offline fixtures (2 real DR1 cutouts) | `tests/fixtures/` |
| Tests | `tests/test_v2_foundation.py` |

## Phase 1 — correctness

| What | Files | Commit |
|---|---|---|
| **1.1 Asteroid check**: JPL SB Identification per exposure time, SBDB orbit details, CADC SSOIS cross-check; `KNOWN_ASTEROID`, `POSSIBLE_UNKNOWN_ASTEROID`; "unavailable" never passes silently; 25 s web time box, cloud refresh waits | `spherexlens/ingest/jpl.py`, `spherexlens/ingest/ssois.py`, `spherexlens/triage/asteroids.py`, `config.py` (asteroids block) | `aa8d95a`, `e1ccfaf` |
| **1.2 Fading sources** (`monopole_negative`) | `step3_zogy.py: find_detections` | `e9cf774` |
| **1.3 Real PSF** from the DR1 `EPSF` table (zone chosen via the cutout's WCS "A"), per-epoch PSF | `step2_coadd.py: load_exposure_full, coadd_matched(return_psf=True)`, `spherexlens/imaging.py`, `step3_zogy.py: zogy(psf1, psf2)` | `e9cf774` |
| **1.4 ZODI** background subtraction | `step2_coadd.py: prepare_exposure` | `e9cf774` |
| Tests | `tests/test_asteroids.py`, `tests/test_image_science.py` | |

## Phase 2 — ingestion and new data sources (`24f6165`)

| What | Files |
|---|---|
| SPHEREx SIA2 + public S3 bucket adapters | `spherexlens/ingest/spherex.py` |
| Gaia DR3: IRSA → MAST → VizieR fallback (wired into `step1_ingest.py`), DR4-ready | `spherexlens/ingest/gaia.py` |
| AllWISE / VSX / NEOWISE wrappers | `spherexlens/ingest/wise.py`, `vsx.py`, `neowise.py` |
| CSA NEOSSat via CADC TAP (`NEOSSAT`) | `spherexlens/ingest/cadc.py` |
| WiseView / IRSA Finder / SIMBAD / MAST links | `spherexlens/ingest/links.py` |
| Coverage check + `GET /api/coverage` | `spherexlens/ingest/coverage.py`, `spherexlens/routes/coverage.py` |
| Parquet catalogue store + migration script | `spherexlens/ingest/store.py`, `scripts/migrate_catalogs_to_parquet.py` |
| Tests | `tests/test_ingest.py` |

## Phase 3 — preprocessing and cleaning

| What | Files | Commit |
|---|---|---|
| Time / flux normalisation, epoch propagation | `spherexlens/ingest/normalize.py` | `24f6165` |
| Per-exposure quality score, edge and spike masks, KD-tree de-duplication (`n_pairs_seen`) | `spherexlens/imaging.py`, `step2_coadd.py`, `app.py` | `e9cf774` |
| Gaia RUWE reliability | `catalog_tools.py: gaia_reliable`, `step4_triage.py` | `24f6165` |
| NEOSSat frame cleaning (per CSA tutorial) | `spherexlens/ingest/neossat_clean.py` | `24f6165` |
| Infrared bright-star masks grow with brightness (cap 10′) | `catalog_tools.py: bright_mask_radius` | `d8332e6` |

## Phase 4 — evaluation (`64c99a1`, calibrated in `d8332e6`, final run `05b7ffd`)

| What | Files |
|---|---|
| Field loader / subtraction harness | `spherexlens/eval/harness.py` |
| Null test (two halves of one epoch) incl. fake-CANDIDATE count after triage | `spherexlens/eval/null_test.py` |
| Strict injection-recovery, matched-filter SNR against measured noise | `spherexlens/eval/injection.py`, `step6_injection.py` |
| Real-sky answer key, threshold tools, metrics assembly, CLI | `spherexlens/eval/answer_key.py`, `threshold.py`, `metrics.py`, `run.py` |
| Results + CI baseline | `eval/metrics.json`, `eval/baseline_metrics.json`, `eval/metrics_history.json` |
| `/api/metrics`, `/api/metrics/history`, `/api/model` | `spherexlens/routes/metrics.py` |
| Extra download for the null test (`--keep-per-slot 2`) | `step1_ingest.py` (`f4e4585`) |
| Tests | `tests/test_eval.py` |

### Decision A — the calibrated detector (`d8332e6`)

The strict test exposed why v1 found almost nothing. Each fix was measured:

| Defect | Fix | Where |
|---|---|---|
| `S` divided by its std: one bright residual shrank every detection | `S / (1.4826·MAD)`; `config.ZOGY_S_NORMALISATION = "robust"` | `step3_zogy.py: s_noise_scale` |
| Any dip paired with any peak ≤ 75″ away: fading stars became fake movers | Pairing limited by plausible proper motion over the real baseline, lobes within 2× in strength, strongest first | `step3_zogy.py: dipole_max_sep_px, find_detections`; `config.py: DIPOLE_*`; `app.py` passes the real baseline |
| ε Indi A halo outside the 3′ mask: 44 fake candidates | Exponential IR mask radius | `catalog_tools.py` |
| Threshold assumed, not measured | `DETECTION_SIGMA = 6.0` from the null test (0.86 → 0.48 fake candidates/field, equal recall) | `config.py` |
| ZOGY astrometric-noise term (tested, raised fake candidates) | implemented, **off** by default | `step3_zogy.py: zogy(astrometric_sigma_px)` |

## Phase 5 — machine learning (`64c99a1`, model `05b7ffd`)

| What | Files |
|---|---|
| 22 features in fixed order | `spherexlens/ml/features.py` |
| Label factory (injections real; null residuals, masks bogus; votes) | `spherexlens/ml/labels.py` |
| Training: GroupKFold by field + held-out fields, isotonic calibration, model card | `spherexlens/ml/train.py` |
| Serving enricher (score + top-3 reasons; never changes the verdict) | `spherexlens/ml/serve.py` |
| Spectral classifier (guarded; no skill yet on DR1 cutouts) | `spherexlens/ml/spectral.py` |
| Active learning (uncertain first; retrain from votes) | `spherexlens/ml/active.py` |
| Trained model `rb-2026.11.13-1` + card (read the per-kind table) | `ml/models/`, `ml/MODEL_CARD.md` |
| Tests | `tests/test_ml.py` |

## Phase 6 — combined evidence (`a1a3707`)

ML + signal + motion agreement with NEOWISE/Gaia + asteroid check + NEOSSat,
each part listed with its reason: `spherexlens/triage/evidence.py`,
`tests/test_evidence.py`.

## Phase 7 — deep fields (`a1a3707`, `fc394a6`, `4905bac`)

| What | Files |
|---|---|
| Time-stratified sampling of `spherex_dr1_deep`, own cutout cache | `spherexlens/deep/select.py` |
| Forced photometry (FLAGS via `step2_coadd.BAD_BITS`) | `spherexlens/deep/photometry.py` |
| Light curves normalised by each star's own spectrum; 3 % floor; ensemble rule; Lomb-Scargle; motion | `spherexlens/deep/lightcurve.py` |
| CLI + publish to snapshot bucket | `spherexlens/deep/run.py` |
| `/api/deep`, `/api/deep/{name}` (local + bucket) | `spherexlens/routes/deep.py` |
| Cloud Run job `spherex-deep` | `cloud/terraform/production.tf`, `variables.tf` |
| First result: NEP, 78 visits, 1 variable = Gaia's own VARIABLE star | `deep/NEP.json` (local, ignored) |
| Tests | `tests/test_deep_fields.py` |

## Phase 8 — website (`5bf7e9e`, `84da7f9`, `0d30eee`)

Vetting card, coverage chips, Accuracy page, Deep fields view, Public/Expert
mode, voting and "Help classify", downloads, credits, accessibility pass,
threshold-aware histogram, 20 s request timeout.

| Files | |
|---|---|
| `static/app.js`, `static/index.html`, `static/style.css`, `static/sw.js` (cache v18) | UI |
| `static/dev/mock_*.json` | `?mock=v2` preview data |
| `export_static.py` | static build exports metrics / model / credits / deep |
| `docs/ACCESSIBILITY.md` | WCAG 2.1 AA checks and open items |

## Phase 9 — production hardening (`c8f3b08` and follow-ups)

| What | Files |
|---|---|
| Votes store (local JSONL / GCS create-only, salted client hash) | `spherexlens/votes.py`, `spherexlens/routes/votes.py` |
| CSV / VOTable exports, IVOA cone search | `spherexlens/routes/catalog_export.py` |
| Health, liveness (+ S8 signature status), credits (verified wording) | `spherexlens/routes/health.py`, `credits.py` |
| Job queue | `spherexlens/jobs.py`, `spherexlens/routes/jobs.py` |
| Rate limiter, JSON logs | `spherexlens/http_guard.py`, `spherexlens/logging_setup.py` |
| Security review S1–S21 (all fixed; S8 switch-on needs Netlify) | `docs/SECURITY_REVIEW.md`; code in `app.py`, `spherexlens/http_guard.py`, `netlify.toml` |
| Hashed lock file, pip-audit, non-root containers | `requirements.lock`, `requirements.txt`, `Dockerfile`, `Dockerfile.cloud`, `.github/workflows/ci.yml` |
| Terraform: votes bucket, alerts, uptime check, proxy secret, budget option, deep job | `cloud/terraform/production.tf`, `main.tf`, `variables.tf`, `outputs.tf` |
| Operator steps (v2 apply, S8, deep job) | `cloud/README.md` |
| Tests | `tests/test_production.py` |

## Phase 10 — documentation (`86d3d72`, `05b7ffd`, `9341e1c`, `84da7f9`)

| Document | What changed |
|---|---|
| `README.md` | "What v2 adds": sources, data paths, measuring accuracy, measured results |
| `EXPLAINER.md` | §11a plain-language v2; numbers; 6σ explained; §7 Barnard's Star rewritten; §9 honesty |
| `paper/spherexlens_paper.tex` | new section "Resolving the completeness defect (v2)" (PDF needs rebuilding) |
| `paper/slides_and_script.tex` | new slide 16b (PDF needs rebuilding) |
| `spherexlens/CONTRACT.md`, `ml/MODEL_CARD.md`, `docs/SECURITY_REVIEW.md`, `docs/ACCESSIBILITY.md`, `cloud/README.md` | reference documents |

---

## Review fixes

`a7f59c7` addressed the cloud ultrareview of Phases 0/6/7. It covered:

- The deep-photometry FLAGS mask.
- Deep-run fail-safety.
- Cutout cache keys.
- The sampler quota.
- The metrics-gate bypass.
- Enricher import isolation.
- Duplicate WCS calls.
- The collection setting.

## Not in this branch / for the operators

- Netlify: redeploy `site/` for the new UI; S8 secret steps in `cloud/README.md`.
- Paper and slide PDFs need rebuilding from the updated `.tex`.
- Merging `exec-plan-v1` into `main` and pushing.
