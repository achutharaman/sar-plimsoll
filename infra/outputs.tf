output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "worker_url" {
  value = google_cloud_run_v2_service.worker.uri
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "sources_bucket" {
  value = google_storage_bucket.sources.name
}

output "local_env" {
  description = "Paste into .env to run the CLI (e.g. rules ingest) against this project"
  value       = <<-EOT
    PLIMSOLL_GCP_PROJECT=${var.project_id}
    PLIMSOLL_GCP_REGION=${var.region}
    PLIMSOLL_STORE_BACKEND=gcp
    PLIMSOLL_LLM_BACKEND=vertex
    PLIMSOLL_EMBEDDING_MODEL=${var.embedding_model}
    PLIMSOLL_EMBEDDING_LOCATION=${var.embedding_location}
    PLIMSOLL_GCS_BUCKET=${google_storage_bucket.sources.name}
    PLIMSOLL_BQ_DATASET=${google_bigquery_dataset.plimsoll.dataset_id}
  EOT
}
