# Commit notes — Cloud snapshot backend, IRSA refresh, and speed/reliability work

This commit moves SPHERExLens from a local data cache to a **Google Cloud
snapshot backend that refreshes all 18 registered fields from IRSA every
12 hours**, adds a visible refresh status to the dashboard, fixes several
bugs that would have broken the cloud deployment, and makes both the snapshot
views and the "fetch fresh from IRSA" views considerably faster.

No science results or stored data were altered by the speed work: the same
inputs still produce the same outputs (details in §6).

---

## 1. Architecture after this commit

```
Cloud Scheduler (00:00 and 12:00 Asia/Kuala_Lumpur)
        │
        ▼
Cloud Run Job "spherex-refresh"  (cloud_refresh.py)
  for each of the 18 fields:
    ask IRSA which SPHEREx images exist  ──► unchanged? keep previous result
    changed? download cutouts, run the pipeline (co-add, ZOGY, triage,
             spectrum, recovery grid, catalogues every 7 days, NEOWISE)
  publish a complete, versioned snapshot to Cloud Storage
        │
        ▼
GCS bucket "spherex-lens-spherex-snapshots"
  current.json ─► manifests/<sha256>.json ─► snapshots/…/<sha256>.json
        │
        ├──► Cloud Run "spherex-lens-api"   fast snapshot reads (Netlify /live-api)
        └──► Cloud Run "spherex-lens-live"  fresh IRSA fetches, one field per instance
```

* **Snapshot mode** (default): every page reads the latest published snapshot.
* **Fresh mode** ("Fetch fresh SPHEREx data from IRSA" ticked): the field — or,
  in All-sky, all 18 fields six at a time — is checked against IRSA right now.
* **Manual refresh**: "Fetch all 18 from IRSA" (operator token) starts the
  same Cloud Run job on demand.
* There is **no local data cache** any more; `data_cache/`, `data_cache_demo/`,
  `figures/`, `figures_real/` and the pre-rendered `site/api/*.json` are removed
  from the repository.

---

## 2. New files

| File | Purpose |
|---|---|
| `cloud_refresh.py` | The 12-hour refresh job: per-field IRSA check, incremental reprocessing, versioned publish, refresh lock with heartbeat, garbage collection of unreferenced objects. |
| `snapshot_store.py` | Versioned GCS storage: content-addressed (SHA-256) immutable objects, `current.json` pointer swapped with a generation precondition, refresh lock. |
| `catalog_tools.py` | Shared catalogue science (epoch propagation, match radii, AllWISE reliability, VSX decoding, Gaia distances, SED fluxes). |
| `step7_neowise.py` | NEOWISE/AllWISE infrared history for each target and detection (one IRSA TAP upload-join per target). |
| `Dockerfile.cloud` | Production image for Cloud Run (no local data). |
| `cloud/terraform/*` | Infrastructure: bucket, two Cloud Run services, job, scheduler, service accounts, Secret Manager token. State and `terraform.tfvars` are git-ignored. |
| `cloud/cloudbuild.yaml` | Image build **plus an import smoke test**: the build fails if a module the API or the job imports is missing from `requirements.txt`. |
| `cloud/run-local.ps1` | Optional local launcher that also connects the "Fetch all 18" button. |
| `conftest.py` | Keeps the test suite offline (no real GCS reads, no warm-up threads). |
| `test_cloud_snapshots.py`, `test_pipeline_coverage.py` | 62 tests in total (all pass, ~2.5 s, no network). |

---

## 3. Cloud refresh job fixes (B1–B8)

* **B1** NEOWISE history is restored to disk before the first dashboard is
  built, so a catalogue-only refresh no longer publishes a dashboard without
  the NEOWISE panel.
* **B2** Refresh lock has a 5-minute heartbeat and a 30-minute stale
  threshold; a crashed run no longer blocks the next two scheduled runs, and
  a pre-empted run is marked failed in the status.
* **B3** Per-field isolation: one failing field keeps its previous published
  entry and the run continues; publication is aborted only if more than half
  of the fields fail.
