locals {
  placeholder_image = "us-docker.pkg.dev/cloudrun/container/hello"
  image             = var.image == "" ? local.placeholder_image : var.image
  # The placeholder has no uvicorn; only override the start command once the real image is deployed.
  worker_command = var.image == "" ? null : ["sh", "-c", "exec uvicorn sar_plimsoll.worker.app:app --host 0.0.0.0 --port $PORT --no-access-log"]
  ingest_command = var.image == "" ? null : ["python", "-m", "sar_plimsoll.worker.ingest_job"]
  ingest_job     = "plimsoll-ingest"

  services = [
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudtasks.googleapis.com",
    "firebasehosting.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "identitytoolkit.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
  ]
  bucket_name = "${var.project_id}-plimsoll-sources"
  dataset_id  = "plimsoll"
  queue_name  = "plimsoll-reviews"

  common_env = {
    PLIMSOLL_GCP_PROJECT        = var.project_id
    PLIMSOLL_GCP_REGION         = var.region
    PLIMSOLL_STORE_BACKEND      = "gcp"
    PLIMSOLL_LLM_BACKEND        = "vertex"
    PLIMSOLL_AUTH_MODE          = "firebase"
    PLIMSOLL_QUEUE_BACKEND      = "cloudtasks"
    PLIMSOLL_GCS_BUCKET         = local.bucket_name
    PLIMSOLL_BQ_DATASET         = local.dataset_id
    PLIMSOLL_TASKS_QUEUE        = local.queue_name
    PLIMSOLL_TRIAGE_MODEL       = var.triage_model
    PLIMSOLL_ESCALATION_MODEL   = var.escalation_model
    PLIMSOLL_EMBEDDING_MODEL    = var.embedding_model
    PLIMSOLL_EMBEDDING_LOCATION = var.embedding_location
    PLIMSOLL_ADMIN_UIDS         = jsonencode(var.admin_uids)
    PLIMSOLL_INGEST_BACKEND     = "cloudrun_job"
    PLIMSOLL_INGEST_JOB_NAME    = local.ingest_job
    PLIMSOLL_INGEST_MAX_RETRIES = tostring(var.ingest_max_retries)
  }
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------- identities
resource "google_service_account" "api" {
  account_id   = "plimsoll-api"
  display_name = "plimsoll API"
  depends_on   = [google_project_service.enabled]
}

resource "google_service_account" "worker" {
  account_id   = "plimsoll-worker"
  display_name = "plimsoll review worker"
  depends_on   = [google_project_service.enabled]
}

resource "google_service_account" "tasks" {
  account_id   = "plimsoll-tasks"
  display_name = "Cloud Tasks → worker invoker"
  depends_on   = [google_project_service.enabled]
}

# ---------------------------------------------------------------- storage
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "plimsoll"
  format        = "DOCKER"
  depends_on    = [google_project_service.enabled]

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
}

resource "google_storage_bucket" "sources" {
  name                        = local.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection

  lifecycle_rule {
    condition {
      age = var.source_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_firestore_database" "default" {
  name                    = "(default)"
  location_id             = var.firestore_location
  type                    = "FIRESTORE_NATIVE"
  deletion_policy         = var.deletion_protection ? "ABANDON" : "DELETE"
  delete_protection_state = var.deletion_protection ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"
  depends_on              = [google_project_service.enabled]
}

# Tables are created by the application (CREATE TABLE IF NOT EXISTS) so that rules ingestion
# works from a cold start; Terraform owns only the dataset and its access.
resource "google_bigquery_dataset" "plimsoll" {
  dataset_id                 = local.dataset_id
  location                   = var.bigquery_location
  delete_contents_on_destroy = !var.deletion_protection
  depends_on                 = [google_project_service.enabled]
}

resource "google_cloud_tasks_queue" "reviews" {
  name     = local.queue_name
  location = var.region

  rate_limits {
    max_concurrent_dispatches = var.queue_max_concurrent_dispatches
    max_dispatches_per_second = 5
  }

  retry_config {
    max_attempts  = 5
    min_backoff   = "5s"
    max_backoff   = "300s"
    max_doublings = 4
  }

  depends_on = [google_project_service.enabled]
}
