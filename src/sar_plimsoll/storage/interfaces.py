"""Storage ports. GCP adapters and in-memory fakes both implement these."""

from dataclasses import dataclass
from typing import Any, Protocol

from sar_plimsoll.analytics.stats import ModeAggregate
from sar_plimsoll.rules.csv_parser import RuleRow


class TransientStorageError(Exception):
    """Retryable storage failure (unavailable, deadline exceeded, contention)."""


@dataclass(frozen=True)
class StoredRule:
    id: str
    type: str
    dimension: str | None
    description: str


@dataclass(frozen=True)
class RuleHit:
    rule: StoredRule
    distance: float


@dataclass(frozen=True)
class SearchResult:
    hits: list[list[RuleHit]]  # one list per query vector, nearest first
    bytes_billed: int = 0


class RulesRepository(Protocol):
    def ensure_schema(self, dimensions: int) -> None: ...
    def fingerprints(self) -> dict[str, tuple[str, str | None]]:
        """rule id → (content_hash, embedding_model). A model mismatch means the vector is stale."""
        ...

    def upsert(
        self, rows: list[RuleRow], embeddings: dict[str, list[float]], model: str
    ) -> None: ...
    def refresh_index(self) -> None: ...
    def search(self, vectors: list[list[float]], k: int) -> SearchResult: ...


class ReviewStore(Protocol):
    """Tenant-scoped review records plus the review cache and small system metadata."""

    def create_review(self, uid: str, review: dict[str, Any]) -> None: ...
    def get_review(self, uid: str, review_id: str) -> dict[str, Any] | None: ...
    def update_review(self, uid: str, review_id: str, fields: dict[str, Any]) -> None: ...
    def list_reviews(self, uid: str, limit: int) -> list[dict[str, Any]]: ...
    def list_review_summaries(self, uid: str, limit: int) -> list[dict[str, Any]]:
        """Newest first; only id, filename, language, status, created_at, score and cost flags."""
        ...

    def reserve_daily_review(self, uid: str, day: str, limit: int) -> bool:
        """Atomically count one fresh review against the user's daily allowance."""
        ...

    def claim_review(self, uid: str, review_id: str, lease_s: int) -> dict[str, Any] | None:
        """Atomically move a queued (or lease-expired) review to running. None if not claimable."""
        ...

    def get_cache(self, key: str) -> dict[str, Any] | None: ...
    def put_cache(self, key: str, entry: dict[str, Any]) -> None: ...
    def get_meta(self, name: str) -> dict[str, Any] | None: ...
    def set_meta(self, name: str, value: dict[str, Any]) -> None: ...
    def create_ingest_job(self, job: dict[str, Any]) -> None: ...
    def get_ingest_job(self, job_id: str) -> dict[str, Any] | None: ...
    def update_ingest_job(self, job_id: str, fields: dict[str, Any]) -> None: ...


class BlobStore(Protocol):
    def put(self, path: str, data: bytes) -> None: ...
    def get(self, path: str) -> bytes: ...


class AnalyticsSink(Protocol):
    def write_review(self, row: dict[str, Any]) -> None: ...
    def write_findings(self, rows: list[dict[str, Any]]) -> None: ...


class AnalyticsQueries(Protocol):
    def mode_aggregates(self, uid: str | None, days: int) -> dict[str, ModeAggregate]:
        """Per-mode aggregates over completed reviews; uid=None means the whole system.
        Production traffic is limited to the window; benchmark modes come from the latest paired
        run only, whatever the uid or window."""
        ...

    def recurring(self, uid: str, days: int) -> dict[str, Any]: ...


class TaskQueue(Protocol):
    def enqueue_review(self, uid: str, review_id: str, generation: int = 0) -> None: ...


class IngestLauncher(Protocol):
    def launch(self, job_id: str) -> str | None:
        """Start processing an ingest job; returns an execution reference when there is one."""
        ...
