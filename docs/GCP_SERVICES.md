# GCP Services

Inventory of every Google Cloud service sar-plimsoll uses, what it is for, and what exists in the
project. Update this file whenever a service is added or removed.

- **Last verified:** 2026-09-14 (full system deployed; dashboard, analytics and benchmark verified)
- **Terraform resources:** 39 — verify with `terraform -chdir=infra state list | wc -l`

---

## 1. Services at a glance

| # | Service | Used for | Managed by |
|---|---|---|---|
| 1 | Artifact Registry | Stores the container image shared by both Cloud Run services | Terraform |
| 2 | BigQuery | Rules corpus with vector search; `findings` and `reviews` analytics (stats, insights, benchmark) | Terraform (dataset), app (tables, index) |
| 3 | Cloud Build | Builds the container image remotely (`make build`) | Makefile |
| 4 | Cloud Logging | Structured JSON logs from both services, including per-call cost entries | Automatic |
| 5 | Cloud Run | Services: API (`plimsoll-api`), review worker (`plimsoll-worker`). Job: rule ingestion (`plimsoll-ingest`) | Terraform |
| 6 | Cloud Storage | Holds submitted source files, tenant-scoped, auto-deleted after 30 days | Terraform |
| 7 | Cloud Tasks | Asynchronous review queue with retries and backoff | Terraform |
| 8 | Firebase Hosting | Serves the dashboard; rewrites `/v1`, `/admin`, `/health` to the API (same origin, no CORS) | Manual link + `make web-deploy` |
| 9 | Firestore | Per-user review history, review cache, rules corpus version | Terraform |
| 10 | IAM | Service accounts and least-privilege role bindings | Terraform |
| 11 | Identity Platform (Firebase Authentication) | User sign-in; issues the ID tokens the API verifies | Manual |
| 12 | Service Usage | Enables the required APIs | Terraform |
| 13 | Vertex AI | Gemini models (triage + escalation) and text embeddings | Called at runtime |

---

## 2. What each service does in the review flow

```
User ──ID token──▶ Identity Platform
  │
  ▼
Firebase Hosting (dashboard) ──rewrite /v1, /admin──▶ Cloud Run: plimsoll-api ──▶ Firestore (cache lookup, review record)
  │                    ──▶ Cloud Storage (store source on cache miss)
  │                    ──▶ Cloud Tasks (enqueue review)
  ▼
Cloud Tasks ──OIDC──▶ Cloud Run: plimsoll-worker
                          ├─▶ Cloud Storage (read source)
                          ├─▶ Vertex AI (embed code chunks)
                          ├─▶ BigQuery (VECTOR_SEARCH rules)
                          ├─▶ Vertex AI (Gemini Flash → Gemini Pro on escalation)
                          ├─▶ Firestore (result, cost, cache entry)
                          └─▶ BigQuery (review + findings rows for analytics)

Dashboard ──▶ /v1/history (Firestore field mask) · /v1/stats, /v1/insights (BigQuery aggregates)

Admin ──▶ plimsoll-api /admin/rules:ingest ──▶ Cloud Storage (CSV) + Firestore (job) ──▶ 202
            └─ starts ──▶ Cloud Run Job: plimsoll-ingest
                              ├─▶ Vertex AI (gemini-embedding-001, parallel batches)
                              ├─▶ BigQuery (NDJSON load + MERGE every 2,000 rules, vector index)
                              └─▶ Firestore (progress, report, corpus version)
All services ──▶ Cloud Logging
```

---

## 3. Vertex AI models

| Role | Model ID | Endpoint location | Pricing basis (USD) |
|---|---|---|---|
| Triage (every review) | `gemini-3.8-flash` | `global` | $0.75 in / $3.75 out per 1M tokens until 2026-12-31, then $1.50 / $7.50 |
| Escalation (only when needed) | `gemini-3.1-pro-preview` | `global` | $2.00 in / $12.00 out per 1M tokens |
| Embeddings (rules + code chunks) | `gemini-embedding-001` (768-d, 250 texts/request) | `global` | $0.00015 per 1,000 input tokens |

Reasoning ("thinking") tokens are billed as output. Prices live in `config/pricing.yaml`.
All three model IDs were verified in the sandbox project on 2026-09-13.

### Vertex AI quotas that matter (sandbox, measured 2026-09-13)

