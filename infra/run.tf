resource "google_cloud_run_v2_service" "worker" {
  name                = "plimsoll-worker"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL" # reachable by Cloud Tasks; IAM restricts invokers
  deletion_protection = var.deletion_protection

  template {
    service_account                  = google_service_account.worker.email
    timeout                          = "900s"
    max_instance_request_concurrency = 4

    scaling {
      min_instance_count = 0
      max_instance_count = var.worker_max_instances
    }

    containers {
      image   = local.image
      command = local.worker_command

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      startup_probe {
        period_seconds    = 1
        timeout_seconds   = 1
        failure_threshold = 60
        http_get {
          path = "/health"
        }
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_cloud_run_v2_service" "api" {
  name                = "plimsoll-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.deletion_protection

  template {
    service_account                  = google_service_account.api.email
    timeout                          = "300s"
    max_instance_request_concurrency = 40

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    containers {
      image = local.image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi" # rule ingestion runs in the plimsoll-ingest job, not in API requests
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = merge(local.common_env, {
          PLIMSOLL_WORKER_URL            = google_cloud_run_v2_service.worker.uri
          PLIMSOLL_TASKS_SERVICE_ACCOUNT = google_service_account.tasks.email
          PLIMSOLL_FIREBASE_WEB_API_KEY  = var.firebase_web_api_key
        })
        content {
          name  = env.key
          value = env.value
        }
      }

      # Probe every second: the default 10 s period made each cold start wait ~10 s for readiness.
      startup_probe {
        period_seconds    = 1
        timeout_seconds   = 1
        failure_threshold = 60
        http_get {
          path = "/health"
        }
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# Rule ingestion: one execution per uploaded CSV. A fresh container per run means memory cannot
# accumulate across ingests, and the task timeout is not bound by an HTTP request.
resource "google_cloud_run_v2_job" "ingest" {
  name                = local.ingest_job
  location            = var.region
  deletion_protection = var.deletion_protection

  template {
    task_count = 1

    template {
      service_account = google_service_account.worker.email
      timeout         = "3600s"
      max_retries     = var.ingest_max_retries

      containers {
        image   = local.image
        command = local.ingest_command

        resources {
          limits = {
            cpu    = "2"
            memory = var.ingest_memory
          }
        }

        dynamic "env" {
          for_each = local.common_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  depends_on = [google_project_service.enabled]
}
