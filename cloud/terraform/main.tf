terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  api_service_name  = "spherex-lens-api"
  live_service_name = "spherex-lens-live"
  job_name          = "spherex-refresh"
  # Cloud Scheduler runs "0 */12 * * *" in Asia/Kuala_Lumpur (UTC+8, no DST),
  # i.e. 04:00 and 16:00 UTC.  The API reports the next run from these hours;
  # change both together.
  refresh_cron           = "0 */12 * * *"
  refresh_time_zone      = "Asia/Kuala_Lumpur"
  refresh_schedule_utc_h = "4,16"
  snapshot_bucket        = "${var.project_id}-spherex-snapshots"
  image                  = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repository}/spherex-lens:${var.image_tag}"
}

resource "google_project_service" "required" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.repository
  description   = "SPHERExLens Cloud Run images"
  format        = "DOCKER"

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket" "snapshots" {
  project                     = var.project_id
  name                        = local.snapshot_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  depends_on = [google_project_service.required]
}

resource "google_project_iam_custom_role" "snapshot_reader" {
  project     = var.project_id
  role_id     = "spherexSnapshotReader"
  title       = "SPHEREx snapshot reader"
  description = "Read published SPHERExLens snapshots from the private bucket."
  permissions = [
    "storage.buckets.get",
    "storage.objects.get",
  ]
}

resource "google_project_iam_custom_role" "snapshot_writer" {
  project     = var.project_id
  role_id     = "spherexSnapshotWriter"
  title       = "SPHEREx snapshot writer"
  description = "Publish and garbage-collect SPHERExLens snapshots."
  permissions = [
    "storage.buckets.get",
    "storage.objects.create",
    "storage.objects.delete",
    "storage.objects.get",
    "storage.objects.list",
    "storage.objects.update",
  ]
}

resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "spherex-api"
  display_name = "SPHERExLens snapshot API"
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "spherex-refresh"
  display_name = "SPHERExLens scheduled refresh worker"
}

