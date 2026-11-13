# Google Cloud refresh backend

This deployment keeps the Netlify site static and uses Google Cloud for the
registered-field backend:

```text
Cloud Scheduler (12 hours, Asia/Kuala_Lumpur)
  -> authenticated Cloud Run Job
  -> fresh IRSA SIA2 checks; changed fields are ingested and processed
  -> private Cloud Storage objects; publish current.json only after validation
  -> public snapshot API with a token-protected operator refresh trigger
  -> Netlify /live-api proxy
```

The scheduled set is the 18 targets in `step1_ingest.TARGETS`. The all-sky
display is an overview of those registered fields, not complete sky coverage.
The **Open any field** control remains an on-demand IRSA request and is not
added to the scheduled snapshot.

## What is refreshed

- SIA2 listings are checked for every registered field each run. A selected
  product fingerprint determines whether its cutouts and dashboard need to be
  processed again.
- Gaia DR3, AllWISE and VSX cone data are refreshed on first ingest and then
  every seven days (configurable with `SPHEREX_CATALOG_REFRESH_DAYS`). An
  empty refresh does not replace a previously non-empty catalog.
- Strict scheduled VSX lookups retry a mirror once after an empty response;
  if all catalog mirrors remain unavailable, the refresh fails and preserves
  the last published snapshot rather than silently dropping variable-star
  matches.
- NEOWISE/AllWISE time-series queries run only when the target or detection
  positions change. A TAP failure fails the job instead of publishing a
  success-shaped empty result.
- Dashboard, recovery, mosaic and target-list objects are content-addressed.
  The worker validates the outputs before replacing `current.json`; generation
  preconditions and a refresh lock prevent partial or concurrent publication.
  A successful publication retains the current and previous 48 hours of
  manifests and referenced objects (long enough for in-flight API requests),
  then removes older unreferenced objects from this dedicated bucket. The
  dashboard's **Snapshot history** control walks the same retained
  `previous_manifest` chain through `/api/snapshot-history`, so it never lists a
  version whose dashboard was already garbage-collected. Increase
  `SPHEREX_PREVIOUS_SNAPSHOT_RETENTION_HOURS` (for example `336` for a week) to
  keep a longer playback history; the API and the refresh job must use the same
  value. `SPHEREX_SNAPSHOT_HISTORY_LIMIT` caps how many manifests are listed.

The first run is a full bootstrap of all fields and can be slow. Later runs
still check IRSA every 12 hours, but only changed fields incur cutout download
and pipeline work. This is asynchronous archive polling, not a 200 ms data
stream; IRSA availability and response time remain external dependencies.

## Provision and deploy

Use your own project and local Google authentication. Never put service-account
keys, billing credentials, or tokens in this repository or paste them into
chat.

1. Install the Google Cloud CLI and Terraform, create the new project, attach a
   billing account, and authenticate locally:

   ```powershell
   gcloud auth login
   gcloud auth application-default login
   gcloud config set project YOUR_PROJECT_ID
   ```

2. Copy `cloud/terraform/terraform.tfvars.example` to
   `cloud/terraform/terraform.tfvars` and replace the placeholders:

   ```hcl
   project_id     = "YOUR_PROJECT_ID"
   region         = "us-central1"
   netlify_origin = "https://YOUR_SITE.netlify.app"
   ```

3. Bootstrap the private bucket, Artifact Registry repository, APIs, service
   accounts, Secret Manager secret, and least-privilege roles. The runtime is
   disabled during this first apply because the image must exist before Cloud
   Run can deploy:

   ```powershell
   Set-Location .\cloud\terraform
   terraform init
   terraform apply -var-file=terraform.tfvars
   ```

   The apply creates the empty `spherex-refresh-operator-token` secret. Before
   deploying the runtime, open **Secret Manager →
   `spherex-refresh-operator-token` → Add new version** and add a high-entropy
   token. Keep the token out of Terraform variables/state, source control,
   URLs, and logs. Operators enter it in the dashboard only when requesting a
   refresh.

