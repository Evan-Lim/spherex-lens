# Commit notes

Two rounds of work. Round 2 (offline copy for students without stable
internet) is the newest and is described first. Round 1 is below it.

## Timeline

All times are Malaysia time (UTC+8).

| When | What | Status |
|---|---|---|
| 6–8 Nov 2026 | **Round 1:** Cloud snapshot backend, 12-hour IRSA refresh, refresh status panel, fresh all-sky from IRSA, speed work, deployment bug fixes, Vercel removed | Committed as `f55034d` (8 Nov, 02:36) |
| 8 Nov, 12:05 | Scheduled cloud refresh published a snapshot: 18/18 fields, 1 updated (SgrA*), 17 unchanged | Live |
| 8 Nov, afternoon | **Round 2** planned: offline copy, so the site works without internet | Plan approved |
| 8 Nov, afternoon | Round 2 built: snapshot index endpoint, service worker, offline layer, OFFLINE badge | Done |
| 8 Nov, 16:34 | Round 2 tested in Chrome on localhost against the real Cloud snapshot (see R2.5) | All checks passed; 63/63 tests pass |
| 8 Nov | Image `offline-copy-20261108` built in Cloud Build (import check passed) | Done |
| Next | `terraform apply` with `image_tag = "offline-copy-20261108"` | To do (operator) |
| Next | Netlify redeploy of `site/` | To do (teammates) |
| Next | Commit Round 2 | To do, after review |

---

# Round 2 — Offline copy of the Cloud snapshot

## R2.1 Why

Lab students often have no stable internet. Before this change, the site
could not even open without a connection. Now every visitor's browser keeps a
complete copy of the latest Cloud snapshot:

* **No internet:** the site opens from the saved copy and shows a grey
  **OFFLINE** badge with the snapshot's age.
* **Internet:** the saved copy shows instantly (no wait for a Cloud Run cold
  start). The browser checks for a newer snapshot and downloads only what
  changed.
* **Internet comes back while the page is open:** the page switches back to
  Cloud snapshot mode by itself within about 30 s (5 s in testing), with no
  reload.

## R2.2 How it works

1. **Website copy.** A service worker (`site/sw.js`) keeps the HTML, JS, CSS
   and fonts. It asks the network first, and uses the saved copy when the
   network is down or slower than 4 s.
2. **Snapshot index.** New endpoint `/api/snapshot-index` (about 2 KB) lists
   the SHA-256 name of every object in the current snapshot: the target list,
   the all-sky mosaic, and each field's dashboard and recovery grid.
3. **Only changed files are downloaded.** The browser compares those names
   with the ones it saved and downloads only the new ones. One changed field
   means one download (~0.4 MB), not 7 MB. Comparing names stays correct even
   after missing many 12-hour runs.
4. **Every download is checked.** Snapshot responses now carry an
   `X-Snapshot-Object` header (the per-field status carries
   `X-Snapshot-Manifest`). A file is saved only when that label matches the
   index and the body is complete JSON. If a new snapshot is published during
   a download, the browser notices and starts again.
5. **The switch to a new copy happens all at once.** New files are saved next
   to the old ones. The page switches to the new copy only when every file is
   present. Files the new copy no longer uses are deleted only after the
   switch.
6. **Connection check.** The page decides it is offline from a real request to
   the snapshot index, which waits up to 30 s so a cold start is not mistaken
   for "no internet". It does not rely on the browser's online flag, which is
   wrong on lab Wi-Fi without internet. While offline it retries every 30 s,
   and immediately when the browser reports a connection.
7. **Gentle on the API.** Background saving runs 3 downloads at once, with
   dashboard and recovery reads paced to about 17 per minute (the API limit is
   30 per minute per visitor). If a shared lab address hits the limit, saving
   waits and retries.

## R2.3 What is available offline

| Feature | Offline |
|---|---|
| All 18 fields (map, detections, charts, metrics) | Yes, from the saved copy |
| Recovery grid | Yes (saved scheduled result) |
| All-sky view (18 tiles) | Yes |
| Refresh panel and per-field grid | Yes, from the last status seen online |
| Fetch fresh from IRSA, Fetch all 18, Open any field | Disabled, with the reason shown; re-enabled automatically when the connection returns |

