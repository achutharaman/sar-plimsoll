import os

import pytest

from sar_plimsoll.llm.backends.base import TransientLLMError
from sar_plimsoll.rules.ingest_jobs import IngestLaunchFailed
from sar_plimsoll.worker import ingest_job
from tests.helpers import SPEC_RULES, local_container


def many_rules(n: int) -> bytes:
    return "".join(f"{i},performance,Rule {i} about caching\n" for i in range(n)).encode()


def test_retry_resumes_from_committed_checkpoints():
    c = local_container(ingest_checkpoint_rows=100, embedding_batch_size=50, ingest_max_retries=2)
    real_embed = c.gateway.embed
    calls = {"n": 0}

    def fail_once_midway(texts, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise TransientLLMError("429", rate_limited=True)
        return real_embed(texts, **kw)

    c.gateway.embed = fail_once_midway
    job = c.ingest_jobs.submit(uid="admin", filename="big.csv", data=many_rules(450))

    assert job["status"] == "done"
    assert job["attempts"] == 2
    # Second attempt only embedded what the first attempt had not committed.
    assert job["report"]["unchanged"] == 200
    assert job["report"]["embedded"] == 250
    assert job["report"]["corpus_size"] == 450


def test_launch_failure_marks_job_failed():
    c = local_container()

    class Broken:
        def launch(self, job_id):
            raise RuntimeError("permission denied")

    c.ingest_jobs._launcher = Broken()
    with pytest.raises(IngestLaunchFailed):
        c.ingest_jobs.submit(uid="admin", filename="r.csv", data=SPEC_RULES)
    (job,) = c.store._ingest_jobs.values()
    assert job["status"] == "failed"
    assert "could not start" in job["error"]


def test_job_entrypoint_exit_codes(monkeypatch):
    c = local_container()
    c.store.create_ingest_job(
        {"id": "j1", "status": "queued", "source_path": "ingest/j1.csv", "attempts": 0}
    )
    c.blobs.put("ingest/j1.csv", SPEC_RULES)
    monkeypatch.setattr(ingest_job, "build_container", lambda settings: c)
    monkeypatch.setattr(ingest_job, "configure_logging", lambda *a: None)

    monkeypatch.setenv("PLIMSOLL_INGEST_JOB_ID", "j1")
    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", "0")
    assert ingest_job.main() == 0
    assert c.store.get_ingest_job("j1")["status"] == "done"

    monkeypatch.delenv("PLIMSOLL_INGEST_JOB_ID")
    assert ingest_job.main() == 2
    assert "PLIMSOLL_INGEST_JOB_ID" not in os.environ


def test_job_entrypoint_signals_retry_with_nonzero_exit(monkeypatch):
    c = local_container(ingest_max_retries=2)
    c.store.create_ingest_job(
        {"id": "j2", "status": "queued", "source_path": "ingest/j2.csv", "attempts": 0}
    )
    c.blobs.put("ingest/j2.csv", SPEC_RULES)

    def exhausted(*a, **kw):
        raise TransientLLMError("429", rate_limited=True)

    c.gateway.embed = exhausted
    monkeypatch.setattr(ingest_job, "build_container", lambda settings: c)
    monkeypatch.setattr(ingest_job, "configure_logging", lambda *a: None)
    monkeypatch.setattr(ingest_job, "get_settings", lambda: c.settings)
    monkeypatch.setenv("PLIMSOLL_INGEST_JOB_ID", "j2")

    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", "0")
    assert ingest_job.main() == 1
    assert c.store.get_ingest_job("j2")["status"] == "retrying"

    monkeypatch.setenv("CLOUD_RUN_TASK_ATTEMPT", "2")
    assert ingest_job.main() == 1
    assert c.store.get_ingest_job("j2")["status"] == "failed"
