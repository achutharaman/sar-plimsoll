from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from sar_plimsoll.api.app import create_app
from tests.helpers import CLEAN_PY, SPEC_RULES, VULNERABLE_PY, local_container

ALICE = {"Authorization": "Bearer dev:alice"}
ADMIN = {"Authorization": "Bearer dev:root:admin"}


@pytest.fixture
def container():
    return local_container()


@pytest.fixture
def client(container):
    return TestClient(create_app(container), raise_server_exceptions=False)


def submit(client, content=VULNERABLE_PY, filename="app.py", headers=ALICE):
    return client.post(
        "/v1/reviews", json={"filename": filename, "content": content}, headers=headers
    )


def test_config_is_public_and_has_no_secrets(client):
    body = client.get("/v1/config").json()
    assert set(body["firebase"]) == {"apiKey", "authDomain", "projectId"}
    assert "python" in body["languages"]
    assert body["limits"]["max_file_bytes"] > 0


def test_history_groups_resubmissions_of_the_same_file(client):
    submit(client, VULNERABLE_PY)
    submit(client, CLEAN_PY)  # same filename, improved content
    submit(client, CLEAN_PY)  # cache hit
    submit(client, CLEAN_PY, filename="other.py")
    body = client.get("/v1/history", headers=ALICE).json()
    app = next(f for f in body["files"] if f["filename"] == "app.py")
    assert app["reviews"] == 3
    assert app["latest_score"] == 10.0 and app["first_score"] < 10.0
    assert app["delta"] > 0
    assert [p["cache_hit"] for p in app["points"]] == [False, False, True]
    assert sum(d["reviews"] for d in body["trend"]) == 4


def test_stats_reflect_cache_hits_escalations_and_cost(client):
    submit(client, VULNERABLE_PY)  # escalates (critical finding)
    submit(client, VULNERABLE_PY)  # cache hit
    submit(client, CLEAN_PY, filename="clean.py")
    me = client.get("/v1/stats", headers=ALICE).json()
    assert "system" not in me
    t = me["me"]["traffic"]
    assert t["reviews"] == 3 and t["cache_hits"] == 1
    assert t["escalation_rate"] == 0.5
    assert t["mean_cost_usd"] > 0
    assert t["cost_per_1000_reviews_usd"] == pytest.approx(t["mean_cost_usd"] * 1000, rel=1e-3)
    assert "system" in client.get("/v1/stats", headers=ADMIN).json()


def test_insights_surface_recurring_grounded_rules(client):
    client.post("/admin/rules:ingest", files={"file": ("r.csv", SPEC_RULES)}, headers=ADMIN)
    submit(client, VULNERABLE_PY)
    submit(client, VULNERABLE_PY.replace("find_user", "find_account"), filename="accounts.py")
    body = client.get("/v1/insights", headers=ALICE).json()
    assert body["dimensions"][0]["dimension"] in {"security", "performance", "formatting"}
    top = {r["rule_id"]: r for r in body["rules"]}
    assert top["3"]["hits"] == 2 and top["3"]["reviews"] == 2
    assert top["3"]["description"].startswith("Never interpolate")
    assert body["weekly"][0]["reviews"] == 2


def test_daily_limit_blocks_fresh_reviews_but_not_cache_hits(container):
    container.settings.daily_review_limit = 2
    client = TestClient(create_app(container))
    assert submit(client, "x1 = 1\n", "a.py").status_code == 202
    assert submit(client, "x2 = 2\n", "b.py").status_code == 202
    blocked = submit(client, "x3 = 3\n", "c.py")
    assert blocked.status_code == 429
    assert submit(client, "x1 = 1\n", "a.py").status_code == 200  # cached: no model spend
    assert submit(client, "x4 = 4\n", "d.py", headers=ADMIN).status_code == 202  # admins exempt


def test_failed_review_can_be_retried(container):
    from sar_plimsoll.worker.orchestrator import ReviewFailed

    real_run = container.orchestrator.run
    container.orchestrator.run = lambda *a, **kw: (_ for _ in ()).throw(ReviewFailed("unparseable"))
    client = TestClient(create_app(container))
    review_id = submit(client).json()["id"]
    assert client.get(f"/v1/reviews/{review_id}", headers=ALICE).json()["status"] == "failed"

    container.orchestrator.run = real_run
    retried = client.post(f"/v1/reviews/{review_id}:retry", headers=ALICE)
    assert retried.status_code == 202
    review = client.get(f"/v1/reviews/{review_id}", headers=ALICE).json()
    assert review["status"] == "done" and review["score"] is not None
    assert client.post(f"/v1/reviews/{review_id}:retry", headers=ALICE).status_code == 409


def test_stalled_reviews_are_flagged_and_retryable(container):
    class LostQueue:
        def enqueue_review(self, uid, review_id, generation=0):
            pass  # simulates a task that never ran

    container.submissions._queue = LostQueue()
    client = TestClient(create_app(container))
    review_id = submit(client).json()["id"]
    assert client.get(f"/v1/reviews/{review_id}", headers=ALICE).json()["stalled"] is False

    old = datetime.now(UTC) - timedelta(minutes=container.settings.review_stall_minutes + 1)
    container.store.update_review("alice", review_id, {"created_at": old})
    assert client.get(f"/v1/reviews/{review_id}", headers=ALICE).json()["stalled"] is True
    assert client.post(f"/v1/reviews/{review_id}:retry", headers=ALICE).status_code == 202


def test_oversized_body_is_rejected_before_parsing(client, container):
    huge = "x" * (container.settings.max_file_bytes * 6 + 20_000)
    response = client.post(
        "/v1/reviews",
        content=f'{{"filename":"a.py","content":"{huge}"}}',
        headers=ALICE | {"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


def test_unexpected_errors_return_json_without_internals(client, container):
    container.store.list_review_summaries = lambda *a: (_ for _ in ()).throw(
        RuntimeError("secret detail")
    )
    response = client.get("/v1/history", headers=ALICE)
    assert response.status_code == 500
    assert response.json() == {"error": "internal", "detail": "unexpected error"}


def test_transient_storage_errors_return_503(client, container):
    from sar_plimsoll.storage.interfaces import TransientStorageError

    container.store.list_reviews = lambda *a: (_ for _ in ()).throw(
        TransientStorageError("Unavailable")
    )
    response = client.get("/v1/reviews", headers=ALICE)
    assert response.status_code == 503 and response.headers["Retry-After"] == "5"


def test_me_reports_admin_from_the_server(client):
    assert client.get("/v1/me", headers=ADMIN).json() == {
        "uid": "root",
        "email": None,
        "admin": True,
    }
    assert client.get("/v1/me", headers=ALICE).json()["admin"] is False
    assert client.get("/v1/me").status_code == 401