| Quota | Limit | Impact |
|---|---|---|
| Regional `:predict` requests per base model (e.g. `text-embedding-005`) | 10 / min per region | Why the legacy embedding model was dropped: 30k rules would take 12+ min |
| Global `embedContent` requests, `gemini-embedding-001` | 100,000 / min | Bulk ingest at ~11,000 rules/min with 8 parallel batches |
| Global `embedContent` input tokens, `gemini-embedding-001` | 100M / min | Not a constraint |
| Global generateContent requests, Gemini 3.1 Pro | 250 / min | Upper bound on escalations per minute |
| Shared/dynamic capacity for Gemini 3.x on the global endpoint | Not a fixed per-project number | Occasional `429 Resource exhausted` seen during parallel real-file reviews; gateway retries, then Cloud Tasks redelivers |

Check current limits: `gcloud alpha services quota list --service=aiplatform.googleapis.com --consumer=projects/<PROJECT_ID>`.

---

## 4. Enabled APIs (11)

| # | API | Service name |
|---|---|---|
| 1 | Vertex AI API | `aiplatform.googleapis.com` |
| 2 | Artifact Registry API | `artifactregistry.googleapis.com` |
| 3 | BigQuery API | `bigquery.googleapis.com` |
| 4 | Cloud Build API | `cloudbuild.googleapis.com` |
| 5 | Cloud Tasks API | `cloudtasks.googleapis.com` |
| 6 | Cloud Firestore API | `firestore.googleapis.com` |
| 7 | Identity and Access Management API | `iam.googleapis.com` |
| 8 | Identity Toolkit API | `identitytoolkit.googleapis.com` |
| 9 | Cloud Run Admin API | `run.googleapis.com` |
| 10 | Cloud Storage API | `storage.googleapis.com` |
| 11 | Firebase Hosting API | `firebasehosting.googleapis.com` |

Enabled manually (outside Terraform): `firebase.googleapis.com`.

---

## 5. Terraform resources (39), sorted by service

| S.No | Service | Terraform resource | GCP name / detail |
|---|---|---|---|
| 1 | Artifact Registry | `google_artifact_registry_repository.images` | Docker repo `plimsoll` |
| 2 | BigQuery | `google_bigquery_dataset.plimsoll` | Dataset `plimsoll` (US) |
| 3 | BigQuery | `google_bigquery_dataset_iam_member.api_dataset` | api → dataEditor on `plimsoll` |
| 4 | BigQuery | `google_bigquery_dataset_iam_member.worker_dataset` | worker → dataEditor on `plimsoll` |
| 5 | Cloud Run | `google_cloud_run_v2_job.ingest` | Job `plimsoll-ingest` (2 CPU, 2 GiB, 3600 s, 2 retries) |
| 6 | Cloud Run | `google_cloud_run_v2_job_iam_member.api_runs_ingest` | api → jobsExecutorWithOverrides on `plimsoll-ingest` |
| 7 | Cloud Run | `google_cloud_run_v2_service.api` | `plimsoll-api` (public, 512 MiB, 300 s) |
| 8 | Cloud Run | `google_cloud_run_v2_service.worker` | `plimsoll-worker` (private, 1 GiB, 900 s) |
| 9 | Cloud Run | `google_cloud_run_v2_service_iam_member.public_api[0]` | allUsers → invoke `plimsoll-api` |
| 10 | Cloud Run | `google_cloud_run_v2_service_iam_member.tasks_invokes_worker` | tasks → invoke `plimsoll-worker` |
| 11 | Cloud Storage | `google_storage_bucket.sources` | `<PROJECT_ID>-plimsoll-sources` |
| 12 | Cloud Storage | `google_storage_bucket_iam_member.api_sources` | api → objectAdmin on bucket |
| 13 | Cloud Storage | `google_storage_bucket_iam_member.worker_sources` | worker → objectViewer on bucket |
| 14 | Cloud Tasks | `google_cloud_tasks_queue.reviews` | `plimsoll-reviews` |
| 15 | Firestore | `google_firestore_database.default` | `(default)`, Native mode, nam5 |
| 16 | IAM | `google_service_account.api` | `plimsoll-api@<PROJECT_ID>.iam.gserviceaccount.com` |
| 17 | IAM | `google_service_account.worker` | `plimsoll-worker@<PROJECT_ID>.iam.gserviceaccount.com` (also runs the ingest job) |
| 18 | IAM | `google_service_account.tasks` | `plimsoll-tasks@<PROJECT_ID>.iam.gserviceaccount.com` |
| 19 | IAM | `google_project_iam_member.api_roles["roles/aiplatform.user"]` | api → call Vertex AI |
| 20 | IAM | `google_project_iam_member.api_roles["roles/bigquery.jobUser"]` | api → run BigQuery jobs |
| 21 | IAM | `google_project_iam_member.api_roles["roles/cloudtasks.enqueuer"]` | api → create tasks |
| 22 | IAM | `google_project_iam_member.api_roles["roles/datastore.user"]` | api → read/write Firestore |
| 23 | IAM | `google_project_iam_member.api_roles["roles/logging.logWriter"]` | api → write logs |
| 24 | IAM | `google_project_iam_member.worker_roles["roles/aiplatform.user"]` | worker → call Vertex AI |
| 25 | IAM | `google_project_iam_member.worker_roles["roles/bigquery.jobUser"]` | worker → run BigQuery jobs |
| 26 | IAM | `google_project_iam_member.worker_roles["roles/datastore.user"]` | worker → read/write Firestore |
| 27 | IAM | `google_project_iam_member.worker_roles["roles/logging.logWriter"]` | worker → write logs |
| 28 | IAM | `google_service_account_iam_member.api_acts_as_tasks` | api → act as tasks account (signs task OIDC tokens) |
| 29 | Service Usage | `google_project_service.enabled["aiplatform.googleapis.com"]` | Vertex AI API |
| 30 | Service Usage | `google_project_service.enabled["artifactregistry.googleapis.com"]` | Artifact Registry API |
| 31 | Service Usage | `google_project_service.enabled["bigquery.googleapis.com"]` | BigQuery API |
| 32 | Service Usage | `google_project_service.enabled["cloudbuild.googleapis.com"]` | Cloud Build API |
| 33 | Service Usage | `google_project_service.enabled["cloudtasks.googleapis.com"]` | Cloud Tasks API |
| 34 | Service Usage | `google_project_service.enabled["firebasehosting.googleapis.com"]` | Firebase Hosting API |
| 35 | Service Usage | `google_project_service.enabled["firestore.googleapis.com"]` | Firestore API |
| 36 | Service Usage | `google_project_service.enabled["iam.googleapis.com"]` | IAM API |
| 37 | Service Usage | `google_project_service.enabled["identitytoolkit.googleapis.com"]` | Identity Toolkit API |
| 38 | Service Usage | `google_project_service.enabled["run.googleapis.com"]` | Cloud Run Admin API |
| 39 | Service Usage | `google_project_service.enabled["storage.googleapis.com"]` | Cloud Storage API |