4. Build and push the one Docker image used by both the API and refresh job.
   Cloud Build uses the checked-in config to select `Dockerfile.cloud`:

   ```powershell
   gcloud builds submit --config cloud/cloudbuild.yaml --substitutions=_IMAGE=us-central1-docker.pkg.dev/YOUR_PROJECT_ID/spherex-lens/spherex-lens:latest .
   ```

   Run this command from the repository root. It uploads the build context to
   Cloud Build; do not include local credentials or unrelated large files in
   that context. If the build cannot push to Artifact Registry, grant the
   Cloud Build service account Artifact Registry Writer access to this
   repository, then retry.

5. After adding the Secret Manager version, deploy Cloud Run and the
   authenticated 12-hour Scheduler trigger:

   ```powershell
   Set-Location .\cloud\terraform
   terraform apply -var-file=terraform.tfvars -var="deploy_runtime=true"
   terraform output api_url
   ```

6. Connect Netlify to the Cloud Run URL. The `/live-api/*` rewrite in
   `netlify.toml` serves short snapshot reads. Regenerate the site with both
   API bases, then deploy `site/`:

   ```powershell
   python export_static.py --api-base /live-api --pipeline-api-base https://YOUR_CLOUD_RUN_URL/api
   ```

   Cached target, dashboard, recovery and mosaic requests use the snapshot API
   through Netlify. Fresh IRSA dashboard and recovery requests use the direct
   Cloud Run URL: Netlify proxy rewrites time out after 26 seconds, while an
   IRSA request can take longer. Set `netlify_origin` to the exact site origin
   (for example `https://example.netlify.app`, without a trailing slash) so
   Cloud Run's CORS allowlist permits that browser origin. CORS is not
   authentication. Snapshot reads are public; starting a refresh requires the
   operator token. The API service account can invoke only this refresh job and
   can read (but not write) snapshot objects.

7. Run the initial bootstrap and verify it before relying on the schedule:

   ```powershell
   gcloud run jobs execute spherex-refresh --region us-central1 --wait
   ```

   Check Cloud Run Job logs and verify the API routes return 18 targets and a
   populated mosaic. Until the first successful publication, snapshot reads
   intentionally return HTTP 503 instead of inventing or silently serving a
   successful cloud snapshot.

### Use the Cloud snapshot from localhost

The local FastAPI app can read the same private snapshot directly; it does not
download the snapshot into the repository or fall back to local images when a
Cloud snapshot is configured. Authenticate with Application Default
Credentials, grant your Google identity the bucket's `spherexSnapshotReader`
role, then start Uvicorn with the Cloud bucket configured:

```powershell
gcloud auth application-default login
$project = (gcloud config get-value project).Trim()
$account = (gcloud config get-value account).Trim()
gcloud storage buckets add-iam-policy-binding "gs://$project-spherex-snapshots" `
  --member="user:$account" `
  --role="projects/$project/roles/spherexSnapshotReader"
.\cloud\run-local.ps1
```

Set the project to the same project used by Terraform and make sure the
`gcloud` account and ADC login are the identity granted the reader role. The
script derives the bucket name, sets the snapshot environment, discovers the
deployed Cloud Run API URL, and starts the app from the repository root. The
local dashboard reads the snapshot directly with ADC. Its refresh status and
trigger are proxied over HTTPS to the Cloud API; the operator token is
forwarded only in the request header and is validated by that API. The local
app does not receive Cloud Run job permissions or store the token. If the
service uses a non-default name or region, set `SPHEREX_REFRESH_SERVICE` or
`SPHEREX_REFRESH_REGION` before running the script. A permission error means
the ADC identity lacks bucket read access. `503 No published snapshot` means
the refresh job has not successfully published one yet. Plain `uvicorn
app:app` does not set up Cloud snapshot access or the refresh proxy; use
`.\cloud\run-local.ps1`.

