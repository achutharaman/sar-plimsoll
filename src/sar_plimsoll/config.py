"""Runtime settings. Every value can be set with a PLIMSOLL_* environment variable."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sar_plimsoll.paths import project_root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLIMSOLL_", env_file=".env", extra="ignore")

    # Backends. "local" wiring runs fully offline; "gcp" uses managed services.
    store_backend: Literal["memory", "gcp"] = "memory"
    queue_backend: Literal["inline", "cloudtasks"] = "inline"
    llm_backend: Literal["fake", "vertex"] = "fake"
    auth_mode: Literal["dev", "firebase"] = "dev"

    gcp_project: str = ""
    gcp_region: str = "us-central1"
    # Gemini 3.x models and Gemini embeddings are served from the global endpoint. The global
    # embedContent quota is orders of magnitude above the regional :predict quota (10 req/min in
    # sandbox projects), which is what makes a 30k-row ingest feasible.
    vertex_location: str = "global"
    embedding_location: str = "global"

    # Model IDs — verify against Model Garden in the target project before deploying.
    triage_model: str = "gemini-3.8-flash"
    escalation_model: str = "gemini-3.1-pro-preview"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768
    embedding_batch_size: int = 250  # API maximum per request
    embedding_concurrency: int = 8
    ingest_checkpoint_rows: int = 2_000
    # Thinking tokens count against max_output_tokens. With default (high) thinking, Gemini 3.1 Pro
    # spent 7,860 of 8,192 tokens thinking and truncated its JSON (see DECISIONS.md).
    triage_thinking_level: str | None = "low"
    escalation_thinking_level: str | None = "medium"
    triage_max_output_tokens: int = 8192
    escalation_max_output_tokens: int = 16384

    llm_timeout_s: float = 90.0
    llm_max_attempts: int = 5

    rubric_path: Path = Field(default_factory=lambda: project_root() / "rubric" / "v1.yaml")
    pricing_path: Path = Field(default_factory=lambda: project_root() / "config" / "pricing.yaml")

    # Submission limits, enforced before any token is spent.
    max_file_bytes: int = 200_000
    max_file_lines: int = 5_000

    # Review shaping.
    batch_max_chars: int = 48_000
    rules_top_k: int = 5
    max_rules_per_batch: int = 15
    escalation_confidence_threshold: float = 0.6
    cache_scope: Literal["tenant", "global"] = "tenant"

    # GCP resources.
    gcs_bucket: str = ""
    bq_dataset: str = "plimsoll"
    tasks_queue: str = "plimsoll-reviews"
    worker_url: str = ""
    tasks_service_account: str = ""
    task_max_attempts: int = 5

    admin_uids: list[str] = Field(default_factory=list)
    # Cost guardrail: fresh (non-cached) reviews a user may start per UTC day. 0 disables.
    daily_review_limit: int = 100
    # A queued/running review older than this is reported as stalled and may be retried.
    review_stall_minutes: int = 20
    analytics_cache_s: int = 60
    # Public Firebase web config served to the dashboard (Firebase web API keys are not secrets,
    # but are kept out of the repo and the bundle).
    firebase_web_api_key: str = ""
    max_ingest_bytes: int = 25_000_000
    # Rule ingestion runs as a Cloud Run Job (fresh container per run) in GCP, in-process locally.
    ingest_backend: Literal["inline", "cloudrun_job"] = "inline"
    ingest_job_name: str = "plimsoll-ingest"
    ingest_max_retries: int = 2

    @model_validator(mode="after")
    def _no_dev_auth_in_cloud(self) -> "Settings":
        # K_SERVICE is set by Cloud Run. Dev auth trusts the bearer string, so it must never run there.
        if self.auth_mode == "dev" and os.environ.get("K_SERVICE"):
            raise ValueError("PLIMSOLL_AUTH_MODE=dev is not allowed on Cloud Run")
        if self.store_backend == "gcp" and not self.gcp_project:
            raise ValueError("PLIMSOLL_GCP_PROJECT is required when store_backend=gcp")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
