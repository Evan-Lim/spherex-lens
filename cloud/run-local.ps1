# Run the dashboard locally against the published Cloud snapshot.
#
#   .\cloud\run-local.ps1
#
# There is no local data cache: every field is read from the GCS snapshot
# bucket, which the Cloud Run job refreshes from IRSA every 12 hours.
# Needs Google application-default credentials on this machine
# (gcloud auth application-default login, done once).
$ErrorActionPreference = "Stop"

if (-not (Get-Command uvicorn -ErrorAction SilentlyContinue)) {
  throw "uvicorn is not available. Install the requirements first: pip install -r requirements.txt"
}

# Defaults match cloud/terraform; override any of them before running.
if (-not $env:GOOGLE_CLOUD_PROJECT) { $env:GOOGLE_CLOUD_PROJECT = "spherex-lens" }
if (-not $env:SPHEREX_SNAPSHOT_BUCKET) {
  $env:SPHEREX_SNAPSHOT_BUCKET = "$($env:GOOGLE_CLOUD_PROJECT)-spherex-snapshots"
}
# The local server forwards refresh status and the "Fetch all 18" trigger to
# the deployed snapshot API, which holds the operator token and job access.
if (-not $env:SPHEREX_REFRESH_API_URL) {
  $env:SPHEREX_REFRESH_API_URL = "https://spherex-lens-api-m6cqoypnxa-uc.a.run.app/api"
}
$env:SPHEREX_SNAPSHOT_READS = "true"

Set-Location (Join-Path $PSScriptRoot "..")
Write-Host "Reading the published Cloud snapshot from gs://$env:SPHEREX_SNAPSHOT_BUCKET"
Write-Host "Refresh status and all-18 refresh via $env:SPHEREX_REFRESH_API_URL"
Write-Host "Open http://127.0.0.1:8000"
& uvicorn app:app --reload --port 8000
exit $LASTEXITCODE