### Manually refresh all registered fields

In the deployed dashboard, or localhost started with `.\cloud\run-local.ps1`,
switch to **All-sky**, enter the Secret Manager operator token, and select
**Fetch fresh IRSA · all 18 fields**. This performs a new IRSA listing check
for every registered field, reprocesses changed fields, and publishes a new
Cloud snapshot after completion. Unchanged fields are retained; catalog and
NEOWISE/AllWISE refreshes continue to follow their separate policies. The
browser sends the token only in the `X-Refresh-Token` request header; it is
not stored in browser storage or added to the URL. The API validates it and
asynchronously starts the existing Cloud Run Job. A concurrent run is
rejected, and the worker's storage lock remains the final overlap guard.

Progress reports the current field and completed, updated, unchanged, and
failed counts. The map is still the previously published snapshot until the
worker validates and publishes the new manifest. On success, the dashboard
reloads the all-sky map and selected field. A failure before publication
leaves the previous manifest current; if post-publication cleanup fails, the
new validated snapshot is still current. The status endpoint never exposes
exception messages or the operator token.

Catalog freshness and NEOWISE/AllWISE history are shown separately for the
selected field. Catalogs follow the configured 7-day cadence; WISE history is
queried when detections change or its history is missing, not on every
12-hour run.

### Updating an existing deployment

Use a new image tag when deploying code changes. Reusing the same `latest` tag
may not cause Terraform to detect that the image contents changed:

```powershell
# From the repository root
$tag = "vsx-retry-20261107"
gcloud builds submit --config cloud/cloudbuild.yaml --substitutions="_IMAGE=us-central1-docker.pkg.dev/YOUR_PROJECT_ID/spherex-lens/spherex-lens:$tag" .
```

Set `image_tag = "vsx-retry-20261107"` in
`cloud/terraform/terraform.tfvars`, then run:

```powershell
Set-Location cloud\terraform
terraform apply -var-file=terraform.tfvars -var="deploy_runtime=true"
gcloud run jobs execute spherex-refresh --region us-central1 --wait
```

The Scheduler trigger uses an OAuth service account with permission to execute
only the refresh job. The API service account can read the private bucket but
cannot modify snapshots. The bucket enforces uniform access and public access
prevention. Only the API service is public; its published data is the
application's intended public output.

## v2 changes (Phase 9: production hardening)

What the v2 Terraform adds (all in `cloud/terraform/production.tf`, plus one
env var and two APIs in `main.tf`):

| Resource | Purpose |
|---|---|
| `google_storage_bucket.votes` (`<project>-spherex-votes`) | Public real/bogus votes. Private, uniform access, public access prevention, versioning; noncurrent versions deleted after 30 days; optional age limit (`votes_retention_days`). |
| `spherexVotesWriter` custom role -> API service account | `objects.create/get/list` only: the API can add votes but never overwrite or delete them. |
| `roles/storage.objectViewer` on the votes bucket -> worker | The ML training run can read votes. |
| `SPHEREX_VOTES_BUCKET` env on both Cloud Run services | Turns on GCS vote storage. Without it a production instance answers `POST /api/votes` with 503. |
| `google_monitoring_uptime_check_config.api_live` | Checks `https://<api>/api/health/live` every 5 minutes. |
| `google_monitoring_alert_policy.api_down` | Fires when that check fails for 10 minutes. |
| `google_logging_metric.refresh_job_errors` + `refresh_job_failed` policy | Counts `severity>=ERROR` log lines from the `spherex-refresh` job and alerts on any. |
| `google_monitoring_notification_channel.email` | Created only when `alert_email` is set; the policies exist either way. |
| `google_billing_budget.monthly` | Optional: created only when `budget_amount > 0` and `billing_account` is set. Alerts at 50/90/100 % and at a forecast of 100 %. **Budgets alert; they do not cap spend.** |

