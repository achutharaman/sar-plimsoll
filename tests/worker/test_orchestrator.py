from sar_plimsoll.llm.backends.fake import MALFORMED_MARKER
from sar_plimsoll.llm.ledger import CostLedger
from sar_plimsoll.review.languages import validate_submission
from sar_plimsoll.rules.ingest import current_corpus_version
from tests.helpers import CLEAN_PY, SPEC_RULES, VULNERABLE_PY, local_container


def run(container, code: str, filename="app.py", mode="tiered"):
    source = validate_submission(
        filename=filename,
        content=code.encode(),
        declared_language=None,
        max_bytes=200_000,
        max_lines=5_000,
    )
    ledger = CostLedger()
    outcome = container.orchestrator.run(
        source, ledger=ledger, corpus_version=current_corpus_version(container.store), mode=mode
    )
    return outcome, ledger


def test_findings_are_grounded_in_ingested_rules():
    c = local_container()
    c.ingestor.ingest(SPEC_RULES)
    outcome, _ = run(c, VULNERABLE_PY)

    sql = next(f for f in outcome.findings if f.dimension == "security")
    assert sql.severity == "critical"
    assert sql.grounded_rule_ids == ["3"]
    assert {r["id"] for r in outcome.rules_grounded} >= {"3"}
    assert any(
        f.grounded_rule_ids == ["2"] for f in outcome.findings if f.dimension == "performance"
    )
    assert outcome.score.overall < 10


def test_no_rules_means_no_grounding_and_no_retrieval_cost():
    c = local_container()
    outcome, ledger = run(c, VULNERABLE_PY)
    assert all(f.grounded_rule_ids == [] for f in outcome.findings)
    assert not any(e.stage.startswith("embed") for e in ledger.entries)
    assert c.rules_repo.search_calls == 0


def test_high_severity_escalates_to_pro():
    c = local_container()
    outcome, ledger = run(c, VULNERABLE_PY)
    assert outcome.escalations == [{"lines": [1, 21], "reason": "high_severity"}]
    assert [e.stage for e in ledger.entries] == ["triage", "escalation"]
    assert all(f.model_tier == "escalation" for f in outcome.findings)


def test_clean_code_stays_on_the_cheap_tier():
    c = local_container()
    outcome, ledger = run(c, CLEAN_PY)
    assert outcome.escalations == []
    assert [e.stage for e in ledger.entries] == ["triage"]
    assert outcome.score.overall == 10.0


def test_parse_failure_escalates():
    c = local_container()
    outcome, ledger = run(c, f"# {MALFORMED_MARKER}\ndef ok():\n    return 1\n")
    assert outcome.escalations[0]["reason"] == "parse_failed"
    assert [e.stage for e in ledger.entries] == ["triage", "escalation"]


def test_hallucinated_rule_ids_are_dropped():
    c = local_container()
    c.ingestor.ingest(SPEC_RULES)

    real_classify = c.gateway.classify

    def inject_fake_id(**kwargs):
        result = real_classify(**kwargs)
        for f in result.findings.findings:
            f.grounded_rule_ids.append("999")
        return result

    c.gateway.classify = inject_fake_id
    outcome, _ = run(c, VULNERABLE_PY)
    assert all("999" not in f.grounded_rule_ids for f in outcome.findings)
    assert outcome.stats["ungrounded_rule_ids_dropped"] == len(outcome.findings)


def test_baseline_mode_sends_everything_to_pro():
    c = local_container()
    _, ledger = run(c, CLEAN_PY, mode="baseline")
    assert [e.stage for e in ledger.entries] == ["baseline"]
    assert ledger.entries[0].model == c.settings.escalation_model


def test_same_input_same_score():
    c = local_container()
    c.ingestor.ingest(SPEC_RULES)
    first, _ = run(c, VULNERABLE_PY)
    second, _ = run(c, VULNERABLE_PY)
    assert first.score == second.score


def test_blank_chunks_are_not_embedded():
    # A lone blank line between definitions becomes an empty chunk; the embedding API drops empty
    # inputs, which previously failed the review with "expected N embeddings, got M".
    c = local_container()
    c.ingestor.ingest(SPEC_RULES)
    code = "import os\n\ndef a():\n    return 1\n\ndef b():\n    return 2\n\nx = 1\n"
    outcome, ledger = run(c, code)
    assert outcome.stats["chunks"] >= 5
    assert any(e.stage == "embed_query" for e in ledger.entries)


def test_gateway_rejects_empty_texts():
    import pytest

    c = local_container()
    with pytest.raises(ValueError, match="empty text"):
        c.gateway.embed(
            ["a", ""], task_type="CODE_RETRIEVAL_QUERY", ledger=CostLedger(), stage="embed_query"
        )


def test_failed_escalation_is_recorded_on_the_review():
    from sar_plimsoll.llm.gateway import ClassifyResult

    c = local_container()
    real = c.gateway.classify

    def broken_pro(**kwargs):
        if kwargs["tier"] == "escalation":
            return ClassifyResult(
                model="pro",
                tier="escalation",
                findings=None,
                parse_error="truncated: output limit reached",
            )
        return real(**kwargs)

    c.gateway.classify = broken_pro
    outcome, _ = run(c, VULNERABLE_PY)
    (escalation,) = outcome.escalations
    assert escalation["reason"] == "high_severity"
    assert escalation["escalation_error"].startswith("truncated")
    assert all(f.model_tier == "triage" for f in outcome.findings)  # kept, not lost
