from fastapi.testclient import TestClient

from sar_plimsoll.api.app import create_app
from sar_plimsoll.llm.backends.base import TransientLLMError
from sar_plimsoll.storage.queues import InlineQueue
from sar_plimsoll.worker.app import create_worker_app
from tests.helpers import VULNERABLE_PY, local_container

ALICE = {"Authorization": "Bearer dev:alice"}


class RecordingQueue:
    def __init__(self):
        self.tasks = []

    def enqueue_review(self, uid, review_id, generation=0):
        self.tasks.append((uid, review_id, generation))


def queued_review(container):
    queue = RecordingQueue()
    container.submissions._queue = queue
    api = TestClient(create_app(container))
    review_id = api.post(
        "/v1/reviews", json={"filename": "app.py", "content": VULNERABLE_PY}, headers=ALICE
    ).json()["id"]
    return api, queue, review_id


def test_worker_endpoint_processes_a_task():
    c = local_container()
    api, _, review_id = queued_review(c)
    worker = TestClient(create_worker_app(c))

    response = worker.post("/tasks/review", json={"uid": "alice", "review_id": review_id})
    assert response.json() == {"outcome": "done"}
    assert api.get(f"/v1/reviews/{review_id}", headers=ALICE).json()["status"] == "done"
    rows = c.analytics.finding_rows
    assert rows and all(r["review_id"] == review_id for r in rows)
    (review_row,) = c.analytics.review_rows
    assert review_row["status"] == "done" and review_row["cache_hit"] is False
    assert review_row["cost_usd"] > 0 and review_row["cost_usd_standard"] >= review_row["cost_usd"]


def test_duplicate_delivery_is_skipped():
    c = local_container()
    _, _, review_id = queued_review(c)
    worker = TestClient(create_worker_app(c))
    worker.post("/tasks/review", json={"uid": "alice", "review_id": review_id})
    again = worker.post("/tasks/review", json={"uid": "alice", "review_id": review_id})
    assert again.json() == {"outcome": "skipped"}


def test_transient_failure_returns_503_so_cloud_tasks_retries():
    c = local_container()
    _, _, review_id = queued_review(c)
    c.orchestrator.run = lambda *a, **kw: (_ for _ in ()).throw(TransientLLMError("503"))
    worker = TestClient(create_worker_app(c))

    response = worker.post("/tasks/review", json={"uid": "alice", "review_id": review_id})
    assert response.status_code == 503
    assert c.store.get_review("alice", review_id)["status"] == "queued"


def test_gives_up_after_max_attempts():
    c = local_container(task_max_attempts=2)
    _, _, review_id = queued_review(c)
    c.orchestrator.run = lambda *a, **kw: (_ for _ in ()).throw(TransientLLMError("503"))
    worker = TestClient(create_worker_app(c))

    outcomes = [
        worker.post("/tasks/review", json={"uid": "alice", "review_id": review_id}).json()
        for _ in range(2)
    ]
    assert [o["outcome"] for o in outcomes] == ["retry", "failed"]
    assert c.store.get_review("alice", review_id)["status"] == "failed"


def test_analytics_failure_does_not_fail_the_review():
    c = local_container()
    _, _, review_id = queued_review(c)
    c.runner._analytics.write_review = lambda row: (_ for _ in ()).throw(RuntimeError("bq down"))
    assert c.runner.process("alice", review_id) == "done"


def test_background_inline_queue_completes():
    c = local_container()
    queue = InlineQueue(synchronous=False, max_attempts=1)
    queue.bind(c.runner.process)
    c.submissions._queue = queue
    api = TestClient(create_app(c))
    review_id = api.post(
        "/v1/reviews", json={"filename": "app.py", "content": VULNERABLE_PY}, headers=ALICE
    ).json()["id"]
    queue._pool.shutdown(wait=True)
    assert c.store.get_review("alice", review_id)["status"] == "done"