New variables (`terraform.tfvars`): `alert_email`, `billing_account`,
`budget_amount`, `votes_retention_days` (see `terraform.tfvars.example`).

New API routes served by the same image: `POST /api/votes`,
`GET /api/votes/queue`, `GET /api/candidates.csv`,
`GET /api/candidates.votable`, `GET /api/cone` (IVOA Simple Cone Search),
`GET /api/health`, `GET /api/health/live`, `GET /api/credits`,
`POST /api/jobs/field`, `GET /api/jobs/{id}`. Production limits (queue size,
timeouts, probe TTL, ...) are in `config.Production` and can be overridden with
`SPHEREX_<NAME>` environment variables (for example `SPHEREX_MAX_CONCURRENT_JOBS=2`).
The security review is in `docs/SECURITY_REVIEW.md`.

### Commands to apply v2 (run them yourself, from the repository root)

1. Enable the APIs used by the new resources. `terraform apply` also enables
   `logging` and `monitoring`; doing it first avoids a slow first apply.
   Add `billingbudgets` only if you will set a budget:

   ```powershell
   gcloud services enable logging.googleapis.com monitoring.googleapis.com
   gcloud services enable billingbudgets.googleapis.com   # only for the budget
   ```

2. Optional settings: add to `cloud/terraform/terraform.tfvars`:

   ```hcl
   alert_email     = "you@example.com"
   billing_account = "012345-6789AB-CDEF01"   # gcloud billing accounts list
   budget_amount   = 20
   ```

   A budget created with your user login needs the **Billing Account
   Administrator** (or Costs Manager) role on that billing account.

3. Build a new image with the v2 code (use a new tag; reusing `latest` may not
   redeploy):

   ```powershell
   $tag = "v2-prod-20261113"
   gcloud builds submit --config cloud/cloudbuild.yaml --substitutions="_IMAGE=us-central1-docker.pkg.dev/YOUR_PROJECT_ID/spherex-lens/spherex-lens:$tag" .
   ```

   Then set `image_tag = "v2-prod-20261113"` in `terraform.tfvars`.

4. Review and apply:

   ```powershell
   Set-Location cloud\terraform
   terraform init -upgrade
   terraform plan -var-file=terraform.tfvars -var="deploy_runtime=true" -out=v2.plan
   terraform apply v2.plan
   terraform output votes_bucket
   ```

   The plan should **add** the votes bucket, custom role, three bucket IAM
   bindings, the log metric, two alert policies, the uptime check (and the
   channel or budget if configured), and **update in place** both Cloud Run
   services (new env var and image). It should destroy nothing. If the plan
   shows a replacement or destroy, stop and check before applying.

5. Check the deployment:

   ```powershell
   $api = terraform output -raw api_url
   curl.exe "$api/api/health/live"
   curl.exe "$api/api/health"
   curl.exe "$api/api/credits"
   curl.exe "$api/api/cone?RA=133.78&DEC=-7.24&SR=0.2"
   ```

   If you set `alert_email`, confirm the address in the verification email
   Cloud Monitoring sends.

6. Netlify: no change is needed for the new GET routes, because `/live-api/*`
   already forwards everything under `/api/`. `POST /api/votes` also goes
   through the same proxy. Redeploy `site/` only when the frontend changes.

Known limits:
- The job queue (`/api/jobs/*`) keeps jobs in memory, so a job ID is known
  only to the instance that accepted it. On the scaled live service, either
  enable session affinity (`gcloud run services update spherex-lens-live
  --session-affinity --region us-central1`) or keep using the synchronous SSE
  route. `SPHEREX_JOBS_BACKEND=cloudrun` is a documented stub (HTTP 501).
- Rate limits are in memory and per instance (as are the existing slowapi
  limits). See `docs/SECURITY_REVIEW.md` S8 about forged client headers.

