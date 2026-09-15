variable "project_id" {
  description = "GCP project to deploy into"
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Cloud Tasks, Artifact Registry and the bucket"
  type        = string
  default     = "us-central1"
}

variable "firestore_location" {
  description = "Firestore location (a region or multi-region such as nam5)"
  type        = string
  default     = "nam5"
}

variable "bigquery_location" {
  description = "BigQuery dataset location"
  type        = string
  default     = "US"
}

variable "image" {
  description = "Container image for both services. Empty uses Google's public placeholder so the first apply succeeds before any build."
  type        = string
  default     = ""
}

variable "triage_model" {
  type    = string
  default = "gemini-3.8-flash"
}

variable "escalation_model" {
  type    = string
  default = "gemini-3.1-pro-preview"
}

variable "embedding_model" {
  type    = string
  default = "gemini-embedding-001"
}

variable "embedding_location" {
  description = "Vertex location for embeddings. The global embedContent quota is far higher than regional :predict quotas."
  type        = string
  default     = "global"
}

variable "admin_uids" {
  description = "Firebase UIDs allowed to call /admin routes (in addition to the admin custom claim)"
  type        = list(string)
  default     = []
}

variable "allow_public_api" {
  description = "Grant allUsers run.invoker on the API (auth is enforced in-app with Firebase ID tokens). Some org policies forbid this."
  type        = bool
  default     = true
}

variable "ingest_max_retries" {
  description = "Cloud Run Job retries for rule ingestion; each retry resumes from committed checkpoints"
  type        = number
  default     = 2
}

variable "ingest_memory" {
  type    = string
  default = "2Gi"
}

variable "firebase_web_api_key" {
  description = "Firebase web API key served to the dashboard via /v1/config (public by design; kept out of the repo)"
  type        = string
  default     = ""
}

variable "api_min_instances" {
  description = "Set to 1 during a live demo to remove API cold starts (billed while idle)"
  type        = number
  default     = 0
}

variable "api_max_instances" {
  type    = number
  default = 3
}

variable "worker_max_instances" {
  type    = number
  default = 3
}

variable "queue_max_concurrent_dispatches" {
  description = "Upper bound on reviews running at once — also caps concurrent model spend"
  type        = number
  default     = 8
}

variable "source_retention_days" {
  description = "Submitted source is deleted from the bucket after this many days"
  type        = number
  default     = 30
}

variable "billing_account" {
  description = "Billing account ID for a budget alert. Leave empty to skip (sandboxes often cannot manage billing)."
  type        = string
  default     = ""
}

variable "budget_usd" {
  type    = number
  default = 250
}

variable "deletion_protection" {
  description = "Protect stateful resources from terraform destroy"
  type        = bool
  default     = false
}
