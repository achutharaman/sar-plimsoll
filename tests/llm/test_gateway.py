import random
import time
from datetime import date

import pytest

from sar_plimsoll.llm.backends.base import (
    EmbeddingResult,
    PermanentLLMError,
    RawGeneration,
    TransientLLMError,
)
from sar_plimsoll.llm.backends.fake import FakeEmbedding
from sar_plimsoll.llm.gateway import LLMGateway
from sar_plimsoll.llm.ledger import CostLedger
from sar_plimsoll.llm.pricing import PriceTable
from tests.helpers import local_settings

VALID = (
    '{"findings": [{"line_start": 1, "line_end": 1, "dimension": "security", "severity": "high",'
    ' "message": "m", "suggestion": "s", "grounded_rule_ids": [], "confidence": 0.9}]}'
)


class ScriptedBackend:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate(self, **_):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def gateway(backend=None, embedding=None, sleeps=None, **settings) -> LLMGateway:
    s = local_settings(**settings)
    return LLMGateway(
        generative=backend or ScriptedBackend(),
        embedding=embedding or FakeEmbedding(),
        prices=PriceTable.load(s.pricing_path),
        settings=s,
        today=lambda: date(2026, 9, 13),
        sleep=sleeps.append if sleeps is not None else (lambda _: None),
    )


def classify(gw, tier="triage"):
    return gw.classify(tier=tier, system="s", prompt="p", ledger=CostLedger())


# ---------------------------------------------------------------- generation


def test_cost_is_computed_from_tokens_including_thinking():
    backend = ScriptedBackend(
        RawGeneration(VALID, input_tokens=1_000_000, output_tokens=500_000, thinking_tokens=500_000)
    )
    ledger = CostLedger()
    result = gateway(backend).classify(tier="triage", system="s", prompt="p", ledger=ledger)
    assert result.findings is not None
    # gemini-3.8-flash promo on 2026-09-13: $0.75/M in, $3.75/M out (thinking billed as output).
    assert ledger.summary().cost_usd == pytest.approx(0.75 + 3.75)


def test_promo_pricing_expires():
    prices = PriceTable.load(local_settings().pricing_path)
    kw = dict(input_tokens=1_000_000, output_tokens=0)
    before = prices.generation_cost("gemini-3.8-flash", on=date(2026, 12, 31), **kw)
    after = prices.generation_cost("gemini-3.8-flash", on=date(2027, 1, 1), **kw)
    assert (before, after) == (0.75, 1.50)


def test_transient_errors_are_retried_then_succeed():
    backend = ScriptedBackend(
        TransientLLMError("429"), TransientLLMError("503"), RawGeneration(VALID, 10, 10)
    )
    ledger = CostLedger()
    gateway(backend).classify(tier="triage", system="s", prompt="p", ledger=ledger)
    assert backend.calls == 3
    assert ledger.entries[0].attempts == 3


def test_transient_errors_exhaust_attempts():
    backend = ScriptedBackend(*[TransientLLMError("503")] * 3)
    with pytest.raises(TransientLLMError):
        classify(gateway(backend, llm_max_attempts=3))


def test_permanent_errors_are_not_retried():
    backend = ScriptedBackend(PermanentLLMError("400"))
    with pytest.raises(PermanentLLMError):
        classify(gateway(backend))
    assert backend.calls == 1


def test_unparseable_output_is_reported_and_still_costed():
    backend = ScriptedBackend(RawGeneration('{"findings": [{"severity": "extreme"}]}', 100, 20))
    ledger = CostLedger()
    result = gateway(backend).classify(tier="escalation", system="s", prompt="p", ledger=ledger)
    assert result.findings is None
    assert "ValidationError" in result.parse_error
    assert ledger.entries[0].cost_usd > 0


def test_unknown_model_price_fails_loudly():
    backend = ScriptedBackend(RawGeneration(VALID, 10, 10))
    with pytest.raises(KeyError, match="no price configured"):
        classify(gateway(backend, triage_model="gemini-unknown"))