## Cost and exposure

This is **not guaranteed free**. A 12-hour trigger uses one Scheduler job, but
Cloud Run execution time, Cloud Storage operations and retained current data,
Artifact Registry, Cloud Build, logs, network transfer, and unrelated project
usage can exceed quotas or incur charges. Google Cloud budget alerts notify;
they are not hard spending caps. Set billing budgets/alerts in your project and
review actual usage after the initial bootstrap.

The Cloud Run API is public so the Netlify site can read published snapshots.
Its storage credentials are not exposed, but `/api/field` and live IRSA
endpoints can consume compute and upstream bandwidth. The manual refresh route
is additionally protected by the Secret Manager token; never place that token
in the static site or share it with untrusted users. The deployment caps the
API at one instance and one concurrent request; this bounds concurrency, not
total requests or total spend. Do not treat CORS as authentication. Add a
trusted rate-limiting/authentication layer before allowing untrusted callers
if that risk is unacceptable.

Cloud Storage is in `us-central1`, and the API and worker use the same region.
No cloud resource is created until you run Terraform with your own project.

## S8 signed Netlify proxy (stops forged visitor IPs)

Netlify signs every proxied `/live-api/*` and `/data-live-api/*` request with a
shared secret (`signed = "SPHEREX_PROXY_SECRET"` in `netlify.toml`); the API
trusts the visitor-IP header only when that signature verifies.

`site/_redirects` holds the same proxy rules **unsigned**, and Netlify applies
`_redirects` before `netlify.toml`.  For signing to take effect the proxy rules
must come from `netlify.toml`: remove the two proxy lines from
`site/_redirects` and deploy from the repository root (`netlify deploy --prod`,
which publishes `site/` per `[build] publish`).

1. Create the secret value once and store it in Secret Manager:

   ```powershell
   $b = New-Object byte[] 32
   [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
   $secret = [Convert]::ToBase64String($b)
   $secret | gcloud secrets versions add spherex-proxy-secret --data-file=- --project spherex-lens
   ```

   (`terraform apply` creates the empty secret first; run this after it.)

2. Netlify: Site configuration -> Environment variables -> add
   `SPHEREX_PROXY_SECRET` with the same value (scope: Builds, Functions,
   Runtime / all deploy contexts).  Then redeploy the site.

3. Check the Netlify side reaches the API signed:
   `curl.exe https://spherex-lens.netlify.app/live-api/health/live`
   shows `"proxy_signature": "not_configured"` until step 4.

4. Set `proxy_signing = true` in `terraform.tfvars`, `terraform plan` /
   `apply`.  Now the same curl must show `"proxy_signature": "valid"`; a direct
   call to the Cloud Run URL shows `"missing_or_invalid"`.

Rollback: `proxy_signing = false` and apply.

## Phase 7: deep-field light curves in the cloud

`terraform apply` (with `deploy_runtime=true`) creates the on-demand job
`spherex-deep`.  It samples `spherex_dr1_deep` visits over the whole year,
measures the field's Gaia stars on every visit, and publishes
`deep/<name>.json` to the snapshot bucket; `/api/deep` serves it.

```powershell
# default: North Ecliptic Pole, 200 visits
gcloud run jobs execute spherex-deep --region us-central1 --project spherex-lens

# South Ecliptic Pole (or any position inside the deep footprint)
gcloud run jobs execute spherex-deep --region us-central1 --project spherex-lens `
  --args="--name,SEP,--ra,90.0,--dec,-66.56,--max-exposures,200"

# follow it
gcloud run jobs executions list --job spherex-deep --region us-central1 --project spherex-lens
```

Cost: a 200-visit run is one task of 2 vCPU / 4 GiB for roughly 1-3 hours
(mostly waiting on IRSA's cutout service), with no persistent storage beyond
one small JSON file.  Nothing is scheduled; it runs only when executed.
