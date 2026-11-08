# Commit notes

Four rounds of work, newest first: Round 4 (localhost works on any computer,
without a Google login), Round 3 (faster "Open any field" and a research
History on the device), Round 2 (offline copy of the Cloud snapshot), Round 1
(Cloud snapshot backend).

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
| 8 Nov, 16:55 | Round 2 committed (`b3816d4`, cache files removed in `f02a87f`) and merged with Evan-Lim's paper commits (`c6e2a3b`) | Pushed |
| 8 Nov, evening | "Open any field" found slow (56 s in the browser). Measured in the cloud: 88 s, of which **61 s was a failing VSX lookup** that also left VSX empty | Investigated |
| 8 Nov, evening | **Round 3** planned: parallel catalogues, VSX fix, compressed results, early wake-up, research History | Plan approved |
| 8 Nov, evening | Round 3 built and tested on localhost against real IRSA (see R3.5) | All checks passed; 66/66 tests pass |
| 8 Nov | Image `fast-field-20261108` built in Cloud Build (import check passed) | Done |
| 8 Nov, night | A teammate's clone failed on localhost: every request crashed with `DefaultCredentialsError` (500) because their laptop has no Google login for the private bucket | Investigated |
| 8 Nov, night | **Round 4** built: automatic fallback to the deployed snapshot API, clear errors instead of crashes (see R4) | Done; 68/68 tests pass |
| 8 Nov, night | Round 4 tested with a server that has no Google login at all | All snapshot endpoints work; fresh IRSA fetch works |
| 8 Nov, 19:01 | Rounds 3 and 4 committed and pushed (`3304c9e`) | Pushed |
| 8 Nov, night | Netlify site loads but cannot fetch Cloud data: `https://spherex-lens.netlify.app/live-api/targets` returns Netlify's 404 page, so the `/live-api` proxy rule is not active. The rules lived only in the root `netlify.toml`, which Netlify skips when `site/` is deployed on its own (drag-and-drop, or base directory `site`). CORS on both Cloud Run services was checked and already allows this origin. | Investigated |
| 8 Nov, night | Fix: `site/_redirects` and `site/_headers` carry the same rules inside the published folder, so they apply with every deployment method | Done; takes effect on the next Netlify deploy |
| Next | `terraform apply` with `image_tag = "fast-field-20261108"` (Rounds 2 and 3; Round 4 is only needed on laptops) | To do (operator) |
| Next | Netlify redeploy of `site/` | To do (teammates) |
| Next | Commit Rounds 3 and 4 | To do, after review |

---

# Round 4 — Localhost works on any computer

## R4.1 What was wrong

`uvicorn app:app` read the Cloud snapshot straight from the private GCS bucket.
That works only on a computer with a Google login that has access (the
project owner's laptop, after `gcloud auth application-default login`). On a
teammate's clone there is no such login, so every snapshot request crashed
with `DefaultCredentialsError` and a long 500 traceback. Netlify was not
affected: the browser there talks to the Cloud Run API, which has its own
service account.

## R4.2 What changed (`app.py`)

* **Automatic mode** (`SPHEREX_SNAPSHOT_SOURCE=auto`, the default). At start,
  one cheap read checks whether this computer may read the bucket.
  * Yes: read the bucket directly, exactly as before.
  * No (no login, expired login, or no permission): print one line, e.g.
    `No access to gs://spherex-lens-spherex-snapshots from this computer
    (DefaultCredentialsError). Reading the Cloud snapshot through
    https://spherex-lens-api-…run.app/api instead`. From then on, snapshot
    reads are forwarded to the deployed snapshot API.
  * `bucket` or `api` force a mode; `SPHEREX_SNAPSHOT_API` overrides the
    address.
* **What is forwarded:** target list, dashboards, field status, recovery
  grids, all-sky mosaic, refresh status, snapshot index, and the "Fetch all
  18" trigger (the operator token is passed through; a wrong token is still
  refused). The `X-Snapshot-Object` / `X-Snapshot-Manifest` labels are kept,
  so the offline copy (Round 2) works on these laptops too.
* **What stays on the laptop:** fresh IRSA fetches (`source=live`), custom
  fields and their recovery. They talk to IRSA directly, which needs no
  Google login.
* **No more crash.** A missing or rejected Google login returns one clear 503
  message. In automatic mode it also switches to the API. If the API cannot
  be reached, the message says to check the internet connection.

## R4.3 Verification

