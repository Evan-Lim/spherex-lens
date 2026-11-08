output "snapshot_bucket" {
  value = google_storage_bucket.snapshots.name
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.images.name
}

output "api_url" {
  description = "Snapshot service URL (high-concurrency; proxy via /live-api)"
  value       = try(google_cloud_run_v2_service.api[0].uri, null)
}

output "live_api_url" {
  description = "Live-pipeline service URL (concurrency=1; proxy via /data-live-api)"
  value       = try(google_cloud_run_v2_service.live_api[0].uri, null)
}

output "refresh_job" {
  value = try(google_cloud_run_v2_job.refresh[0].name, null)
}
