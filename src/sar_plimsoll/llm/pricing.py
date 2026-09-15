"""Price table and cost arithmetic. Unknown models raise: a silent $0 would corrupt the cost story."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

_MTOK = 1_000_000
_TIB = 1024**4


@dataclass(frozen=True)
class TokenPrice:
    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float


class PriceTable:
    def __init__(self, data: dict):
        self._data = data

    @classmethod
    def load(cls, path: Path) -> "PriceTable":
        with path.open(encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh))

    def token_price(self, model: str, on: date, *, standard: bool = False) -> TokenPrice:
        try:
            entry = self._data["models"][model]
        except KeyError:
            raise KeyError(f"no price configured for model {model!r} in pricing.yaml") from None
        promo = entry.get("promo")
        if promo and not standard and on <= promo["until"]:
            entry = promo
        return TokenPrice(
            entry["input_per_mtok"], entry["output_per_mtok"], entry["cached_input_per_mtok"]
        )

    def generation_cost(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        on: date,
        standard: bool = False,
    ) -> float:
        p = self.token_price(model, on, standard=standard)
        uncached = max(0, input_tokens - cached_tokens)
        return (
            uncached * p.input_per_mtok
            + cached_tokens * p.cached_input_per_mtok
            + output_tokens * p.output_per_mtok
        ) / _MTOK

    def embedding_cost(self, model: str, *, chars: int, tokens: int | None) -> float:
        try:
            entry = self._data["embeddings"][model]
        except KeyError:
            raise KeyError(f"no price configured for embedding model {model!r}") from None
        if "per_1k_tokens" in entry:
            # Prefer the API's token count; ~4 characters per token when it is not reported.
            billable = tokens if tokens is not None else chars / 4
            return billable / 1000 * entry["per_1k_tokens"]
        return chars / 1000 * entry["per_1k_chars"]

    def entry_cost(self, entry, *, on: date, standard: bool = False) -> float:
        """Re-price a recorded UsageEntry, e.g. at list price once promotional pricing ends."""
        if entry.model == "bigquery":
            return self.bigquery_cost(entry.bytes_billed)
        if entry.stage.startswith("embed"):
            return self.embedding_cost(
                entry.model, chars=entry.chars, tokens=entry.input_tokens or None
            )
        return self.generation_cost(
            entry.model,
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens + entry.thinking_tokens,
            cached_tokens=entry.cached_tokens,
            on=on,
            standard=standard,
        )

    def bigquery_cost(self, bytes_billed: int) -> float:
        bq = self._data["bigquery"]
        if bytes_billed <= 0:
            return 0.0
        return max(bytes_billed, bq["min_billed_bytes"]) / _TIB * bq["on_demand_per_tib"]
