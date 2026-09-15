"""CSV → validated rules → embeddings (changed rows only) → warehouse → corpus version.

Idempotent by rule ID: re-ingesting an identical file embeds nothing and leaves the corpus version,
and therefore every review cache entry, untouched. Rules absent from a file are kept (upsert, not
replace), so ingesting a partial file never silently deletes grounding data.

Large files are committed in checkpoints. If a run fails part-way (quota, timeout, outage), every
committed checkpoint stays, and re-running the same file resumes with only the remaining rows.
"""

import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sar_plimsoll.config import Settings
from sar_plimsoll.llm.gateway import LLMGateway
from sar_plimsoll.llm.ledger import CostLedger
from sar_plimsoll.rules.csv_parser import RejectedRow, RuleRow, parse_rules_csv
from sar_plimsoll.storage.interfaces import ReviewStore, RulesRepository

log = logging.getLogger(__name__)

CORPUS_META = "rules_corpus"
UNINITIALISED_CORPUS = "empty"

Fingerprints = dict[str, tuple[str, str | None]]


@dataclass
class IngestReport:
    complete: bool
    error: str | None
    total_rows: int
    accepted: int
    inserted: int
    updated: int
    unchanged: int
    rejected: list[RejectedRow]
    header_detected: bool
    encoding: str
    embedded: int
    remaining: int
    corpus_version: str
    corpus_size: int
    cost_usd: float
    duration_ms: int

    def to_dict(self) -> dict:
        data = asdict(self)
        data["rejected_count"] = len(self.rejected)
        return data


def embedding_text(row: RuleRow) -> str:
    return f"{row.type}: {row.description}"


def corpus_version(fingerprints: Fingerprints, dimensions: int) -> str:
    if not fingerprints:
        return UNINITIALISED_CORPUS
    digest = hashlib.sha256(f"dim={dimensions}".encode())
    for rule_id in sorted(fingerprints):
        content_hash, model = fingerprints[rule_id]
        digest.update(f"\x1e{rule_id}\x1f{content_hash}\x1f{model or ''}".encode())
    return "rc_" + digest.hexdigest()[:16]


class RulesIngestor:
    def __init__(
        self, *, repo: RulesRepository, store: ReviewStore, gateway: LLMGateway, settings: Settings
    ):
        self._repo = repo
        self._store = store
        self._gateway = gateway
        self._s = settings

    def ingest(
        self, data: bytes, on_checkpoint: Callable[[int, int], None] | None = None
    ) -> IngestReport:
        started = datetime.now(UTC)
        parsed = parse_rules_csv(data)
        self._repo.ensure_schema(self._s.embedding_dim)

        model = self._s.embedding_model
        state: Fingerprints = self._repo.fingerprints()
        new_rows = [r for r in parsed.rules if r.id not in state]
        # A rule is stale if its text changed or it was embedded with a different model:
        # vectors from different models are not comparable.
        changed_rows = [
            r for r in parsed.rules if r.id in state and state[r.id] != (r.content_hash, model)
        ]
        to_write = new_rows + changed_rows

        ledger = CostLedger()
        written, error = 0, None
        step = max(1, self._s.ingest_checkpoint_rows)
        for offset in range(0, len(to_write), step):
            chunk = to_write[offset : offset + step]
            try:
                vectors = self._gateway.embed(
                    [embedding_text(r) for r in chunk],
                    task_type="RETRIEVAL_DOCUMENT",
                    ledger=ledger,
                    stage="embed_rules",
                )
                self._repo.upsert(
                    chunk, {r.id: v for r, v in zip(chunk, vectors, strict=True)}, model
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
                log.exception("rules ingest checkpoint failed", extra={"committed_rows": written})
                break
            del vectors
            written += len(chunk)
            state.update({r.id: (r.content_hash, model) for r in chunk})
            self._publish_version(state)
            if on_checkpoint:
                on_checkpoint(written, len(to_write) - written)
            log.info(
                "rules ingest checkpoint",
                extra={
                    "committed_rows": written,
                    "remaining": len(to_write) - written,
                    "rss_mib": _rss_mib(),
                },
            )

        if written:
            self._repo.refresh_index()
        version = self._publish_version(state)

        report = IngestReport(
            complete=error is None,
            error=None if error is None else f"{error} — re-run the same file to resume",
            total_rows=len(parsed.rules) + len(parsed.rejected),
            accepted=len(parsed.rules),
            inserted=len(new_rows),
            updated=len(changed_rows),
            unchanged=len(parsed.rules) - len(to_write),
            rejected=parsed.rejected,
            header_detected=parsed.header_detected,
            encoding=parsed.encoding,
            embedded=written,
            remaining=len(to_write) - written,
            corpus_version=version,
            corpus_size=len(state),
            cost_usd=ledger.summary().cost_usd,
            duration_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
        )
        log.info("rules_ingested", extra={"report": {**report.to_dict(), "rejected": None}})
        return report

    def _publish_version(self, state: Fingerprints) -> str:
        version = corpus_version(state, self._s.embedding_dim)
        self._store.set_meta(
            CORPUS_META,
            {"version": version, "rule_count": len(state), "updated_at": datetime.now(UTC)},
        )
        return version


def _rss_mib() -> float | None:
    try:
        with open("/proc/self/statm") as fh:
            return round(int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20, 1)
    except (OSError, ValueError):
        return None


def current_corpus_version(store: ReviewStore) -> str:
    meta = store.get_meta(CORPUS_META)
    return meta["version"] if meta else UNINITIALISED_CORPUS