A permission granted on one resource (bucket, dataset, Cloud Run service or job) is listed under
that resource's service; project-wide roles and the service accounts themselves are listed under IAM.

### Count by service

| Service | Count |
|---|---|
| Artifact Registry | 1 |
| BigQuery | 3 |
| Cloud Run | 6 |
| Cloud Storage | 3 |
| Cloud Tasks | 1 |
| Firestore | 1 |
| IAM | 13 |
| Service Usage | 11 |
| **Total** | **39** |

---

## 6. Created outside Terraform

| # | Service | Item | How it was created |
|---|---|---|---|
| 1 | Service Usage | `firebase.googleapis.com` enabled | `gcloud services enable` |
| 2 | Firebase | Firebase added to the project | `POST firebase.googleapis.com/v1beta1/projects/<PROJECT_ID>:addFirebase` |
| 3 | Identity Platform | Enabled, Email/Password provider on | Cloud Console |
| 4 | Identity Platform | Test user (UID is the API admin in `terraform.tfvars`) | Cloud Console |
| 5 | APIs & Services | Firebase browser API key (auto-created) — served to the dashboard via `/v1/config`, stored only in gitignored `terraform.tfvars` | Firebase |
| 6 | Identity Platform | `demo@plimsoll.test` user with `admin` custom claim (password in gitignored `.env`) | Firebase Admin SDK |
| 7 | Firebase Hosting | Default site `<PROJECT_ID>.web.app`, releases from `web/dist` | `make web-deploy` |

## 7. Created by the application at runtime

| Service | Item | Created when |
|---|---|---|
| BigQuery | Table `plimsoll.rules` | First `make rules-ingest` |
| BigQuery | Table `plimsoll.findings` (partitioned by day, clustered by uid, dimension) | First `make rules-ingest` |
| BigQuery | Vector index `rules_embedding_idx` (IVF, cosine) | First ingest; populated once the table is large enough |
| BigQuery | Temporary `_rules_staging_*` tables | During each ingest; deleted afterwards |
| Firestore | Collections `users/{uid}/reviews`, `review_cache`, `system` | First review / first ingest |
| Firestore | Collection `ingest_jobs/{job_id}` (status, progress, report) | Each `POST /admin/rules:ingest` |
| Firestore | Documents `users/{uid}/usage/{YYYY-MM-DD}` (daily fresh-review counter) | First fresh review of a user per day |
| BigQuery | Table `plimsoll.reviews` (one row per review, incl. cached, failed and benchmark runs) | First completed review (created on demand) |
| Cloud Storage | Objects `sources/{uid}/{sha256}` | First submission that misses the cache |
| Cloud Storage | Objects `ingest/{job_id}.csv` (deleted by the 30-day lifecycle rule) | Each `POST /admin/rules:ingest` |
| Cloud Run | Job executions of `plimsoll-ingest` | Each `POST /admin/rules:ingest` / `make rules-ingest-job` |
| Artifact Registry | Image `app:<timestamp>` | Each `make build` / `make deploy` |
| Cloud Build | Build records | Each `make build` / `make deploy` |