## R2.4 Files changed

| File | Change |
|---|---|
| `app.py` | `/api/snapshot-index` endpoint (rate-limited, never cached). The middleware adds `X-Snapshot-Object` / `X-Snapshot-Manifest` through a per-request holder, so every endpoint still returns the same body and internal callers are unaffected. CORS exposes both headers. |
| `site/sw.js` (new) | Service worker: website copy, snapshot reads served from the saved copy, labelled network responses saved by object name. Fresh IRSA requests, progress streams, the refresh trigger, the refresh status and the index are never stored. |
| `site/app.js` | Offline layer (check, compare, paced download, verify, switch, clean up, persistent storage request), offline mode switching, OFFLINE badge and labels, recovery read from the saved copy when offline, views refreshed only when their data changed. |
| `site/index.html`, `site/style.css` | Offline status line in the Cloud snapshot panel; grey OFFLINE badge; amber status pill when offline. |
| `netlify.toml` | `sw.js` is served with `Cache-Control: no-cache`, so students receive a new worker after each deploy. |
| `static/*` | Same files for local `uvicorn app:app`. |
| `test_cloud_snapshots.py` | New test that runs through the full ASGI stack: the index content, the header on each snapshot response, no header on live responses, and plain dicts still returned to internal callers. |
| `README.md` | Note with the deploy command. |

## R2.5 Verification (Chrome, localhost, real Cloud snapshot, 8 Nov)

| Test | Result |
|---|---|
| First visit saves the full copy | 18 fields, 56 files, 61 s in the background |
| Reopen with the network switched off | Page opens; field, recovery, all-sky (18 tiles) work; OFFLINE badge; IRSA controls disabled |
| Connection returns while the page is open | Back to Cloud snapshot mode in 5.1 s, nothing re-downloaded |
| Snapshot with 1 changed field | Exactly 1 file downloaded, 2.8 s |
| Saved copy compared with the server | Byte-for-byte identical |
| Connection lost after 2 of 6 updates | Old copy kept and complete; on reconnect only the 4 missing files downloaded, then switched and cleaned up |
| Browser console | No new errors |
| Automated tests | 63 passed, offline |

## R2.6 No data loss

* Responses are saved exactly as the server sent them. Nothing is compressed,
  trimmed or re-encoded.
* A copy is used only when it is complete. A failed or interrupted update
  never replaces a working copy.
* Each saved file is checked against its content-addressed (SHA-256) object
  name.
* The cloud side is unchanged: the same snapshots, the same 12-hour refresh,
  the same endpoint bodies.

## R2.7 Limits

* A student must open the site once while online before it can work offline.
* Storage is about 54 MB of browser storage per device.
* Safari and iPhone may delete the copy after about 7 days without a visit.
  Chrome and Edge keep it.
* Offline data is as new as the student's last online visit, and the page
  shows its age.
* A whole lab behind one internet address shares the API's per-visitor limit,
  so the first save for 30 students at once takes a few minutes. Later visits
  mostly read the saved copy.
* On the very first visit, the field on screen may be downloaded twice
  (~0.4 MB), because the service worker is not yet active for that first
  request.

## R2.8 Deploying Round 2

1. Image `offline-copy-20261108` is already built.
2. Set `image_tag = "offline-copy-20261108"` in
   `cloud/terraform/terraform.tfvars`, then from `cloud/terraform` run
   `terraform apply -var-file=terraform.tfvars -var=deploy_runtime=true`
   (in PowerShell, add `--%` after `apply`).
3. Check `https://spherex-lens-api-m6cqoypnxa-uc.a.run.app/api/snapshot-index`
   returns JSON that starts with `{"manifest":`.
4. Redeploy `site/` on Netlify.

Until step 2 is done, the deployed site behaves as before. Without the index
endpoint, the page simply does not save an offline copy.

---

# Round 1 — Cloud snapshot backend, IRSA refresh, and speed/reliability work

Committed as `f55034d` (8 Nov 2026, 02:36).

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
