"""In-memory implementations for local runs and tests. Semantics mirror the GCP adapters."""

import copy
import math
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sar_plimsoll.rules.csv_parser import RuleRow
from sar_plimsoll.storage.interfaces import RuleHit, SearchResult, StoredRule


class MemoryRulesRepository:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[RuleRow, list[float], str]] = {}
        self.search_calls = 0

    def ensure_schema(self, dimensions: int) -> None:
        pass

    def fingerprints(self) -> dict[str, tuple[str, str | None]]:
        return {rid: (row.content_hash, model) for rid, (row, _, model) in self._rows.items()}

    def upsert(self, rows: list[RuleRow], embeddings: dict[str, list[float]], model: str) -> None:
        for row in rows:
            self._rows[row.id] = (row, embeddings[row.id], model)

    def refresh_index(self) -> None:
        pass

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        found = self._rows.get(rule_id)
        return (
            {"id": rule_id, "type": found[0].type, "description": found[0].description}
            if found
            else None
        )

    def search(self, vectors: list[list[float]], k: int) -> SearchResult:
        self.search_calls += 1
        hits = []
        for query in vectors:
            scored = sorted(
                (
                    (_cosine_distance(query, emb), row.id, row)
                    for row, emb, _ in self._rows.values()
                ),
            )[:k]
            hits.append(
                [
                    RuleHit(StoredRule(r.id, r.type, r.dimension, r.description), dist)
                    for dist, _, r in scored
                ]
            )
        return SearchResult(hits=hits)


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 1.0 if na == 0 or nb == 0 else 1.0 - dot / (na * nb)


class MemoryReviewStore:
    def __init__(self) -> None:
        self._reviews: dict[tuple[str, str], dict[str, Any]] = {}
        self._cache: dict[str, dict[str, Any]] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._ingest_jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_review(self, uid: str, review: dict[str, Any]) -> None:
        with self._lock:
            self._reviews[(uid, review["id"])] = copy.deepcopy(review)

    def get_review(self, uid: str, review_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._reviews.get((uid, review_id))
            return copy.deepcopy(found) if found else None

    def update_review(self, uid: str, review_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            self._reviews[(uid, review_id)].update(copy.deepcopy(fields))

    def list_reviews(self, uid: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            mine = [copy.deepcopy(r) for (owner, _), r in self._reviews.items() if owner == uid]
        return sorted(mine, key=lambda r: r["created_at"], reverse=True)[:limit]

    def list_review_summaries(self, uid: str, limit: int) -> list[dict[str, Any]]:
        fields = ("id", "filename", "language", "status", "created_at", "score", "finding_counts")
        return [
            {k: r.get(k) for k in fields}
            | {
                "cost": {
                    k: (r.get("cost") or {}).get(k) for k in ("cost_usd", "cache_hit", "escalated")
                }
            }
            for r in self.list_reviews(uid, limit)
        ]

    def reserve_daily_review(self, uid: str, day: str, limit: int) -> bool:
        with self._lock:
            key = f"usage:{uid}:{day}"
            used = self._meta.get(key, {}).get("reviews", 0)
            if used >= limit:
                return False
            self._meta[key] = {"reviews": used + 1}
            return True

    def claim_review(self, uid: str, review_id: str, lease_s: int) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        with self._lock:
            review = self._reviews.get((uid, review_id))
            if review is None or not _claimable(review, now):
                return None
            review.update(
                status="running",
                attempts=review.get("attempts", 0) + 1,
                lease_expires_at=now + timedelta(seconds=lease_s),
            )
            return copy.deepcopy(review)

    def get_cache(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._cache.get(key)
            return copy.deepcopy(found) if found else None

    def put_cache(self, key: str, entry: dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = copy.deepcopy(entry)

    def get_meta(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._meta.get(name)
            return copy.deepcopy(found) if found else None

    def set_meta(self, name: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._meta[name] = copy.deepcopy(value)

    def create_ingest_job(self, job: dict[str, Any]) -> None:
        with self._lock:
            self._ingest_jobs[job["id"]] = copy.deepcopy(job)

    def get_ingest_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._ingest_jobs.get(job_id)
            return copy.deepcopy(found) if found else None

    def update_ingest_job(self, job_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            self._ingest_jobs[job_id].update(copy.deepcopy(fields))


def _claimable(review: dict[str, Any], now: datetime) -> bool:
    if review["status"] == "queued":
        return True
    lease = review.get("lease_expires_at")
    return review["status"] == "running" and lease is not None and lease < now


class MemoryBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put(self, path: str, data: bytes) -> None:
        self._blobs[path] = data

    def get(self, path: str) -> bytes:
        return self._blobs[path]


class MemoryAnalytics:
    """Analytics sink and queries over in-process rows; aggregation mirrors the warehouse SQL."""

    def __init__(self, rule_lookup=None) -> None:
        self.review_rows: list[dict[str, Any]] = []
        self.finding_rows: list[dict[str, Any]] = []
        self._rule_lookup = rule_lookup or (lambda rule_id: None)

    def write_review(self, row: dict[str, Any]) -> None:
        self.review_rows.append(copy.deepcopy(row))

    def write_findings(self, rows: list[dict[str, Any]]) -> None:
        self.finding_rows.extend(copy.deepcopy(rows))

    @staticmethod
    def _recent(rows, days: int):
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return [r for r in rows if datetime.fromisoformat(r["created_at"]) >= cutoff]

    def mode_aggregates(self, uid: str | None, days: int):
        from sar_plimsoll.analytics.stats import aggregate_rows

        traffic = [
            r
            for r in self._recent(self.review_rows, days)
            if r["mode"] == "tiered" and (uid is None or r["uid"] == uid)
        ]
        bench = [
            r for r in self.review_rows if r["mode"].startswith("benchmark") and r.get("run_id")
        ]
        latest = max(bench, key=lambda r: r["completed_at"])["run_id"] if bench else None
        return aggregate_rows(traffic + [r for r in bench if r["run_id"] == latest])

    def recurring(self, uid: str, days: int) -> dict[str, Any]:
        from sar_plimsoll.analytics.insights import build_recurring

        findings = [r for r in self._recent(self.finding_rows, days) if r["uid"] == uid]
        reviews = [
            r
            for r in self._recent(self.review_rows, days)
            if r["uid"] == uid and r["mode"] == "tiered" and r["status"] == "done"
        ]
        return build_recurring(findings, reviews, self._rule_lookup)