---

## 8. Seeing it in the Cloud Console

Sign in with the project account and confirm the project selector shows the right project.
Replace `<PROJECT_ID>` in the links.

| What | Console link | What to look for |
|---|---|---|
| Cloud Run | `https://console.cloud.google.com/run?project=<PROJECT_ID>` | **Services** tab: both services healthy; **Security**: api public, worker requires authentication; **Variables & Secrets**: `PLIMSOLL_*` settings |
| Cloud Run Jobs | `https://console.cloud.google.com/run/jobs?project=<PROJECT_ID>` | `plimsoll-ingest` → **Executions**: one per ingest, with status, retries, duration and logs |
| Cloud Tasks | `https://console.cloud.google.com/cloudtasks?project=<PROJECT_ID>` | Queue `plimsoll-reviews`; **Configuration** tab for retry and rate limits |
| Firestore | `https://console.cloud.google.com/firestore/databases?project=<PROJECT_ID>` | `(default)` → **Data** |
| Cloud Storage | `https://console.cloud.google.com/storage/browser?project=<PROJECT_ID>` | Sources bucket; **Configuration** (public access prevention), **Lifecycle** (30-day delete) |
| BigQuery | `https://console.cloud.google.com/bigquery?project=<PROJECT_ID>` | Explorer → `plimsoll` dataset → tables |
| Artifact Registry | `https://console.cloud.google.com/artifacts?project=<PROJECT_ID>` | Repo `plimsoll` → images |
| Cloud Build | `https://console.cloud.google.com/cloud-build/builds?project=<PROJECT_ID>` | Build history and logs |
| Service accounts | `https://console.cloud.google.com/iam-admin/serviceaccounts?project=<PROJECT_ID>` | The three `plimsoll-*` accounts |
| IAM bindings | `https://console.cloud.google.com/iam-admin/iam?project=<PROJECT_ID>` | Filter `plimsoll` to see each account's roles |
| Enabled APIs | `https://console.cloud.google.com/apis/dashboard?project=<PROJECT_ID>` | The enabled APIs and their traffic |
| Identity Platform | `https://console.cloud.google.com/customer-identity/users?project=<PROJECT_ID>` | Users and providers |
| Firebase | `https://console.firebase.google.com/project/<PROJECT_ID>/authentication/users` | Same users as Identity Platform |
| Vertex AI | `https://console.cloud.google.com/vertex-ai?project=<PROJECT_ID>` | Model usage metrics |
| Firebase Hosting | `https://console.firebase.google.com/project/<PROJECT_ID>/hosting/sites` | Release history of the dashboard |
| Logs | `https://console.cloud.google.com/logs/query?project=<PROJECT_ID>` | Query `resource.type="cloud_run_revision"`; search `llm_usage` for per-call cost |

### Terminal checks

```bash
terraform -chdir=infra state list | wc -l                  # 36
terraform -chdir=infra output                              # URLs, bucket, repo
curl -s "$(terraform -chdir=infra output -raw api_url)/health"
curl -s -o /dev/null -w "%{http_code}\n" "$(terraform -chdir=infra output -raw worker_url)"   # 403 = private
gcloud services list --enabled --project <PROJECT_ID>
```

---

## 9. Current deployment

Deployment-specific values are not stored in the repository. Read them from Terraform and gcloud:

```bash
gcloud config get-value project                 # project ID
terraform -chdir=infra output                   # api_url, worker_url, image_repository, sources_bucket
cat .last-image                                 # last deployed image tag
gcloud run jobs list --region us-central1       # plimsoll-ingest
```

| Item | Naming pattern |
|---|---|
| Region | `us-central1` (Terraform variable `region`) |
| API / worker | Cloud Run services `plimsoll-api`, `plimsoll-worker` |
| Ingest job | Cloud Run job `plimsoll-ingest` |
| Image repository | `<REGION>-docker.pkg.dev/<PROJECT_ID>/plimsoll` |
| Sources bucket | `<PROJECT_ID>-plimsoll-sources` |
| Dashboard | `https://<PROJECT_ID>.web.app` |

The environment can be recreated from scratch with `make bootstrap` plus the manual steps in section 6.