def test_transport_errors_back_off_briefly():
    sleeps = []
    backend = ScriptedBackend(TransientLLMError("503"), RawGeneration(VALID, 10, 10))
    classify(gateway(backend, sleeps=sleeps))
    assert sleeps == [1.0]


# ---------------------------------------------------------------- embeddings


class TaggingEmbedding:
    """Returns a vector encoding each text's index, with jittered latency, 3 tokens per text."""

    def embed(self, *, model, texts, task_type, dimensions, timeout_s):
        time.sleep(random.random() / 200)
        return EmbeddingResult(
            [[float(t.split("#")[1])] for t in texts], input_tokens=len(texts) * 3
        )


class RateLimitedThenOk:
    def __init__(self, failures: int):
        self.failures = failures

    def embed(self, *, texts, **_):
        if self.failures:
            self.failures -= 1
            raise TransientLLMError("429 quota", rate_limited=True)
        return EmbeddingResult([[0.0]] * len(texts))


def embed(gw, texts):
    ledger = CostLedger()
    vectors = gw.embed(texts, task_type="RETRIEVAL_DOCUMENT", ledger=ledger, stage="embed_rules")
    return vectors, ledger


def test_embeddings_are_batched_within_api_limits():
    vectors, ledger = embed(gateway(embedding_batch_size=250), ["rule text"] * 501)
    assert len(vectors) == 501
    assert len(ledger.entries) == 3


def test_parallel_batches_preserve_input_order():
    gw = gateway(embedding=TaggingEmbedding(), embedding_batch_size=37, embedding_concurrency=8)
    vectors, _ = embed(gw, [f"rule #{i}" for i in range(1_000)])
    assert [v[0] for v in vectors] == [float(i) for i in range(1_000)]


def test_embedding_cost_uses_reported_tokens():
    gw = gateway(embedding=TaggingEmbedding(), embedding_batch_size=250)
    _, ledger = embed(gw, [f"rule #{i}" for i in range(1_000)])
    # gemini-embedding-001: $0.00015 per 1k tokens; 3 tokens per text.
    assert sum(e.input_tokens for e in ledger.entries) == 3_000
    assert ledger.summary().cost_usd == pytest.approx(3_000 / 1000 * 0.00015)


def test_rate_limits_back_off_long_enough_for_per_minute_quotas():
    sleeps = []
    embed(gateway(embedding=RateLimitedThenOk(3), sleeps=sleeps), ["x"])
    assert sleeps == [5.0, 10.0, 20.0]


# ---------------------------------------------------------------- thinking budget


class RecordingBackend(ScriptedBackend):
    def __init__(self, *outcomes):
        super().__init__(*outcomes)
        self.kwargs = []

    def generate(self, **kwargs):
        self.kwargs.append(kwargs)
        return super().generate(**kwargs)


def test_tiers_get_their_own_output_limits_and_thinking_levels():
    backend = RecordingBackend(RawGeneration(VALID, 10, 10), RawGeneration(VALID, 10, 10))
    gw = gateway(backend)
    classify(gw, tier="triage")
    classify(gw, tier="escalation")
    (triage, escalation) = backend.kwargs
    assert (triage["max_output_tokens"], triage["thinking_level"]) == (8192, "low")
    assert (escalation["max_output_tokens"], escalation["thinking_level"]) == (16384, "medium")


def test_truncated_output_is_reported_as_truncation_and_costed():
    truncated = RawGeneration(
        '{"findings": [{"line_start": 1',
        input_tokens=500,
        output_tokens=318,
        thinking_tokens=7860,
        finish_reason="MAX_TOKENS",
    )
    ledger = CostLedger()
    result = gateway(ScriptedBackend(truncated)).classify(
        tier="escalation", system="s", prompt="p", ledger=ledger
    )
    assert result.findings is None
    assert result.parse_error.startswith("truncated")
    assert ledger.entries[0].finish_reason == "MAX_TOKENS"
    assert ledger.entries[0].cost_usd > 0
