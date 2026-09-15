"""Rule ingestion as a job: the API stores the CSV and launches a run; the run writes its report.

In GCP each run is a Cloud Run Job execution — a fresh container with its own memory and no request
timeout — so a 30k-row file can neither exhaust a long-lived API instance nor outlive an HTTP
request. Locally the same runner executes in-process.
"""

import logging
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from sar_plimsoll.rules.ingest import RulesIngestor
from sar_plimsoll.storage.interfaces import BlobStore, IngestLauncher, ReviewStore

log = logging.getLogger(__name__)

REJECTED_SAMPLE = 100  # keeps the job document far below Firestore's 1 MiB limit


class IngestLaunchFailed(Exception):
    pass


class IngestJobService:
    def __init__(self, *, store: ReviewStore, blobs: BlobStore, launcher: IngestLauncher):
        self._store = store
        self._blobs = blobs
        self._launcher = launcher

    def submit(self, *, uid: str, filename: str, data: bytes) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        job: dict[str, Any] = {
            "id": job_id,
            "status": "queued",
            "uid": uid,
            "filename": PurePosixPath(filename.replace("\\", "/")).name or "rules.csv",
            "size_bytes": len(data),
            "source_path": f"ingest/{job_id}.csv",
            "created_at": datetime.now(UTC),
            "attempts": 0,
            "progress": None,
            "report": None,
            "error": None,
            "execution": None,
        }
        self._blobs.put(job["source_path"], data)
        self._store.create_ingest_job(job)
        try:
            execution = self._launcher.launch(job_id)
        except Exception as exc:
            log.exception("ingest launch failed", extra={"job_id": job_id})
            self._store.update_ingest_job(
                job_id,
                {"status": "failed", "error": f"could not start ingest job: {type(exc).__name__}"},
            )
            raise IngestLaunchFailed(str(exc)) from exc
        if execution:
            self._store.update_ingest_job(job_id, {"execution": execution})
        return self._store.get_ingest_job(job_id) or job

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._store.get_ingest_job(job_id)


class IngestJobRunner:
    def __init__(self, *, store: ReviewStore, blobs: BlobStore, ingestor: RulesIngestor):
        self._store = store
        self._blobs = blobs
        self._ingestor = ingestor

    def run(self, job_id: str, *, attempt: int, max_retries: int) -> bool:
        """Process one attempt. Returns True when the job needs no further attempts."""
        job = self._store.get_ingest_job(job_id)
        if job is None:
            log.error("ingest job not found", extra={"job_id": job_id})
            return True
        if job["status"] == "done":
            return True

        self._store.update_ingest_job(
            job_id,
            {
                "status": "running",
                "attempts": attempt + 1,
                "started_at": datetime.now(UTC),
                "error": None,
            },
        )
        final_attempt = attempt >= max_retries
        try:
            data = self._blobs.get(job["source_path"])
            report = self._ingestor.ingest(
                data,
                on_checkpoint=lambda committed, remaining: self._store.update_ingest_job(
                    job_id, {"progress": {"committed_rows": committed, "remaining": remaining}}
                ),
            )
        except Exception as exc:
            log.exception("ingest job crashed", extra={"job_id": job_id, "attempt": attempt})
            self._store.update_ingest_job(
                job_id,
                {
                    "status": "failed" if final_attempt else "retrying",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                },
            )
            return final_attempt

        body = report.to_dict()
        body["rejected"] = [vars(r) for r in report.rejected[:REJECTED_SAMPLE]]
        if report.complete:
            self._store.update_ingest_job(
                job_id, {"status": "done", "report": body, "completed_at": datetime.now(UTC)}
            )
            return True
        self._store.update_ingest_job(
            job_id,
            {
                # Checkpoints already committed stay; the next attempt resumes with the remaining rows.
                "status": "failed" if final_attempt else "retrying",
                "report": body,
                "error": report.error,
                "completed_at": datetime.now(UTC) if final_attempt else None,
            },
        )
        return final_attempt
