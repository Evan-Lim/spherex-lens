# SPHEREx-Lens v2 data contract

The fields each v2 subsystem adds to API responses. Backend and frontend are
built in parallel against this file; change it only together with both sides.

All new fields are **optional** — the UI must render a dashboard that lacks
any of them (older snapshots, a subsystem that failed, the static build).

## Dashboard payload (`GET /api/dashboard`)

Top level, added by app.py:

```jsonc
"pipeline": {"pipeline_version": "2.0.0-dev", "config_hash": "ab12cd34ef56",
             "model_version": "rb-2026.11.13-1" /* or null */},
"enrichment": {"<enricher>": {"status": "ok" | "error" | "unavailable",
                              "error": "...", "seconds": 0.4}},
```

Per detection (`payload.detections[i]`), existing fields unchanged
(`category`, `verdict`, `priority`, `reasons`, `kind`, `snr`, `position`, ...).
`kind` gains the value `"monopole_negative"` (a source that got fainter).
`category` gains `"KNOWN_ASTEROID"` and `"POSSIBLE_UNKNOWN_ASTEROID"`.

```jsonc
"ra": 133.78, "dec": -7.24,            // sky position of the detection (deg)
"mjd_a": 60700.1, "mjd_b": 60880.3,    // epochs compared

"asteroid": {                           // Phase 1.1 -- spherexlens.triage.asteroids
  "status": "match" | "no_match" | "unavailable",
  "checked": ["jpl_sb_ident", "ssois"],
  "match": {                            // present when status == "match"
    "designation": "433 Eros", "orbit_class": "AMO", "neo": true,
    "h_mag": 10.4, "v_mag": 14.2,
    "sep_arcsec": 4.1, "epoch": "a" | "b", "mjd": 60700.1,
    "jpl_ra": 0.0, "jpl_dec": 0.0, "pos_unc_arcsec": 1.2,
    "ssois_agrees": true | false | null, "ssois_sep_arcsec": 2.0,
    "sbdb_url": "https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=...",
    "ssois_url": "https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/ssois/..."
  },
  "n_in_field": 3,
  "error": "..."                        // when unavailable
},

"links": {                              // Phase 2 -- spherexlens.ingest.links
  "wiseview": "http://byw.tools/wiseview#ra=...&dec=...",
  "irsa_finder": "https://irsa.ipac.caltech.edu/applications/finderchart/?...",
  "simbad": "https://simbad.cds.unistra.fr/simbad/sim-coo?...",
  "gaia_mast": "https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery=..."
},

"features": {"snr": 7.1, "...": 0.0},   // Phase 5.1 -- the ML feature vector

"ml": {                                 // Phase 5.8 -- spherexlens.ml.serve
  "real_bogus": 0.93,                   // calibrated P(real)
  "model_version": "rb-2026.11.13-1",
  "reasons": [{"feature": "snr", "label": "strong signal", "effect": +0.21}],
  "uncertain": false                     // true -> sent to public voting first
},

"evidence": {                           // Phase 6 -- spherexlens.triage.evidence
  "score": 0.88,                        // 0..1, combined believability
  "parts": [{"name": "ml", "value": 0.93, "weight": 0.4, "detail": "..."},
            {"name": "motion_consistency", "value": 0.9, "detail": "NEOWISE 2010-2024 agrees within 2.1 sigma"},
            {"name": "asteroid_check", "value": 1.0, "detail": "no known asteroid"},
            {"name": "neossat", "value": null, "detail": "no NEOSSat coverage"}]
},

"neossat": {"status": "none" | "frames" | "confirmed" | "not_seen" | "unavailable",
            "n_frames": 0, "frames": [{"obs_id": "...", "mjd": 0, "url": "..."}]}
```

Payload-level, Phase 2.2:

```jsonc
"coverage": [{"archive": "spherex", "available": true, "count": 18, "detail": "3 epochs"},
             {"archive": "neowise", ...}, {"archive": "gaia", ...},
             {"archive": "neossat", ...}, {"archive": "jpl_sb_ident", ...}]
```

Payload-level, Phase 3.2 (exposure quality):

```jsonc
"exposure_quality": [{"obs_id": "...", "score": 0.91, "kept": true,
                      "reasons": ["flagged 3%", "psf fwhm 1.9 px"]}]
```

## New endpoints (each in `spherexlens/routes/<name>.py`)

| Endpoint | Owner | Returns |
|---|---|---|
| `GET /api/coverage?ra=&dec=&radius=` | ingest | `{"ra","dec","coverage":[Coverage...]}` |
| `GET /api/metrics` | eval | contents of `eval/metrics.json` (below) |
| `GET /api/metrics/history` | eval | `[{"pipeline_version","generated_utc","precision","recall"}]` |
| `GET /api/model` | ml | model card as JSON |
| `POST /api/votes` | prod | body `{"target","detection_id","vote":"real"|"bogus"|"unsure"}` -> `{"ok":true}` |
| `GET /api/votes/queue?n=10` | prod | detections to vote on, most-uncertain first |
| `GET /api/cone?RA=&DEC=&SR=` | prod | IVOA Simple Cone Search VOTable of candidates |
| `GET /api/candidates.{csv,votable}` | prod | all triaged detections, flat table |
| `GET /api/health` | prod | `{"ok":true,"archives":{...},"pipeline":{...}}` |
| `GET /api/credits` | prod | data-source licences and acknowledgements |

