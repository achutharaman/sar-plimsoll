"""Vertex AI via the google-genai SDK. The only module that talks to the model APIs."""

from google import genai
from google.genai import errors, types

from sar_plimsoll.llm.backends.base import (
    EmbeddingResult,
    PermanentLLMError,
    RawGeneration,
    TransientLLMError,
)
from sar_plimsoll.storage.gcp_clients import Lazy, shared_credentials

_TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}


def _map_error(exc: Exception) -> Exception:
    code = getattr(exc, "code", None)
    if isinstance(exc, errors.APIError) and code in _TRANSIENT_CODES:
        return TransientLLMError(f"vertex {code}: {exc.message}", rate_limited=code == 429)
    if isinstance(exc, errors.APIError):
        return PermanentLLMError(f"vertex {code}: {exc.message}")
    # Transport-level failures (timeouts, connection resets) are worth retrying.
    return TransientLLMError(f"{type(exc).__name__}: {exc}")


class VertexGenerative:
    def __init__(self, project: str, location: str):
        self._lazy = Lazy(
            lambda: genai.Client(
                vertexai=True, project=project, location=location, credentials=shared_credentials()
            )
        )

    @property
    def _client(self) -> genai.Client:
        return self._lazy.get()

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
    ) -> RawGeneration:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
            response_mime_type="application/json",
            response_json_schema=json_schema,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level)
            if thinking_level
            else None,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )
        try:
            resp = self._client.models.generate_content(model=model, contents=prompt, config=config)
        except Exception as exc:
            raise _map_error(exc) from exc
        usage = resp.usage_metadata
        candidate = resp.candidates[0] if resp.candidates else None
        finish = getattr(candidate, "finish_reason", None)
        return RawGeneration(
            finish_reason=getattr(finish, "name", None) or (str(finish) if finish else None),
            text=resp.text or "",
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            thinking_tokens=(usage.thoughts_token_count or 0) if usage else 0,
            cached_tokens=(usage.cached_content_token_count or 0) if usage else 0,
        )


class VertexEmbedding:
    def __init__(self, project: str, location: str):
        self._lazy = Lazy(
            lambda: genai.Client(
                vertexai=True, project=project, location=location, credentials=shared_credentials()
            )
        )

    @property
    def _client(self) -> genai.Client:
        return self._lazy.get()

    def embed(
        self, *, model: str, texts: list[str], task_type: str, dimensions: int, timeout_s: float
    ) -> EmbeddingResult:
        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=dimensions,
            auto_truncate=True,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )
        try:
            resp = self._client.models.embed_content(model=model, contents=texts, config=config)
        except Exception as exc:
            raise _map_error(exc) from exc
        embeddings = resp.embeddings or []
        vectors = [list(e.values or []) for e in embeddings]
        if len(vectors) != len(texts):
            # Multimodal embedding models fold a multi-text request into one vector; refuse silently
            # misaligned results rather than attach the wrong vector to a rule.
            raise PermanentLLMError(f"expected {len(texts)} embeddings, got {len(vectors)}")
        counts = [e.statistics.token_count for e in embeddings if e.statistics]
        tokens = int(sum(counts)) if len(counts) == len(embeddings) else None
        return EmbeddingResult(vectors=vectors, input_tokens=tokens)
