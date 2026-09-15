# Least privilege: each identity gets only what its code path touches.

# API: create review records, store source, enqueue tasks, run admin ingestion (embeddings + BQ).
resource "google_project_iam_member" "api_roles" {
  for_each = toset([
    "roles/datastore.user",
    "roles/cloudtasks.enqueuer",
    "roles/aiplatform.user",
    "roles/bigquery.jobUser",
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "api_sources" {
  bucket = google_storage_bucket.sources.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_bigquery_dataset_iam_member" "api_dataset" {
  dataset_id = google_bigquery_dataset.plimsoll.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.api.email}"
}

# The API mints OIDC tokens as the tasks identity when it creates a task.
resource "google_service_account_iam_member" "api_acts_as_tasks" {
  service_account_id = google_service_account.tasks.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.api.email}"
}

# Worker: read source, call models, query rules, write reviews, cache and findings.
resource "google_project_iam_member" "worker_roles" {
  for_each = toset([
    "roles/datastore.user",
    "roles/aiplatform.user",
    "roles/bigquery.jobUser",
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_storage_bucket_iam_member" "worker_sources" {
  bucket = google_storage_bucket.sources.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_bigquery_dataset_iam_member" "worker_dataset" {
  dataset_id = google_bigquery_dataset.plimsoll.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.worker.email}"
}

# Only Cloud Tasks (via its service account) may invoke the worker.
resource "google_cloud_run_v2_service_iam_member" "tasks_invokes_worker" {
  name     = google_cloud_run_v2_service.worker.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.tasks.email}"
}

resource "google_cloud_run_v2_service_iam_member" "public_api" {
  count    = var.allow_public_api ? 1 : 0
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# The API may start ingest executions (with the job ID as an env override) on this one job only.
resource "google_cloud_run_v2_job_iam_member" "api_runs_ingest" {
  name     = google_cloud_run_v2_job.ingest.name
  location = var.region
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.api.email}"
}
