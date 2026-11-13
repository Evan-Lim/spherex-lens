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

output "votes_bucket" {
  value = google_storage_bucket.votes.name
}

output "uptime_check_id" {
  value = try(google_monitoring_uptime_check_config.api_live[0].uptime_check_id, null)
}

output "alert_notification_channel" {
  value = try(google_monitoring_notification_channel.email[0].id, null)
}

output "billing_budget" {
  value = try(google_billing_budget.monthly[0].name, null)
}
