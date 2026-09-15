"""Retrieve the historical rules most relevant to each code chunk."""

from dataclasses import dataclass

from sar_plimsoll.llm.gateway import LLMGateway
from sar_plimsoll.llm.ledger import CostLedger, UsageEntry
from sar_plimsoll.llm.pricing import PriceTable
from sar_plimsoll.storage.interfaces import RuleHit, RulesRepository


@dataclass(frozen=True)
class RetrievalResult:
    per_chunk: list[list[RuleHit]]


class RuleRetriever:
    def __init__(self, *, repo: RulesRepository, gateway: LLMGateway, prices: PriceTable):
        self._repo = repo
        self._gateway = gateway
        self._prices = prices

    def retrieve(self, chunk_texts: list[str], *, k: int, ledger: CostLedger) -> RetrievalResult:
        # Blank chunks (e.g. a lone empty line between functions) have nothing to retrieve for, and
        # the embedding API silently drops empty inputs, which would misalign vectors and chunks.
        wanted = [i for i, text in enumerate(chunk_texts) if text.strip()]
        per_chunk: list[list[RuleHit]] = [[] for _ in chunk_texts]
        if not wanted:
            return RetrievalResult(per_chunk=per_chunk)
        vectors = self._gateway.embed(
            [chunk_texts[i] for i in wanted],
            task_type="CODE_RETRIEVAL_QUERY",
            ledger=ledger,
            stage="embed_query",
        )
        # All chunks go into a single search so a review costs one warehouse query, not one per chunk.
        result = self._repo.search(vectors, k)
        if result.bytes_billed:
            ledger.record(
                UsageEntry(
                    stage="vector_search",
                    model="bigquery",
                    bytes_billed=result.bytes_billed,
                    cost_usd=self._prices.bigquery_cost(result.bytes_billed),
                )
            )
        for index, hits in zip(wanted, result.hits, strict=True):
            per_chunk[index] = hits
        return RetrievalResult(per_chunk=per_chunk)


def rules_for_batch(
    per_chunk: list[list[RuleHit]], chunk_indexes: list[int], limit: int
) -> list[RuleHit]:
    """Union the chunks' hits, keep each rule's best distance, return the closest `limit`."""
    best: dict[str, RuleHit] = {}
    for idx in chunk_indexes:
        for hit in per_chunk[idx] if idx < len(per_chunk) else []:
            current = best.get(hit.rule.id)
            if current is None or hit.distance < current.distance:
                best[hit.rule.id] = hit
    return sorted(best.values(), key=lambda h: (h.distance, h.rule.id))[:limit]