* **B4** Split into a snapshot service (high concurrency, 30 s timeout) and a
  live service (one request per instance, 900 s timeout).
* **B5** Custom field names cannot collide with published/registered fields.
* **B6** Custom-field recovery always runs live.
* **B7** Live requests for entries without stored metadata fall back to the
  built-in target registry.
* **B8** The UI stops waiting for a requested run after 5 minutes.
* **B9/B10** GZip responses; explicit error state when the target list fails.

---

## 4. Bugs fixed that would have broken the deployment

* **Rate limiter broke every internal caller.** slowapi's decorator raises
  unless it receives a real `Request`, so the three SSE progress streams
  (`/api/dashboard/stream`, `/api/recovery/stream`, `/api/field/stream`), the
  refresh job and `export_static.py` all failed. Endpoints are now registered
  through `_rate_limited_route`: the HTTP route is limited, the module-level
  function stays plain for internal callers.
* **Rate limit shared by all visitors.** Behind Cloud Run and the Netlify
  proxy every request came from the same address. The key now uses Netlify's
  `x-nf-client-connection-ip`, else the last `X-Forwarded-For` hop.
* **`slowapi` missing from `requirements.txt`** — the first Cloud Run
  deployment failed to start. Added, and the build now has the import smoke
  test so this cannot reach a deployment again.
* **Plain `uvicorn app:app` exited at start-up** because the bucket had to be
  set by hand, and the storage client failed without a default Google
  project. The app now defaults to the production bucket and project
  (environment variables still override them) and reports one clear message
  if Google credentials are missing instead of exiting.
* **Fresh mode depended on the snapshot.** If the bucket could not be read,
  a fresh IRSA fetch for a registered field failed with "No cached data". It
  now falls back to the field's registered coordinates.
* **IRSA time-outs could exceed Cloud Run's 900 s limit** (worst case ~990 s
  for the listing alone, so a request could silently vanish). Now: SIA2
  10 s connect / 90 s read per attempt, cutouts the same, and a 420 s budget
  after which no new download or retry starts. Worst case 865 s, enforced by
  a test.
* Local `Dockerfile` no longer copies deleted folders.

---

## 5. New features

### 5.1 Cloud snapshot panel with visual refresh status
* Freshness badge: **FRESH** (green), **OVERDUE** (amber, snapshot older than
  14 h), **REFRESHING n/18** (blue, pulsing).
* Snapshot age, time of the next automatic refresh, progress bar.
* **18-cell field grid**: queued / checking / updated / unchanged /
  kept previous / failed for the current or latest run; click a cell to open
  that field. The job writes per-field states to
  `refresh-status/latest.json`; the API sanitises them before sending.
* The page polls every 5 s during a run and every 60 s otherwise, and reloads
  the views when a new snapshot is published (scheduled or manual).

### 5.2 Fresh all-sky from IRSA
* Previously, ticking "fresh from IRSA" made All-sky blank.
* Now the snapshot tiles appear immediately (dimmed, "WAITING"), and each
  field is fetched from IRSA through the new endpoint
  `/api/mosaic/tile/stream?target=…`, **six fields at a time**; each fresh
  tile replaces its snapshot tile as it arrives (green border).
* Leaving All-sky or opening a field pauses the run (the live service handles
  one request per instance); fields already fetched are kept.
* Live service `max_instance_count` raised from 1 to 6.

---

## 6. Speed and reliability work — and why no data is lost

| Measured (local server, real browser / real IRSA) | Before | After |
|---|---|---|
| Open page until field shows | 6.4 s | 2.3 s |
| Switch field (snapshot) | 4.5 s | 1.0 s |
| All-sky (snapshot) | 2.9 s | 0.06 s |
| Fresh IRSA check, field with no new images | 38–68 s | ~19 s |

What changed:

1. **Skip unchanged fields in fresh mode.** Every fresh request still asks
   IRSA *now* which images exist. If the list has the same fingerprint the
   12-hour job stored for that field, IRSA has nothing the snapshot has not
   already processed, so the published result is returned — labelled
   ("IRSA checked …: same N images the snapshot processed, so its full result
   (all epochs and bands, processed …) is shown"). Otherwise the full fresh
   fetch runs and reuses the same listing (no second IRSA query). The
   fingerprint function is now shared by the job and the API
   (`step1_ingest.source_fingerprint`); verified identical against the
   published snapshot for 5 fields.
2. **In-memory cache of immutable snapshot objects.** Objects named by the
   SHA-256 of their content can never change, so they are cached (LRU, 256 MB
   cap). `current.json`, the refresh status and the lock are never cached.
3. **Background warm-up** of all 18 fields when a snapshot is first seen.
4. **One GCS round trip per read** instead of two (and two instead of three
   for `current.json`).
5. **Parallel browser requests** (target list with refresh status; dashboard
   with field status).
6. **One IRSA listing per fresh request**, shared with the all-detector retry
   (Sagittarius A* previously queried IRSA twice).
7. **Reused HTTPS connections** for cutout downloads.
8. **Startup CPU boost** on both Cloud Run services (shorter cold starts).

**No data is compressed, dropped or rewritten.** Every FITS cutout is still
downloaded in full (IMAGE, FLAGS, VARIANCE, ZODI, PSF, WCS-WAVE), the pipeline
maths is unchanged, and published snapshots are untouched.

---

## 7. Investigated and deliberately not changed: smaller downloads

Profiling one fresh fetch (48 s) showed ~85 % is waiting on networks
(IRSA listing ~14 s, cutout downloads ~19 s); the science maths is under
1.5 s. Each 5.28 MB cutout is 93 % a PSF cube (121 × 101 × 101) that the
pipeline does not read. Leaving it out would make downloads ~20× smaller, but
IRSA's cutout service accepts only `center` and `size`, always includes the
PSF unmodified, and does not compress responses (tested with and without
`Accept-Encoding: gzip`). So the full files are still downloaded.

Sources consulted:

* [SPHEREx Cutout Capabilities (IRSA)](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/cutout_tool.html)
  — IRSA's official cutout service page; documents only `center` and `size`,
  with no option to select or exclude FITS extensions.
* [SPHEREx cutouts tutorial (IRSA)](https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts)
  — IRSA's guide to downloading cutouts.
* [SPHEREx PSF tutorial (IRSA)](https://caltech-ipac.github.io/irsa-tutorials/spherex-psf/)
  — IRSA's guide to the PSF cube included in every cutout.

**Possible future science upgrade (not in this commit):** the pipeline
currently assumes a round Gaussian PSF (σ = 1.4 px) in ZOGY, triage and
injection-recovery. Using the real PSF from these cubes could reduce
subtraction residuals around unchanged stars (fewer false detections) and make
injection-recovery more realistic. This is untested; IRSA notes the PSF
header currently mislabels the zone of each plane (planes themselves are
correct), so it needs care.

---

## 8. Running and deploying

* **Local:** `uvicorn app:app --reload --port 8000` (needs Google
  application-default credentials once: `gcloud auth application-default login`).
  `.\cloud\run-local.ps1` additionally enables the "Fetch all 18" button.
* **Tests:** `python -m pytest -q` (offline).
* **Cloud:** build the image with `cloud/cloudbuild.yaml`, set `image_tag` in
  `cloud/terraform/terraform.tfvars`, then
  `terraform apply -var-file=terraform.tfvars -var=deploy_runtime=true`
  (PowerShell: insert `--%` after `apply`). Image for this commit:
  `fast-fresh-20261108`.
* **Netlify:** deploy `site/` after the Cloud Run services are updated; the
  page calls the live service at `spherex-lens-live-…run.app`.
* Cost choice: services scale to zero (no always-on instance), so the first
  visitor after an idle period waits for a cold start of several seconds.

## 9. Known limitations

* Sagittarius A* has no detector band observed in two epochs in the current
  SPHEREx release, so it shows an explanation instead of a dashboard (correct
  behaviour; retried every 12 hours).
* The page overflows horizontally on phone-width screens (pre-existing).
* `export_static.py` still assumes local cache data and is obsolete with the
  cloud backend.
