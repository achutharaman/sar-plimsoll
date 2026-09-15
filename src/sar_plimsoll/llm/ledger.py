"""Per-review cost ledger. Every billable call appends one entry."""

import threading
from typing import Literal

from pydantic import BaseModel

Stage = Literal["triage", "escalation", "embed_query", "embed_rules", "vector_search", "baseline"]


class UsageEntry(BaseModel):
    stage: Stage
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cached_tokens: int = 0
    chars: int = 0
    bytes_billed: int = 0
    attempts: int = 1
    latency_ms: int = 0
    cost_usd: float = 0.0
    finish_reason: str | None = None


class CostSummary(BaseModel):
    input_tokens: int
    output_tokens: int
    cost_usd: float
    triage_calls: int
    escalation_calls: int
    entries: list[UsageEntry]


class CostLedger:
    def __init__(self) -> None:
        self._entries: list[UsageEntry] = []
        self._lock = threading.Lock()

    def record(self, entry: UsageEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    @property
    def entries(self) -> list[UsageEntry]:
        with self._lock:
            return list(self._entries)

    def summary(self) -> CostSummary:
        entries = self.entries
        return CostSummary(
            input_tokens=sum(e.input_tokens for e in entries),
            output_tokens=sum(e.output_tokens + e.thinking_tokens for e in entries),
            cost_usd=round(sum(e.cost_usd for e in entries), 8),
            triage_calls=sum(1 for e in entries if e.stage == "triage"),
            escalation_calls=sum(1 for e in entries if e.stage in ("escalation", "baseline")),
            entries=entries,
        )
