from tests.helpers import SPEC_RULES, local_container


def test_ingest_embeds_and_versions_the_corpus():
    c = local_container()
    report = c.ingestor.ingest(SPEC_RULES)
    assert (report.inserted, report.updated, report.unchanged) == (3, 0, 0)
    assert report.embedded == 3
    assert report.corpus_version.startswith("rc_")
    assert c.store.get_meta("rules_corpus")["rule_count"] == 3


def test_reingesting_the_same_file_is_a_no_op():
    c = local_container()
    first = c.ingestor.ingest(SPEC_RULES)
    second = c.ingestor.ingest(SPEC_RULES)
    assert (second.inserted, second.updated, second.unchanged, second.embedded) == (0, 0, 3, 0)
    assert second.corpus_version == first.corpus_version
    assert second.cost_usd == 0


def test_changed_rule_is_reembedded_and_bumps_version():
    c = local_container()
    first = c.ingestor.ingest(SPEC_RULES)
    changed = SPEC_RULES.replace(b"SQL queries", b"SQL or shell commands")
    second = c.ingestor.ingest(changed)
    assert (second.updated, second.embedded) == (1, 1)
    assert second.corpus_version != first.corpus_version


def test_partial_file_does_not_delete_rules():
    c = local_container()
    c.ingestor.ingest(SPEC_RULES)
    report = c.ingestor.ingest(b"4, security, Validate all external input\n")
    assert report.corpus_size == 4


def test_file_with_only_bad_rows_reports_and_does_not_crash():
    c = local_container()
    report = c.ingestor.ingest(b"\n\n,,\nonly-one-column\n")  # ",," is a blank row
    assert report.accepted == 0
    assert len(report.rejected) == 1
    assert report.corpus_version == "empty"


def many_rules(n: int) -> bytes:
    return "".join(f"{i},performance,Rule number {i} about caching\n" for i in range(n)).encode()


def test_large_file_commits_in_checkpoints_and_resumes_after_failure():
    from sar_plimsoll.llm.backends.base import TransientLLMError

    c = local_container(ingest_checkpoint_rows=100, embedding_batch_size=50)
    real_embed = c.gateway.embed
    calls = {"n": 0}

    def flaky_embed(texts, **kw):
        calls["n"] += 1
        if calls["n"] == 3:  # third checkpoint hits an exhausted quota
            raise TransientLLMError("429", rate_limited=True)
        return real_embed(texts, **kw)

    c.gateway.embed = flaky_embed
    first = c.ingestor.ingest(many_rules(450))
    assert not first.complete
    assert "re-run" in first.error
    assert (first.embedded, first.remaining, first.corpus_size) == (200, 250, 200)
    assert c.store.get_meta("rules_corpus")["rule_count"] == 200

    c.gateway.embed = real_embed
    second = c.ingestor.ingest(many_rules(450))
    assert second.complete
    assert (second.inserted, second.unchanged, second.embedded) == (250, 200, 250)
    assert second.corpus_size == 450


def test_changing_the_embedding_model_reembeds_everything():
    c = local_container()
    first = c.ingestor.ingest(SPEC_RULES)
    c.settings.embedding_model = "text-embedding-005"
    second = c.ingestor.ingest(SPEC_RULES)
    assert (second.updated, second.embedded) == (3, 3)
    assert second.corpus_version != first.corpus_version
