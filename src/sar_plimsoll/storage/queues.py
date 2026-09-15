"""Task dispatch: Cloud Tasks in GCP, an in-process runner locally."""

import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

JobFn = Callable[[str, str], str]


class InlineQueue:
    """Runs jobs in-process, retrying 'retry' outcomes. `synchronous=True` finishes before returning."""

    def __init__(self, *, synchronous: bool, max_attempts: int, backoff_s: float = 0.5):
        self._run: JobFn | None = None
        self._sync = synchronous
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._pool = None if synchronous else ThreadPoolExecutor(max_workers=2)

    def bind(self, run: JobFn) -> None:
        self._run = run

    def enqueue_review(self, uid: str, review_id: str, generation: int = 0) -> None:
        if self._run is None:
            raise RuntimeError("InlineQueue is not bound to a job runner")
        if self._pool is None:
            self._drive(uid, review_id)
        else:
            self._pool.submit(self._drive, uid, review_id)

    def _drive(self, uid: str, review_id: str) -> None:
        for attempt in range(1, self._max_attempts + 1):
            outcome = self._run(uid, review_id)
            if outcome != "retry":
                return
            if not self._sync:
                time.sleep(self._backoff_s * attempt)


class CloudTasksQueue:
    def __init__(
        self,
        *,
        project: str,
        region: str,
        queue: str,
        worker_url: str,
        service_account: str,
        dispatch_deadline_s: int = 1800,
    ):
        from google.cloud import tasks_v2
        from google.protobuf import duration_pb2

        from sar_plimsoll.storage.gcp_clients import Lazy, shared_credentials

        self._tasks_v2 = tasks_v2
        self._lazy = Lazy(lambda: tasks_v2.CloudTasksClient(credentials=shared_credentials()))
        self._parent = f"projects/{project}/locations/{region}/queues/{queue}"
        self._url = worker_url.rstrip("/") + "/tasks/review"
        self._audience = worker_url.rstrip("/")
        self._sa = service_account
        self._deadline = duration_pb2.Duration(seconds=dispatch_deadline_s)

    def enqueue_review(self, uid: str, review_id: str, generation: int = 0) -> None:
        t = self._tasks_v2
        task = t.Task(
            # Deterministic name: a duplicate enqueue for the same review is rejected by Cloud Tasks.
            # A manual retry bumps the generation, because Cloud Tasks keeps names reserved for
            # hours after a task completes.
            name=f"{self._parent}/tasks/review-{review_id}-g{generation}",
            dispatch_deadline=self._deadline,
            http_request=t.HttpRequest(
                http_method=t.HttpMethod.POST,
                url=self._url,
                headers={"Content-Type": "application/json"},
                body=json.dumps({"uid": uid, "review_id": review_id}).encode(),
                oidc_token=t.OidcToken(service_account_email=self._sa, audience=self._audience),
            ),
        )
        try:
            self._lazy.get().create_task(parent=self._parent, task=task)
        except Exception as exc:
            from google.api_core.exceptions import AlreadyExists

            if isinstance(exc, AlreadyExists):
                log.info("task already enqueued", extra={"review_id": review_id})
                return
            raise


class InlineIngestLauncher:
    """Runs the ingest job in-process with the same retry semantics as a Cloud Run Job."""

    def __init__(self, *, max_retries: int):
        self._run = None
        self._max_retries = max_retries

    def bind(self, run) -> None:
        self._run = run

    def launch(self, job_id: str) -> str | None:
        if self._run is None:
            raise RuntimeError("InlineIngestLauncher is not bound to a runner")
        for attempt in range(self._max_retries + 1):
            if self._run(job_id, attempt=attempt, max_retries=self._max_retries):
                break
        return None


class CloudRunJobLauncher:
    """Starts one execution of the ingest Cloud Run Job with the job ID passed as an env override."""

    def __init__(self, *, project: str, region: str, job_name: str):
        from google.cloud import run_v2

        from sar_plimsoll.storage.gcp_clients import Lazy, shared_credentials

        self._run_v2 = run_v2
        self._lazy = Lazy(lambda: run_v2.JobsClient(credentials=shared_credentials()))
        self._name = f"projects/{project}/locations/{region}/jobs/{job_name}"

    def launch(self, job_id: str) -> str | None:
        r = self._run_v2
        request = r.RunJobRequest(
            name=self._name,
            overrides=r.RunJobRequest.Overrides(
                container_overrides=[
                    r.RunJobRequest.Overrides.ContainerOverride(
                        env=[r.EnvVar(name="PLIMSOLL_INGEST_JOB_ID", value=job_id)]
                    )
                ]
            ),
        )
        operation = self._lazy.get().run_job(
            request=request
        )  # returns once the execution is created
        metadata = getattr(operation, "metadata", None)
        return getattr(metadata, "name", None) or None
