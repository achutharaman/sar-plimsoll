"""Process one queued review: claim → load source → orchestrate → persist → cache → analytics."""

import logging
import time
from datetime import UTC, datetime
from typing import Literal

from sar_plimsoll.analytics.records import finding_counts, finding_rows, review_row
from sar_plimsoll.config import Settings
from sar_plimsoll.llm.backends.base import TransientLLMError
from sar_plimsoll.llm.ledger import CostLedger
from sar_plimsoll.llm.pricing import PriceTable
from sar_plimsoll.review.languages import SubmissionError, validate_submission
from sar_plimsoll.storage.interfaces import (
    AnalyticsSink,
    BlobStore,
    ReviewStore,
    TransientStorageError,
)
from sar_plimsoll.worker.orchestrator import ReviewFailed, ReviewOrchestrator

log = logging.getLogger(__name__)

JobOutcome = Literal["done", "skipped", "retry", "failed"]
LEASE_SECONDS = 900


class ReviewJobRunner:
    def __init__(
        self,
        *,
        store: ReviewStore,
        blobs: BlobStore,
        analytics: AnalyticsSink,
        orchestrator: ReviewOrchestrator,
        prices: PriceTable,
        settings: Settings,
    ):
        self._store = store
        self._blobs = blobs
        self._analytics = analytics
        self._orchestrator = orchestrator
        self._prices = prices
        self._s = settings

    def process(self, uid: str, review_id: str) -> JobOutcome:
        review = self._store.claim_review(uid, review_id, LEASE_SECONDS)
        if review is None:
            # Already done, running elsewhere, or unknown: Cloud Tasks delivers at least once.
            return "skipped"

        started = time.monotonic()
        ctx = {"review_id": review_id, "attempt": review["attempts"]}
        ledger = CostLedger()
        try:
            content = self._blobs.get(review["source_path"])
            source = validate_submission(
                filename=review["filename"],
                content=content,
                declared_language=review["language"],
                max_bytes=self._s.max_file_bytes,
                max_lines=self._s.max_file_lines,
            )
            outcome = self._orchestrator.run(
                source, ledger=ledger, corpus_version=review["rules_corpus_version"]
            )
        except (TransientLLMError, TransientStorageError) as exc:
            return self._retry_or_fail(uid, review, ledger, f"transient: {type(exc).__name__}", ctx)
        except (ReviewFailed, SubmissionError) as exc:
            self._fail(uid, review, ledger, str(exc), started)
            log.warning("review failed", extra=ctx | {"error": str(exc)})
            return "failed"
        except Exception as exc:
            log.exception("review crashed", extra=ctx)
            return self._retry_or_fail(uid, review, ledger, f"internal: {type(exc).__name__}", ctx)

        wall_ms = int((time.monotonic() - started) * 1000)
        result = outcome.to_record()
        cost = cost_record(
            ledger, cache_hit=False, wall_ms=wall_ms, escalations=outcome.escalations
        )
        now = datetime.now(UTC)
        self._store.update_review(
            uid,
            review_id,
            {
                "status": "done",
                "result": result,
                "cost": cost,
                # Top-level copies let history queries read a field mask instead of whole results.
                "score": result["score"]["overall"],
                "finding_counts": finding_counts(result["findings"]),
                "completed_at": now,
                "lease_expires_at": None,
                "error": None,
            },
        )
        self._store.put_cache(
            review["cache_key"],
            {"result": result, "created_at": now, "source_review_id": review_id},
        )
        self.emit(
            review,
            status="done",
            result=result,
            ledger=ledger,
            wall_ms=wall_ms,
            escalations=outcome.escalations,
            completed_at=now,
        )
        log.info(
            "review done", extra=ctx | {"cost": {k: v for k, v in cost.items() if k != "entries"}}
        )
        return "done"

    def emit(
        self,
        review: dict,
        *,
        status: str,
        result: dict | None,
        ledger: CostLedger,
        wall_ms: int,
        escalations: list[dict],
        completed_at: datetime,
        mode: str = "tiered",
        cache_hit: bool = False,
        run_id: str | None = None,
    ) -> None:
        try:
            self._analytics.write_review(
                review_row(
                    review=review,
                    status=status,
                    mode=mode,
                    completed_at=completed_at,
                    result=result,
                    cache_hit=cache_hit,
                    wall_ms=wall_ms,
                    escalations=escalations,
                    entries=ledger.entries,
                    prices=self._prices,
                    run_id=run_id,
                )
            )
            if result and not cache_hit:
                self._analytics.write_findings(
                    finding_rows(review, result["findings"], completed_at)
                )
        except Exception:
            # Analytics must never fail a review that the user has already been charged for.
            log.exception("analytics write failed", extra={"review_id": review["id"]})

    def _retry_or_fail(self, uid, review, ledger, error, ctx) -> JobOutcome:
        if review["attempts"] >= self._s.task_max_attempts:
            self._fail(uid, review, ledger, error, None)
            return "failed"
        self._store.update_review(
            uid, review["id"], {"status": "queued", "lease_expires_at": None, "error": error}
        )
        log.warning("review will retry", extra=ctx | {"error": error})
        return "retry"

    def _fail(self, uid: str, review: dict, ledger: CostLedger, error: str, started) -> None:
        now = datetime.now(UTC)
        wall_ms = int((time.monotonic() - started) * 1000) if started else 0
        self._store.update_review(
            uid,
            review["id"],
            {
                "status": "failed",
                "error": error,
                "completed_at": now,
                "lease_expires_at": None,
                # Failed reviews still spent money; keep that visible in cost reporting.
                "cost": cost_record(ledger, cache_hit=False, wall_ms=wall_ms, escalations=[]),
            },
        )
        self.emit(
            review,
            status="failed",
            result=None,
            ledger=ledger,
            wall_ms=wall_ms,
            escalations=[],
            completed_at=now,
        )


def cost_record(
    ledger: CostLedger, *, cache_hit: bool, wall_ms: int, escalations: list[dict]
) -> dict:
    summary = ledger.summary()
    tiers = sorted(
        {e.stage for e in summary.entries if e.stage in ("triage", "escalation", "baseline")}
    )
    return {
        "cache_hit": cache_hit,
        "escalated": bool(escalations),
        "escalation_reasons": sorted({e["reason"] for e in escalations}),
        "model_tiers": tiers,
        "input_tokens": summary.input_tokens,
        "output_tokens": summary.output_tokens,
        "cost_usd": summary.cost_usd,
        "triage_calls": summary.triage_calls,
        "escalation_calls": summary.escalation_calls,
        "wall_ms": wall_ms,
        "entries": [e.model_dump() for e in summary.entries],
    }