| Test | Result |
|---|---|
| Server started with no Google login (credential folder pointed at an empty directory) | Clear one-line message at start; snapshot API reachable |
| `targets`, `dashboard`, `status`, `recovery`, `mosaic`, `refresh-status`, `snapshot-index` | All HTTP 200 through the API (0.3–2.4 s) |
| "Fetch all 18" with a wrong token | Refused (401), as on the real API |
| Fresh IRSA fetch of Barnard's Star on that server | Works: 10 detections, 36 s from this PC |
| Server log | No tracebacks |
| New tests | Without a login, reads are forwarded with their labels, while live and custom-field requests are not; forced bucket mode without a login gives a clear 503 instead of a crash |
| All tests | 68 passed |

## R4.4 Notes

* Teammates need nothing beyond `pip install -r requirements.txt` and
  `uvicorn app:app`.
* The owner's laptop and Cloud Run are unchanged: they can read the bucket,
  so they keep reading it directly.
* Cost: teammates' snapshot reads are ordinary requests to the snapshot API,
  like Netlify visitors make.
* No new image is required for this round: it only changes how a laptop
  reaches the snapshot, and the API it uses is already deployed. It is
  included in the next image build anyway.

---

# Round 3 — Faster "Open any field" and research History

## R3.1 What was wrong

One real "Open any field" request (RA 2, Dec 2) on the deployed live service:

| Step | Time |
|---|---|
| Gaia DR3 (IRSA TAP) | 7.9 s |
| AllWISE (IRSA TAP) | 4.6 s |
| **VSX (VizieR)** | **61 s**: the CfA mirror returned no table, then the Strasbourg and Tokyo mirrors each timed out after 30 s (Google Cloud cannot reach them) |
| IRSA listing | 1.9 s, but only after all catalogues finished |
| 4 cutouts | 4.5 s |
| Science | 1.2 s |
| Result sent to the browser | 6.4 s: progress streams are not gzip-compressed, so the 2.3 MB result travelled raw |
| **Total** | **88 s** |

After the 61 s wait, **VSX was empty**: the page showed no variable-star
matches although the field holds 2 VSX stars. That was silent data loss. The
12-hour refresh job was not affected: it uses strict mode, which retries and
keeps the previous catalogue on failure.

Results of "Open any field" were also not remembered anywhere: after a reload,
or offline, a researcher could not get back to coordinates already studied.

## R3.2 Changes: speed and reliability (backend)

1. **Catalogues in parallel** (`step1_ingest.download_catalogs_with_status`).
   Gaia, AllWISE and VSX are queried at the same time, and for custom fields
   also at the same time as the IRSA listing and cutout downloads. The
   pipeline waits for them only at triage, which is the first step that uses
   them. Same queries and same fallback order as before.
2. **VSX fixed.** The first mirror gets a second try. Fallback mirrors give up
   after 10 s instead of 30 s. Each catalogue now reports `ok`, `none` (the
   archive answered: no sources) or `unavailable` (no archive answered). The
   dashboard carries this as `catalog_status`, and the page says "VSX
   unavailable" instead of showing 0 matches. The strict refresh job keeps its
   existing behaviour: it raises, so the previous catalogue is kept.
3. **Compressed results.** A client that asks with `?result=gzip` receives the
   final result gzip-compressed and base64-wrapped inside the progress stream:
   1.41 MB → 0.29 MB measured. The browser unpacks it to the same JSON. This
   applies to all four streams (dashboard, recovery, custom field, all-sky
   tile). Older clients that don't ask get it uncompressed, as before.
4. **One IRSA listing per custom field.** It is fetched once, in parallel with
   the catalogues, and reused for the cutouts.
5. **Quick re-check for known coordinates.** The custom-field response now
   includes the IRSA listing fingerprint. When the browser already holds a
   result for those coordinates, it sends the fingerprint (`known=`). If IRSA
   lists the same images, the server answers after the listing alone (no
   catalogues, no cutouts) with `unchanged: true`.
6. **Early wake-up.** Focusing the RA/Dec/name boxes or the IRSA toggle sends
   one cheap request to the live service, so a cold start (5.8 s measured)
   happens while the user types. No instance is kept running, so there is no
   extra cost.
7. **Clearer progress.** For example: "Catalogue Gaia DR3: 579 sources",
   "Catalogue VSX: 2 sources", "IRSA returned 348 images for this position",
   "Images ready; waiting for the catalogue lookups".

## R3.3 Changes: research History (frontend, IndexedDB)

* **Saved automatically, exactly as received:** every custom field, every
  fresh IRSA run of a registered field that produced new results, and every
  live recovery grid. Each entry stores name, RA/Dec, settings, time, summary
  and IRSA fingerprint. Payloads are stored separately from the list, so
  showing the list never loads megabytes.
