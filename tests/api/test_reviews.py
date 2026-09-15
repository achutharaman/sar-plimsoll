import pytest
from fastapi.testclient import TestClient

from sar_plimsoll.api.app import create_app
from tests.helpers import SPEC_RULES, VULNERABLE_PY, local_container

ALICE = {"Authorization": "Bearer dev:alice"}
BOB = {"Authorization": "Bearer dev:bob"}
ADMIN = {"Authorization": "Bearer dev:root:admin"}


@pytest.fixture
def container():
    return local_container()


@pytest.fixture
def client(container):
    return TestClient(create_app(container))


def submit(client, headers=ALICE, content=VULNERABLE_PY, filename="app.py"):
    return client.post(
        "/v1/reviews", json={"filename": filename, "content": content}, headers=headers
    )


def test_submit_returns_202_then_poll_returns_the_result(client):
    response = submit(client)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert response.headers["Location"] == f"/v1/reviews/{body['id']}"

    review = client.get(f"/v1/reviews/{body['id']}", headers=ALICE).json()
    assert review["status"] == "done"
    assert 1 <= review["result"]["score"]["overall"] <= 10
    assert review["rubric_version"] == "v1"
    cost = review["cost"]
    assert cost["cache_hit"] is False
    assert cost["escalated"] is True
    assert cost["cost_usd"] > 0
    assert cost["input_tokens"] > 0 and cost["output_tokens"] > 0
    assert "source_path" not in review and "cache_key" not in review


def test_same_file_twice_gives_identical_score_from_cache_at_zero_cost(client, container):
    client.post("/admin/rules:ingest", files={"file": ("rules.csv", SPEC_RULES)}, headers=ADMIN)

    first_id = submit(client).json()["id"]
    first = client.get(f"/v1/reviews/{first_id}", headers=ALICE).json()

    second_response = submit(client)
    assert second_response.status_code == 200
    second = second_response.json()

    assert second["id"] != first["id"]
    assert second["result"]["score"] == first["result"]["score"]
    assert second["result"]["findings"] == first["result"]["findings"]
    assert second["cost"]["cache_hit"] is True
    assert second["cost"]["cost_usd"] == 0
    assert second["cost"]["source_review_id"] == first_id


def test_rule_ingestion_invalidates_the_cache(client):
    submit(client)
    client.post("/admin/rules:ingest", files={"file": ("rules.csv", SPEC_RULES)}, headers=ADMIN)
    after = submit(client)
    assert after.status_code == 202  # corpus version changed → new key → fresh review
    review = client.get(f"/v1/reviews/{after.json()['id']}", headers=ALICE).json()
    grounded = {rid for f in review["result"]["findings"] for rid in f["grounded_rule_ids"]}
    assert "3" in grounded


def test_reviews_are_tenant_scoped(client):
    review_id = submit(client).json()["id"]
    assert client.get(f"/v1/reviews/{review_id}", headers=BOB).status_code == 404
    assert client.get("/v1/reviews", headers=BOB).json()["reviews"] == []
    # Tenant-scoped cache: Bob's identical submission is reviewed, not served from Alice's entry.
    assert submit(client, headers=BOB).status_code == 202


def test_history_is_newest_first(client):
    first = submit(client, filename="a.py").json()["id"]
    second = submit(client, filename="b.py", content="def f():\n    return 1\n").json()["id"]
    history = client.get("/v1/reviews", headers=ALICE).json()["reviews"]
    assert [r["id"] for r in history] == [second, first]


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"filename": "big.py", "content": "x = 1\n" * 40_000}, 413, "file_too_large"),
        ({"filename": "notes.txt", "content": "hello"}, 422, "unsupported_language"),
        ({"filename": "empty.py", "content": "  "}, 422, "empty_file"),
    ],
)
def test_invalid_submissions_spend_no_tokens(client, container, payload, status, code):
    calls = []
    container.gateway.classify = lambda **kw: calls.append(kw)
    container.gateway.embed = lambda *a, **kw: calls.append(kw)
    response = client.post("/v1/reviews", json=payload, headers=ALICE)
    assert response.status_code == status
    assert response.json()["error"] == code
    assert calls == []


def ingest(client, csv: bytes):
    response = client.post("/admin/rules:ingest", files={"file": ("r.csv", csv)}, headers=ADMIN)
    assert response.status_code == 202
    job_url = response.headers["Location"]
    return client.get(job_url, headers=ADMIN).json()


def test_admin_ingest_runs_as_a_job_and_reports_rejected_rows(client):
    job = ingest(client, SPEC_RULES + b"3, security, duplicate\nbroken-row\n")
    assert job["status"] == "done"
    assert job["attempts"] == 1
    assert job["report"]["inserted"] == 3
    assert job["report"]["rejected_count"] == 2
    assert job["progress"] == {"committed_rows": 3, "remaining": 0}
    corpus = client.get("/admin/rules/corpus", headers=ADMIN).json()
    assert corpus["version"] == job["report"]["corpus_version"]


def test_ingest_retries_then_fails_with_progress(client, container):
    from sar_plimsoll.llm.backends.base import TransientLLMError

    calls = []

    def exhausted(*args, **kwargs):
        calls.append(1)
        raise TransientLLMError("429", rate_limited=True)

    container.gateway.embed = exhausted
    job = ingest(client, SPEC_RULES)
    assert job["status"] == "failed"
    assert job["attempts"] == container.settings.ingest_max_retries + 1
    assert len(calls) == container.settings.ingest_max_retries + 1
    assert job["report"]["complete"] is False
    assert job["report"]["remaining"] == 3
    assert "re-run" in job["error"]


def test_unknown_ingest_job_is_404(client):
    assert client.get("/admin/rules/ingest-jobs/nope", headers=ADMIN).status_code == 404


def test_ingest_job_status_requires_admin(client):
    assert client.get("/admin/rules/ingest-jobs/x", headers=ALICE).status_code == 403