resource "google_secret_manager_secret" "refresh_operator_token" {
  project   = var.project_id
  secret_id = "spherex-refresh-operator-token"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "api_token_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.refresh_operator_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "api_read" {
  bucket = google_storage_bucket.snapshots.name
  role   = google_project_iam_custom_role.snapshot_reader.name
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "worker_write" {
  bucket = google_storage_bucket.snapshots.name
  role   = google_project_iam_custom_role.snapshot_writer.name
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_cloud_run_v2_service" "api" {
  count               = var.deploy_runtime ? 1 : 0
  name                = local.api_service_name
  project             = var.project_id
  location            = var.region
  deletion_protection = false

  # B4 FIX: snapshot reads are fast GCS fetches; high concurrency keeps them
  # responsive while a live pipeline is running on the live service.
  template {
    service_account                  = google_service_account.api.email
    max_instance_request_concurrency = 80
    timeout                          = "30s"

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      image = local.image
      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        # Extra CPU while a new instance starts (Python + astropy imports),
        # shortening the cold start a visitor waits through.
        startup_cpu_boost = true
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
        name  = "SPHEREX_CORS_ORIGINS"
        value = trim(var.netlify_origin, "/")
      }
      env {
        name  = "SPHEREX_VOTES_BUCKET"
        value = google_storage_bucket.votes.name
      }
      env {
        name  = "SPHEREX_FIELD_REQUEST_CONCURRENCY"
        value = "1"
      }
      env {
        name  = "SPHEREX_REFRESH_PROJECT"
        value = var.project_id
      }
      env {
        name  = "SPHEREX_REFRESH_REGION"
        value = var.region
      }
      env {
        name  = "SPHEREX_REFRESH_JOB"
        value = local.job_name
      }
      env {
        name  = "SPHEREX_REFRESH_SCHEDULE_UTC_HOURS"
        value = local.refresh_schedule_utc_h
      }
      env {
        name = "SPHEREX_REFRESH_OPERATOR_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.refresh_operator_token.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_storage_bucket_iam_member.api_read,
    google_storage_bucket_iam_member.api_votes,
    google_secret_manager_secret_iam_member.api_token_access,
    google_cloud_run_v2_job_iam_member.api_invoke,
  ]
}

# B4 FIX: separate live-pipeline service with concurrency=1 so long IRSA
# fetches never block fast snapshot reads.  Netlify proxies /data-live-api/*
# to this service for the refresh trigger; SSE streams use the direct URL.
resource "google_cloud_run_v2_service" "live_api" {
  count               = var.deploy_runtime ? 1 : 0
  name                = local.live_service_name
  project             = var.project_id
  location            = var.region
  deletion_protection = false

  template {
    service_account                  = google_service_account.api.email
    max_instance_request_concurrency = 1
    timeout                          = "900s"

    scaling {
      min_instance_count = 0
      # One IRSA field per instance; the fresh all-sky view fetches up to
      # 6 fields at once (LIVE_SKY_PARALLEL in site/app.js).
      max_instance_count = 6
    }

    containers {
      image = local.image
      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        # Extra CPU while a new instance starts (Python + astropy imports),
        # shortening the cold start a visitor waits through.
        startup_cpu_boost = true
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
        name  = "SPHEREX_CORS_ORIGINS"
        value = trim(var.netlify_origin, "/")
      }
      env {
        name  = "SPHEREX_VOTES_BUCKET"
        value = google_storage_bucket.votes.name
      }
      env {
        name  = "SPHEREX_FIELD_REQUEST_CONCURRENCY"
        value = "1"
      }
      env {
        name  = "SPHEREX_REFRESH_PROJECT"
        value = var.project_id
      }
      env {
        name  = "SPHEREX_REFRESH_REGION"
        value = var.region
      }
      env {
        name  = "SPHEREX_REFRESH_JOB"
        value = local.job_name
      }
      env {
        name  = "SPHEREX_REFRESH_SCHEDULE_UTC_HOURS"
        value = local.refresh_schedule_utc_h
      }
      env {
        name = "SPHEREX_REFRESH_OPERATOR_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.refresh_operator_token.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_storage_bucket_iam_member.api_read,
    google_storage_bucket_iam_member.api_votes,
    google_secret_manager_secret_iam_member.api_token_access,
    google_cloud_run_v2_job_iam_member.api_invoke,
  ]
}

resource "google_cloud_run_v2_job" "refresh" {
  count               = var.deploy_runtime ? 1 : 0
  name                = local.job_name
  project             = var.project_id
  location            = var.region
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.worker.email
      max_retries     = 1
      timeout         = "86400s"

      containers {
        image   = local.image
        command = ["python"]
        args    = ["cloud_refresh.py"]

        resources {
          limits = {
            cpu    = "1"
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
          name  = "SPHEREX_CATALOG_REFRESH_DAYS"
          value = "7"
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_storage_bucket_iam_member.worker_write,
  ]
}

resource "google_service_account" "scheduler" {
  count        = var.deploy_runtime ? 1 : 0
  project      = var.project_id
  account_id   = "spherex-scheduler"
  display_name = "SPHERExLens Cloud Scheduler invoker"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoke" {
  count    = var.deploy_runtime ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.refresh[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler[0].email}"
}

resource "google_cloud_run_v2_job_iam_member" "api_invoke" {
  count    = var.deploy_runtime ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.refresh[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.api.email}"
}

resource "google_cloud_scheduler_job" "refresh" {
  count       = var.deploy_runtime ? 1 : 0
  project     = var.project_id
  region      = var.region
  name        = "spherex-refresh-every-12-hours"
  description = "Refresh registered SPHEREx fields and publish a complete snapshot."
  schedule    = local.refresh_cron
  time_zone   = local.refresh_time_zone

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.refresh[0].name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler[0].email
    }
  }

  retry_config {
    retry_count = 1
  }

  depends_on = [
    google_project_service.required,
    google_cloud_run_v2_job_iam_member.scheduler_invoke,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_api" {
  count    = var.deploy_runtime ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "public_live_api" {
  count    = var.deploy_runtime ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.live_api[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