* **History panel** in Target Control: newest first, search by name or by
  "RA, Dec" (within 0.1°), with **Open / Pin / Delete** on each entry.
* **Same coordinates (within 1″) open the saved result at once:** offline from
  History, and online too, followed by the quick IRSA re-check (R3.2.5). If
  IRSA has new images, the field is processed fresh and replaces the saved
  result.
* Share links (`?ra=…&dec=…`) work offline when the coordinates are in
  History.
* **Export / Import** to one JSON file (`spherexlens-history-YYYY-MM-DD.json`).
  On import, entries are checked, files that are not SPHERExLens History are
  rejected, and entries already saved are skipped.
* **Limit 200 MB** (about 80–150 results). When full, the oldest *unpinned*
  results are removed and the panel says so. Pinned results are never removed
  automatically.
* Offline, the RA/Dec inputs stay usable for coordinates saved in History. New
  coordinates show "offline, and these coordinates are not in History".

## R3.4 Files changed

| File | Change |
|---|---|
| `step1_ingest.py` | `download_catalogs_with_status` (parallel, per-catalogue status, VSX retry, short fallback timeouts); `download_catalogs` keeps its old signature and return value |
| `app.py` | Background catalogue lookup joined at triage; `catalog_status` in the dashboard; one shared IRSA listing; `known=` fingerprint check returning `unchanged`; `irsa_check` with the fingerprint in custom-field responses; `?result=gzip` on all four progress streams |
| `site/app.js` | History module and panel; History lookup in "Open any field"; result unpacking; live-service wake-up; catalogue summary and "unavailable" warning; shared recovery renderer |
| `site/index.html`, `site/style.css` | History section and styles |
| `static/*` | Same frontend for local uvicorn |
| `test_cloud_snapshots.py` | 3 new tests: catalogues truly run in parallel and report `ok` / `none` / `unavailable` without waiting on unreachable mirrors; an unchanged listing skips catalogues and cutouts; a compressed result unpacks to identical JSON |

## R3.5 Verification (8 Nov)

| Test | Result |
|---|---|
| Parallel catalogues vs each catalogue queried alone (RA 2, Dec 2) | Identical rows: Gaia 579, AllWISE 2,766, VSX 2 |
| Custom field on localhost, real IRSA | 30.7 s end to end. Catalogues finished at 5.5 s, hidden behind the image downloads, which are slow from this PC (~20 s; 4.5 s from the cloud) |
| Compressed result | 1.41 MB → 0.29 MB; unpacks to identical JSON |
| Detections | 8, same as the old cloud run; VSX now 2 instead of 0 |
| Same coordinates again (online) | Saved result on screen in 0.13 s; IRSA check "same 348 images" after 6.4 s; nothing reprocessed |
| Reload | History kept |
| Offline: share link, typed coordinates, search | Saved result opens; unknown coordinates get a clear message |
| Export → delete → Import | Data identical, fingerprint kept; a wrong file is rejected; a second import adds no duplicates |
| Storage limit (simulated 270 MB) | Oldest unpinned result removed; pinned result kept |
| Browser console | No errors |
| Automated tests | 66 passed |

Expected in the cloud after deployment, for a new field: about 10–15 s (an
estimate from the measured cloud steps: Gaia ~8 s now runs alongside the
listing and the 4.5 s of downloads). Measure it after `terraform apply`.

## R3.6 No data loss

* Catalogues: same queries, same rows (verified identical); VSX no longer goes
  silently empty, and a catalogue that cannot be reached is labelled as such.
* Images and science: unchanged (same listing, same cutouts, same pipeline).
* Compression is lossless (gzip); the unpacked JSON is identical.
* History stores the exact response received; Export/Import round-trips it
  unchanged.
* Limits: History lives in this browser on this device. Clearing site data
  removes it, so use Export as the backup. Raw FITS cutouts are not stored,
  so saved results can be viewed offline but not reprocessed.

## R3.7 Deploying Round 3

1. Image `fast-field-20261108` (contains Rounds 2 and 3).
2. Set `image_tag = "fast-field-20261108"` in
   `cloud/terraform/terraform.tfvars`, then from `cloud/terraform` run
   `terraform apply -var-file=terraform.tfvars -var=deploy_runtime=true`
   (in PowerShell, add `--%` after `apply`).
3. Redeploy `site/` on Netlify.

The new frontend also works against the old backend: it asks for compressed
results and sends `known=`, and an old backend simply ignores both.

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