`detection_id` = `"<target>:<pair>:<y>,<x>"` (stable across reloads).

## `eval/metrics.json`

```jsonc
{
  "pipeline": {...}, "generated_utc": "...",
  "threshold": {"sigma": 5.3, "false_alarms_per_field": 0.08, "method": "null-test + BH FDR"},
  "null_test": {"n_fields": 16, "false_alarms_per_field": 0.08, "false_alarms_per_deg2": 3.5},
  "injection": {"recall_overall": 0.87,
                "grid": [{"snr": 5, "mu": 1, "category": "mover", "recall": 0.4, "n": 50}]},
  "answer_key": {"n": 41, "recall": 0.78, "by_class": {"high_pm_star": 0.8}},
  "classifier": {"precision": 0.94, "recall": 0.9, "f1": 0.92, "pr_auc": 0.95,
                 "ci95": {"precision": [0.9, 0.97]},
                 "pr_curve": [[recall, precision], ...],
                 "calibration": [[predicted, observed, n], ...],
                 "confusion": {"labels": ["real","bogus"], "matrix": [[0,0],[0,0]]},
                 "split": "GroupKFold by field + held-out fields"}
}
```

## v2 additions agreed at merge (2026-11-13)

Fields the subsystems actually write, beyond the original contract above.

**Detection**
- `sign` (+1 brighter / -1 fainter), `n_pairs_seen`, `pairs_seen`, `spike`,
  `edge` (image science); `psf_fwhm_arcsec` (evidence).
- `asteroid.match` also has `match_radius_arcsec`, `condition_code`,
  `ssois_n_images`, `ssois_neossat`, `ssois_error`; `asteroid.checked` may
  include `"jpl_sbdb"`; `asteroid.n_in_field` is null when unavailable.
  `ssois_url` points to `/en/ssois/results.html?...`.
- `gaia.astrometry_reliable: false` for RUWE > config.GAIA_RUWE_MAX;
  `motion_check` may be `{"precise": false, "status": "not tested: ..."}`.
- `neossat.frames[].url` is a CADC DataLink URL.
- `ml` absent and `payload.ml_status = {"status": "no_model"}` when no model
  is loaded (features are still written).

**Payload**
- `asteroid_check = {"status": ok|partial|unavailable|skipped, "n_queries",
  "n_in_field", "errors": [], "reason", "ecliptic_latitude_deg", "query_mjds",
  "objects", "pending"}` (`pending` = JPL queries still running after the
  web time box, config.ASTEROID_TIME_BOX_S).
- `epochs[].psf = {"source", "fwhm_arcsec", "grid_fwhm_arcsec"}`.
- `exposure_quality[]` rows also carry `epoch`, `band`, `components`,
  `zodi_subtracted`, `psf_source`, `psf_fwhm_arcsec`, `mjd`.
- `coverage[]` entries may carry `pending`, `cached`, `seconds`, `checked_utc`.
- `catalog_status.gaia_mirror` (`irsa` | `mast` | `vizier:<host>` | null);
  target meta `catalog_snapshot_id`.
- `enrichment.<module>` is `{"status": "error", "error": "failed to load: ..."}`
  when an enricher module could not be imported.

**Endpoints**

| Endpoint | Returns |
|---|---|
| `GET /api/health/live` | `{"ok", "pipeline", "uptime_s", "proxy_signature": not_configured|valid|missing_or_invalid}` |
| `GET /api/health` | `{"ok", "pipeline", "snapshot": {status, published_at, age_hours, ...}, "archives": {name: {status, http|error, latency_ms, checked_at}}, "archives_down": [...]}`; 503 when `ok` is false |
| `POST /api/jobs/field`, `GET /api/jobs/{id}` | 202 + job id; `{status: queued|running|done|failed|timed_out, result}` |
| `GET /api/votes/queue` | `{"n", "ranking", "source", "generated_utc", "detections": [flat row + pair, position, reasons, links, votes: {real, bogus, unsure}]}` |
| `GET /api/credits` | `{"sources": [{"id", "name", "used_for", "acknowledgement", "citation?", "licence", "licence_url?", "url", "wording_source", "wording_verified"}]}` |
| `GET /api/coverage` | also `radius_deg`, `pipeline` |
| `GET /api/deep`, `GET /api/deep/{name}` | deep-field list / result (`sources[]` with `variable`, `variable_self`, `ensemble_chi2_median`, `period`, `motion`, `lightcurve`, `spectrum`) |

- `detection_id` input also accepts `->` for `→`; the server normalises to `→`.
- CSV / VOTable column names are flattened: `ml_real_bogus`, `evidence_score`,
  `asteroid_status`.

**`eval/metrics.json`** also has: `null_test.{ci95, per_field, skipped,
n_realisations}`, `injection.grid[].{n_recovered, ci95}`,
`injection.{by_category, psf_model}`, `answer_key.by_class_n`,
`pipeline_purity_estimate`, `threshold.bh_fdr`.  The confusion matrix is
rows = true label, columns = prediction; `classifier.ci95` covers
`precision`, `recall`, `f1`, `pr_auc`.
