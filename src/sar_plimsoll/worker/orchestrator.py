"""Review orchestration: chunk → retrieve rules → triage → (escalate) → validate → score."""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from sar_plimsoll.config import Settings
from sar_plimsoll.llm.gateway import ClassifyResult, LLMGateway
from sar_plimsoll.llm.ledger import CostLedger
from sar_plimsoll.review.chunking import Batch, Chunk, chunk_source, pack_batches
from sar_plimsoll.review.languages import ValidatedSource
from sar_plimsoll.review.prompts import SYSTEM_PROMPT, PromptRule, build_review_prompt
from sar_plimsoll.review.schema import SEVERITIES, Finding, ModelFinding, finding_id
from sar_plimsoll.review.triage import escalation_reason
from sar_plimsoll.rules.ingest import UNINITIALISED_CORPUS
from sar_plimsoll.rules.retrieval import RuleRetriever, rules_for_batch
from sar_plimsoll.scoring.engine import ScoreResult, score
from sar_plimsoll.scoring.rubric import Rubric
from sar_plimsoll.storage.interfaces import RuleHit

log = logging.getLogger(__name__)

Mode = Literal["tiered", "baseline"]
_MAX_PARALLEL_BATCHES = 4


class ReviewFailed(Exception):
    """Non-retryable: the models could not produce parseable output for part of the file."""


@dataclass
class BatchOutcome:
    findings: list[Finding]
    rules_offered: list[RuleHit]
    escalation: str | None
    dropped_rule_ids: int
    escalation_error: str | None = None


@dataclass
class ReviewOutcome:
    findings: list[Finding]
    score: ScoreResult
    rules_grounded: list[dict]
    escalations: list[dict]
    stats: dict = field(default_factory=dict)

    def to_record(self) -> dict:
        return {
            "score": self.score.model_dump(),
            "findings": [f.model_dump() for f in self.findings],
            "rules_grounded": self.rules_grounded,
            "escalations": self.escalations,
            "stats": self.stats,
        }


class ReviewOrchestrator:
    def __init__(
        self, *, gateway: LLMGateway, retriever: RuleRetriever, rubric: Rubric, settings: Settings
    ):
        self._gateway = gateway
        self._retriever = retriever
        self._rubric = rubric
        self._s = settings

    def run(
        self,
        source: ValidatedSource,
        *,
        ledger: CostLedger,
        corpus_version: str,
        mode: Mode = "tiered",
    ) -> ReviewOutcome:
        lines = source.text.splitlines()
        chunks = chunk_source(source.text, source.language)
        batches = pack_batches(chunks, lines, self._s.batch_max_chars)

        if corpus_version == UNINITIALISED_CORPUS:
            per_chunk: list[list[RuleHit]] = [[] for _ in chunks]
        else:
            per_chunk = self._retriever.retrieve(
                [c.text(lines) for c in chunks], k=self._s.rules_top_k, ledger=ledger
            ).per_chunk

        def review_batch(batch: Batch) -> BatchOutcome:
            indexes = [_index_of(chunks, c) for c in batch.chunks]
            hits = rules_for_batch(per_chunk, indexes, self._s.max_rules_per_batch)
            return self._review_batch(source, lines, batch, hits, ledger, mode)

        with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL_BATCHES, len(batches) or 1)) as pool:
            outcomes = list(pool.map(review_batch, batches))

        findings = _dedupe(f for o in outcomes for f in o.findings)
        offered = {h.rule.id: h.rule for o in outcomes for h in o.rules_offered}
        grounded_ids = sorted({rid for f in findings for rid in f.grounded_rule_ids})

        return ReviewOutcome(
            findings=findings,
            score=score(findings, self._rubric),
            rules_grounded=[
                {"id": rid, "type": offered[rid].type, "description": offered[rid].description}
                for rid in grounded_ids
            ],
            escalations=[
                {"lines": [b.start_line, b.end_line], "reason": o.escalation}
                | ({"escalation_error": o.escalation_error} if o.escalation_error else {})
                for b, o in zip(batches, outcomes, strict=True)
                if o.escalation
            ],
            stats={
                "mode": mode,
                "chunks": len(chunks),
                "batches": len(batches),
                "rules_offered": len(offered),
                "ungrounded_rule_ids_dropped": sum(o.dropped_rule_ids for o in outcomes),
            },
        )

    def _review_batch(
        self,
        source: ValidatedSource,
        lines: list[str],
        batch: Batch,
        hits: list[RuleHit],
        ledger: CostLedger,
        mode: Mode,
    ) -> BatchOutcome:
        prompt = build_review_prompt(
            filename=source.filename,
            language=source.language,
            lines=lines[batch.start_line - 1 : batch.end_line],
            first_line=batch.start_line,
            rules=[PromptRule(h.rule.id, h.rule.type, h.rule.description) for h in hits],
        )

        def call(tier, stage=None) -> ClassifyResult:
            return self._gateway.classify(
                tier=tier, system=SYSTEM_PROMPT, prompt=prompt, ledger=ledger, stage=stage
            )

        reason = None
        escalation_error = None
        if mode == "baseline":
            result = call("escalation", stage="baseline")
        else:
            result = call("triage")
            reason = escalation_reason(result, self._s.escalation_confidence_threshold)
            if reason:
                escalated = call("escalation")
                if escalated.findings is not None:
                    result = escalated
                elif result.findings is not None:
                    # Keep the triage findings, but make the degraded outcome visible on the review.
                    escalation_error = escalated.parse_error
                    log.warning(
                        "escalation output unusable; keeping triage findings",
                        extra={"error": escalated.parse_error},
                    )

        if result.findings is None:
            raise ReviewFailed(
                f"unparseable model output for lines {batch.start_line}-{batch.end_line}"
            )

        offered_ids = {h.rule.id for h in hits}
        findings, dropped = [], 0
        for mf in result.findings.findings:
            finding, n_dropped = _validate(mf, offered_ids, len(lines), result.tier)
            findings.append(finding)
            dropped += n_dropped
        return BatchOutcome(findings, hits, reason, dropped, escalation_error)


def _index_of(chunks: list[Chunk], chunk: Chunk) -> int:
    # Oversized chunks are split during packing; map pieces back to the chunk that contains them.
    for i, c in enumerate(chunks):
        if c.start_line <= chunk.start_line <= c.end_line:
            return i
    return 0


def _validate(
    mf: ModelFinding, offered_ids: set[str], total_lines: int, tier
) -> tuple[Finding, int]:
    start = min(max(1, mf.line_start), total_lines)
    end = min(max(start, mf.line_end), total_lines)
    # Grounding must be provable: keep only rule IDs that were actually given to the model.
    grounded = sorted({rid for rid in mf.grounded_rule_ids if rid in offered_ids})
    dropped = len(set(mf.grounded_rule_ids)) - len(grounded)
    clean = mf.model_copy(
        update={
            "line_start": start,
            "line_end": end,
            "message": mf.message.strip(),
            "suggestion": mf.suggestion.strip(),
            "grounded_rule_ids": grounded,
        }
    )
    return Finding(**clean.model_dump(), id=finding_id(clean), model_tier=tier), dropped


def _dedupe(findings) -> list[Finding]:
    unique: dict[str, Finding] = {}
    for f in findings:
        unique.setdefault(f.id, f)
    rank = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    return sorted(unique.values(), key=lambda f: (f.line_start, rank[f.severity], f.id))
