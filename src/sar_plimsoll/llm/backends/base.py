"""Backend contracts. Only llm/ may import concrete backends."""

from dataclasses import dataclass
from typing import Protocol


class TransientLLMError(Exception):
    """Retryable: rate limits, timeouts, 5xx."""

    def __init__(self, message: str, *, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited


class PermanentLLMError(Exception):
    """Not retryable: bad request, permission denied, unknown model."""


@dataclass(frozen=True)
class RawGeneration:
    text: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int = 0
    cached_tokens: int = 0
    finish_reason: str | None = None  # e.g. "STOP", "MAX_TOKENS"


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    input_tokens: int | None = None  # None when the backend does not report usage


class GenerativeBackend(Protocol):
    def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        json_schema: dict,
        thinking_level: str | None,
        max_output_tokens: int,
        timeout_s: float,
    ) -> RawGeneration: ...


class EmbeddingBackend(Protocol):
    def embed(
        self, *, model: str, texts: list[str], task_type: str, dimensions: int, timeout_s: float
    ) -> EmbeddingResult: ...
