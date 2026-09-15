"""Rows written to the analytics warehouse. One review row per finished review, one row per finding.

No source code and no finding suggestions are written: only metadata, scores, costs and the
model-generated one-line message.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sar_plimsoll.llm.ledger import UsageEntry
from sar_plimsoll.llm.pricing import PriceTable
from sar_plimsoll.review.schema import SEVERITIES

REVIEW_MODES = ("tiered", "benchmark_tiered", "benchmark_baseline")


def finding_counts(findings: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(SEVERITIES, 0)
    for f in findings:
        counts[f["severity"]] += 1
    return counts


def review_row(
    *,
    review: dict[str, Any],
    status: str,
    mode: str,
    completed_at: datetime,
    result: dict[str, Any] | None,
    cache_hit: bool,
    wall_ms: int,
    escalations: Sequence[dict[str, Any]],
    entries: Sequence[UsageEntry],
    prices: PriceTable | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    findings = (result or {}).get("findings", [])
    counts = finding_counts(findings)

    def tokens(stages: tuple[str, ...], field: str) -> int:
        return sum(getattr(e, field) for e in entries if e.stage in stages)

    pro_stages = ("escalation", "baseline")
    on = completed_at.date()
    return {
        "review_id": review["id"],
        "uid": review["uid"],
        "mode": mode,
        "run_id": run_id,
        "status": status,
        "created_at": review["created_at"].isoformat(),
        "completed_at": completed_at.isoformat(),
        "filename": review["filename"],
        "language": review["language"],
        "line_count": review.get("line_count"),
        "score": (result or {}).get("score", {}).get("overall"),
        "findings": len(findings),
        "critical": counts["critical"],
        "high": counts["high"],
        "medium": counts["medium"],
        "low": counts["low"],
        "grounded_findings": sum(1 for f in findings if f.get("grounded_rule_ids")),
        "cache_hit": cache_hit,
        "escalated": bool(escalations),
        "escalation_reasons": sorted({e["reason"] for e in escalations}),
        "triage_calls": sum(1 for e in entries if e.stage == "triage"),
        "escalation_calls": sum(1 for e in entries if e.stage in pro_stages),
        "triage_input_tokens": tokens(("triage",), "input_tokens"),
        "triage_output_tokens": tokens(("triage",), "output_tokens")
        + tokens(("triage",), "thinking_tokens"),
        "escalation_input_tokens": tokens(pro_stages, "input_tokens"),
        "escalation_output_tokens": tokens(pro_stages, "output_tokens")
        + tokens(pro_stages, "thinking_tokens"),
        "thinking_tokens": tokens(("triage", *pro_stages), "thinking_tokens"),
        "cost_usd": round(sum(e.cost_usd for e in entries), 8),
        "cost_usd_standard": round(
            sum(prices.entry_cost(e, on=on, standard=True) for e in entries), 8
        )
        if prices
        else 0.0,
        "wall_ms": wall_ms,
        "rubric_version": review.get("rubric_version"),
        "prompt_version": review.get("prompt_version"),
        "rules_corpus_version": review.get("rules_corpus_version"),
        "triage_model": (review.get("models") or {}).get("triage"),
        "escalation_model": (review.get("models") or {}).get("escalation"),
    }


def finding_rows(
    review: dict[str, Any], findings: Sequence[dict[str, Any]], completed_at: datetime
) -> list[dict[str, Any]]:
    return [
        {
            "review_id": review["id"],
            "uid": review["uid"],
            "created_at": completed_at.isoformat(),
            "language": review["language"],
            "rubric_version": review.get("rubric_version"),
            "rules_corpus_version": review.get("rules_corpus_version"),
            "finding_id": f["id"],
            "dimension": f["dimension"],
            "severity": f["severity"],
            "confidence": f["confidence"],
            "model_tier": f["model_tier"],
            "grounded_rule_ids": list(f.get("grounded_rule_ids", [])),
            "message": f["message"],
        }
        for f in findings
    ]
