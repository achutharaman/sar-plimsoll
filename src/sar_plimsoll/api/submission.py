"""Accept a submission: validate → cache lookup → daily cap → store source → enqueue.

No model calls happen here.
"""

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sar_plimsoll.config import Settings
from sar_plimsoll.llm.ledger import CostLedger
from sar_plimsoll.review.cache_key import compute_cache_key
from sar_plimsoll.review.languages import validate_submission
from sar_plimsoll.review.prompts import PROMPT_VERSION
from sar_plimsoll.rules.ingest import current_corpus
from sar_plimsoll.scoring.rubric import Rubric
from sar_plimsoll.storage.interfaces import BlobStore, ReviewStore, TaskQueue
from sar_plimsoll.worker.jobs import ReviewJobRunner, cost_record

log = logging.getLogger(__name__)

SubmitOutcome = Literal["cached", "queued"]


class EnqueueFailed(Exception):
    pass


class DailyLimitExceeded(Exception):
    pass


class ReviewNotFound(Exception):
    pass


class RetryNotAllowed(Exception):
    pass


def is_stalled(review: dict[str, Any], stall_minutes: int, now: datetime | None = None) -> bool:
    if review.get("status") not in ("queued", "running"):
        return False
    now = now or datetime.now(UTC)
    since = review.get("requeued_at") or review["created_at"]
    return since < now - timedelta(minutes=stall_minutes)


class SubmissionService:
    def __init__(
        self,
        *,
        store: ReviewStore,
        blobs: BlobStore,
        queue: TaskQueue,
        runner: ReviewJobRunner,
        rubric: Rubric,
        settings: Settings,
    ):
        self._store = store
        self._blobs = blobs
        self._queue = queue
        self._runner = runner
        self._rubric = rubric
        self._s = settings

    def submit(
        self, *, uid: str, filename: str, content: bytes, language: str | None, exempt: bool = False
    ) -> tuple[dict[str, Any], SubmitOutcome]:
        started = time.monotonic()
        source = validate_submission(
            filename=filename,
            content=content,
            declared_language=language,
            max_bytes=self._s.max_file_bytes,
            max_lines=self._s.max_file_lines,
        )
        corpus, corpus_size = current_corpus(self._store)
        cache_key = compute_cache_key(
            content_sha256=source.sha256,
            language=source.language,
            rubric_version=self._rubric.version,
            prompt_version=PROMPT_VERSION,
            triage_model=self._s.triage_model,
            escalation_model=self._s.escalation_model,
            rules_corpus_version=corpus,
            tenant=uid if self._s.cache_scope == "tenant" else None,
        )
        now = datetime.now(UTC)
        review: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "uid": uid,
            "filename": source.filename,
            "language": source.language,
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
            "line_count": source.line_count,
            "created_at": now,
            "cache_key": cache_key,
            "rubric_version": self._rubric.version,
            "prompt_version": PROMPT_VERSION,
            "rules_corpus_version": corpus,
            "rules_corpus_size": corpus_size,
            "models": {"triage": self._s.triage_model, "escalation": self._s.escalation_model},
            "attempts": 0,
            "generation": 0,
            "error": None,
        }

        cached = self._store.get_cache(cache_key)
        if cached is not None:
            wall_ms = int((time.monotonic() - started) * 1000)
            result = cached["result"]
            review |= {
                "status": "done",
                "result": result,
                "score": result["score"]["overall"],
                "cost": cost_record(CostLedger(), cache_hit=True, wall_ms=wall_ms, escalations=[])
                | {"source_review_id": cached.get("source_review_id")},
                "completed_at": datetime.now(UTC),
            }
            self._store.create_review(uid, review)
            self._runner.emit(
                review,
                status="done",
                result=result,
                ledger=CostLedger(),
                wall_ms=wall_ms,
                escalations=[],
                completed_at=review["completed_at"],
                cache_hit=True,
            )
            log.info("review cache hit", extra={"review_id": review["id"]})
            return review, "cached"

        # Only fresh reviews spend model tokens, so only they count against the daily cap.
        limit = self._s.daily_review_limit
        if (
            limit
            and not exempt
            and not self._store.reserve_daily_review(uid, now.date().isoformat(), limit)
        ):
            raise DailyLimitExceeded(f"daily limit of {limit} new reviews reached")

        # Tenant-scoped path; identical content from the same user reuses one object.
        review["source_path"] = f"sources/{uid}/{source.sha256}"
        review["status"] = "queued"
        self._blobs.put(review["source_path"], content)
        self._store.create_review(uid, review)
        self._enqueue(uid, review["id"], 0)
        return review, "queued"

    def retry(self, *, uid: str, review_id: str) -> dict[str, Any]:
        review = self._store.get_review(uid, review_id)
        if review is None:
            raise ReviewNotFound(review_id)
        if review["status"] != "failed" and not is_stalled(review, self._s.review_stall_minutes):
            raise RetryNotAllowed(
                f"review is {review['status']}; only failed or stalled reviews can be retried"
            )
        generation = review.get("generation", 0) + 1
        self._store.update_review(
            uid,
            review_id,
            {
                "status": "queued",
                "attempts": 0,
                "generation": generation,
                "error": None,
                "lease_expires_at": None,
                "requeued_at": datetime.now(UTC),
            },
        )
        self._enqueue(uid, review_id, generation)
        return self._store.get_review(uid, review_id) or review

    def _enqueue(self, uid: str, review_id: str, generation: int) -> None:
        try:
            self._queue.enqueue_review(uid, review_id, generation=generation)
        except Exception as exc:
            log.exception("enqueue failed", extra={"review_id": review_id})
            self._store.update_review(
                uid, review_id, {"status": "failed", "error": "could not enqueue review"}
            )
            raise EnqueueFailed(str(exc)) from exc
