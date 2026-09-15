"""The single entry point for model calls: retries, timeouts, structured parsing, cost logging.

Nothing outside llm/ may call a model SDK directly.
"""

import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from sar_plimsoll.config import Settings
from sar_plimsoll.llm.backends.base import (
    EmbeddingBackend,
    EmbeddingResult,
    GenerativeBackend,
    RawGeneration,
    TransientLLMError,
)
from sar_plimsoll.llm.ledger import CostLedger, Stage, UsageEntry
from sar_plimsoll.llm.pricing import PriceTable
from sar_plimsoll.review.schema import ModelFindings, ModelTier, response_json_schema

log = logging.getLogger(__name__)

# Embedding request limits: 250 texts and ~20k tokens per request; stay well under the token cap.
EMBED_MAX_CHARS = 48_000
EMBED_MAX_TEXT_CHARS = 6_000


@dataclass(frozen=True)
class ClassifyResult:
    model: str
    tier: ModelTier
    findings: ModelFindings | None
    parse_error: str | None


class LLMGateway:
    def __init__(
        self,
        *,
        generative: GenerativeBackend,
        embedding: EmbeddingBackend,
        prices: PriceTable,
        settings: Settings,
        today: Callable[[], date] = date.today,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._gen = generative
        self._emb = embedding
        self._prices = prices
        self._s = settings
        self._today = today
        self._sleep = sleep
        # One long-lived pool: a fresh pool per call spawns new threads per ingest checkpoint, and
        # glibc gives each new thread its own malloc arena, so RSS ratchets up checkpoint by
        # checkpoint (observed on Cloud Run: 1 GiB exhausted after 13 checkpoints).
        self._embed_pool = ThreadPoolExecutor(
            max_workers=max(1, settings.embedding_concurrency), thread_name_prefix="embed"
        )

    def model_for(self, tier: ModelTier) -> str:
        return self._s.triage_model if tier == "triage" else self._s.escalation_model

    def classify(
        self,
        *,
        tier: ModelTier,
        system: str,
        prompt: str,
        ledger: CostLedger,
        stage: Stage | None = None,
    ) -> ClassifyResult:
        model = self.model_for(tier)
        thinking = (
            self._s.triage_thinking_level if tier == "triage" else self._s.escalation_thinking_level
        )
        schema = response_json_schema()

        def call() -> RawGeneration:
            return self._gen.generate(
                model=model,
                system=system,
                prompt=prompt,
                json_schema=schema,
                thinking_level=thinking,
                max_output_tokens=self._s.triage_max_output_tokens
                if tier == "triage"
                else self._s.escalation_max_output_tokens,
                timeout_s=self._s.llm_timeout_s,
            )

        raw, attempts, latency_ms = self._with_retries(call)
        cost = self._prices.generation_cost(
            model,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens + raw.thinking_tokens,
            cached_tokens=raw.cached_tokens,
            on=self._today(),
        )
        entry = UsageEntry(
            stage=stage or tier,
            model=model,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            thinking_tokens=raw.thinking_tokens,
            cached_tokens=raw.cached_tokens,
            attempts=attempts,
            latency_ms=latency_ms,
            cost_usd=cost,
            finish_reason=raw.finish_reason,
        )
        ledger.record(entry)
        self._log(entry)

        if raw.finish_reason == "MAX_TOKENS":
            error = (
                f"truncated: output limit reached ({raw.thinking_tokens} thinking + "
                f"{raw.output_tokens} output tokens)"
            )
            log.warning("structured output truncated", extra={"model": model, "error": error})
            return ClassifyResult(model=model, tier=tier, findings=None, parse_error=error)
        try:
            parsed = ModelFindings.model_validate(json.loads(raw.text))
            return ClassifyResult(model=model, tier=tier, findings=parsed, parse_error=None)
        except (json.JSONDecodeError, ValidationError) as exc:
            # Never include the raw text: it can echo submitted source.
            error = f"{type(exc).__name__} at output length {len(raw.text)}"
            log.warning("structured output parse failed", extra={"model": model, "error": error})
            return ClassifyResult(model=model, tier=tier, findings=None, parse_error=error)

    def embed(
        self, texts: list[str], *, task_type: str, ledger: CostLedger, stage: Stage
    ) -> list[list[float]]:
        """Embed texts in API-sized batches, several batches in parallel, preserving input order."""
        model = self._s.embedding_model
        if any(not t for t in texts):
            # The API drops empty inputs without an error, returning fewer vectors than texts.
            raise ValueError("embed() received an empty text; filter blank inputs before embedding")
        clipped = [t[:EMBED_MAX_TEXT_CHARS] for t in texts]
        batches = _batches(clipped, self._s.embedding_batch_size)

        def run(batch: list[str]) -> list[list[float]]:
            result, attempts, latency_ms = self._with_retries(
                lambda: self._emb.embed(
                    model=model,
                    texts=batch,
                    task_type=task_type,
                    dimensions=self._s.embedding_dim,
                    timeout_s=self._s.llm_timeout_s,
                )
            )
            chars = sum(len(t) for t in batch)
            entry = UsageEntry(
                stage=stage,
                model=model,
                input_tokens=result.input_tokens or 0,
                chars=chars,
                attempts=attempts,
                latency_ms=latency_ms,
                cost_usd=self._prices.embedding_cost(
                    model, chars=chars, tokens=result.input_tokens
                ),
            )
            ledger.record(entry)
            self._log(entry)
            return _checked(result, len(batch))

        if len(batches) == 1:
            per_batch = [run(batches[0])]
        else:
            per_batch = list(self._embed_pool.map(run, batches))
        return [vector for vectors in per_batch for vector in vectors]

    def _with_retries[T](self, fn: Callable[[], T]) -> tuple[T, int, int]:
        attempts = 0
        while True:
            attempts += 1
            started = time.monotonic()
            try:
                result = fn()
                return result, attempts, int((time.monotonic() - started) * 1000)
            except TransientLLMError as exc:
                if attempts >= self._s.llm_max_attempts:
                    raise
                self._sleep(_backoff_s(attempts, rate_limited=exc.rate_limited))

    @staticmethod
    def _log(entry: UsageEntry) -> None:
        log.info("llm_usage", extra={"usage": entry.model_dump()})


def _backoff_s(attempt: int, *, rate_limited: bool) -> float:
    # Per-minute quotas need most of a minute to recover; transport blips need a second or two.
    if rate_limited:
        return min(60.0, 5.0 * 2 ** (attempt - 1))
    return min(30.0, 2.0 ** (attempt - 1))


def _checked(result: EmbeddingResult, expected: int) -> list[list[float]]:
    if len(result.vectors) != expected:
        raise ValueError(
            f"embedding backend returned {len(result.vectors)} vectors for {expected} texts"
        )
    return result.vectors


def _batches(texts: list[str], max_texts: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    chars = 0
    for text in texts:
        if current and (len(current) >= max_texts or chars + len(text) > EMBED_MAX_CHARS):
            batches.append(current)
            current, chars = [], 0
        current.append(text)
        chars += len(text)
    if current:
        batches.append(current)
    return batches
