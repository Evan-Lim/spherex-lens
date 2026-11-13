# Phase 9 (v2): votes storage, uptime / failure alerting and an optional
# billing budget.  Everything here is additive; see "v2 changes" in
# cloud/README.md for the commands to apply it.

locals {
  votes_bucket = "${var.project_id}-spherex-votes"
  # Alert policies always exist (visible in the console); they only notify
  # when alert_email is set.
  alert_channels = google_monitoring_notification_channel.email[*].id
}

data "google_project" "this" {
  project_id = var.project_id
}

# ---- public votes ----------------------------------------------------------
# One immutable object per vote (spherexlens/votes.py).  The API may create
# and read objects but never overwrite or delete them.
resource "google_storage_bucket" "votes" {
  project                     = var.project_id
  name                        = local.votes_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  # Votes are never overwritten by the app; this only cleans up versions left
  # by manual edits or deletes.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }

  # Raw votes older than votes_retention_days are deleted (0 = keep forever).
  dynamic "lifecycle_rule" {
    for_each = var.votes_retention_days > 0 ? [1] : []
    content {
      condition {
        age            = var.votes_retention_days
        matches_prefix = ["votes/"]
      }
      action {
        type = "Delete"
      }
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_project_iam_custom_role" "votes_writer" {
  project     = var.project_id
  role_id     = "spherexVotesWriter"
  title       = "SPHEREx votes writer"
  description = "Append and read public votes; cannot overwrite or delete them."
  permissions = [
    "storage.buckets.get",
    "storage.objects.create",
    "storage.objects.get",
    "storage.objects.list",
  ]
}

resource "google_storage_bucket_iam_member" "api_votes" {
  bucket = google_storage_bucket.votes.name
  role   = google_project_iam_custom_role.votes_writer.name
  member = "serviceAccount:${google_service_account.api.email}"
}

# The ML training run (refresh worker image) reads votes (get + list).
resource "google_storage_bucket_iam_member" "worker_votes_read" {
  bucket = google_storage_bucket.votes.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

# ---- notification channel --------------------------------------------------
resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  project      = var.project_id
  display_name = "SPHERExLens alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.required]
}

# ---- uptime check on /api/health/live --------------------------------------
resource "google_monitoring_uptime_check_config" "api_live" {
  count        = var.deploy_runtime ? 1 : 0
  project      = var.project_id
  display_name = "spherex-lens-api /api/health/live"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path           = "/api/health/live"
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"
    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = trimprefix(google_cloud_run_v2_service.api[0].uri, "https://")
    }
  }

  content_matchers {
    content = "\"ok\":true"
    matcher = "CONTAINS_STRING"
  }

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "api_down" {
  count        = var.deploy_runtime ? 1 : 0
  project      = var.project_id
  display_name = "SPHERExLens API is down"
  combiner     = "OR"

  conditions {
    display_name = "Uptime check /api/health/live failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.api_live[0].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "600s"
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.project_id", "resource.label.host"]
      }
      trigger {
        count = 1
      }
    }
  }

  notification_channels = local.alert_channels

  documentation {
    content   = "The public snapshot API stopped answering /api/health/live. Check Cloud Run logs for spherex-lens-api."
    mime_type = "text/markdown"
  }
}

# ---- refresh-job failures --------------------------------------------------
resource "google_logging_metric" "refresh_job_errors" {
  project = var.project_id
  name    = "spherex_refresh_job_errors"
  filter  = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${local.job_name}\" AND severity>=ERROR"

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    display_name = "SPHERExLens refresh job error log entries"
  }

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "refresh_job_failed" {
  project      = var.project_id
  display_name = "SPHERExLens scheduled refresh failed"
  combiner     = "OR"

  conditions {
    display_name = "Error logs from the spherex-refresh Cloud Run Job"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.refresh_job_errors.name}\" AND resource.type=\"cloud_run_job\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_DELTA"
      }
      trigger {
        count = 1
      }
    }
  }

  alert_strategy {
    auto_close = "86400s"
  }

  notification_channels = local.alert_channels

  documentation {
    content   = "The 12-hour refresh job logged an error. The previous snapshot stays published. See Cloud Run > Jobs > spherex-refresh > Logs, and /api/refresh-status."
    mime_type = "text/markdown"
  }
}

# ---- optional billing budget -----------------------------------------------
# Budgets need the Billing Budgets API and, with user credentials, a quota
# project; the aliased provider supplies it only for this resource.
provider "google" {
  alias                 = "billing"
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

resource "google_project_service" "billing_budgets" {
  count              = var.budget_amount > 0 && var.billing_account != "" ? 1 : 0
  project            = var.project_id
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

resource "google_billing_budget" "monthly" {
  count           = var.budget_amount > 0 && var.billing_account != "" ? 1 : 0
  provider        = google.billing
  billing_account = var.billing_account
  display_name    = "SPHERExLens monthly budget"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      units = tostring(var.budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = local.alert_channels
    disable_default_iam_recipients   = false
  }

  depends_on = [google_project_service.billing_budgets]
}

# ---------------------------------------------------------------------------
# Phase 7: SPHEREx deep-field light curves as an on-demand Cloud Run Job.
# Downloads a time-spread sample of spherex_dr1_deep cutouts into /tmp
# (memory), measures the field's Gaia stars on every visit and publishes
# deep/<name>.json to the snapshot bucket, which /api/deep serves.
# Run it with:  gcloud run jobs execute spherex-deep --region us-central1
# (default: North Ecliptic Pole) or override --args, see cloud/README.md.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_job" "deep" {
  count               = var.deploy_runtime ? 1 : 0
  name                = "spherex-deep"
  project             = var.project_id
  location            = var.region
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.worker.email
      max_retries     = 0
      timeout         = "21600s"

      containers {
        image   = local.image
        command = ["python", "-m", "spherexlens.deep.run"]
        args = ["--name", var.deep_default_name,
          "--ra", tostring(var.deep_default_ra),
          "--dec", tostring(var.deep_default_dec),
        "--max-exposures", tostring(var.deep_max_exposures)]

        resources {
          limits = {
            cpu    = "2"
            memory = "4Gi"
          }
        }

        env {
          name  = "SPHEREX_SNAPSHOT_BUCKET"
          value = google_storage_bucket.snapshots.name
        }
        env {
          name  = "SPHEREX_CACHE"
          value = "/tmp/spherex-data"
        }
        env {
          name  = "SPHEREX_DEEP_DIR"
          value = "/tmp/deep"
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_storage_bucket_iam_member.worker_write,
  ]
}
