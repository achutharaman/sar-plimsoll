"""Cloud Run Job entrypoint: `python -m sar_plimsoll.worker.ingest_job`.

Cloud Run sets CLOUD_RUN_TASK_ATTEMPT (0-based) and retries the task on a non-zero exit, up to the
job's max_retries. Every committed checkpoint survives a failed attempt, so a retry resumes.
"""

import logging
import os
import sys

from sar_plimsoll.config import get_settings
from sar_plimsoll.logging_setup import configure_logging
from sar_plimsoll.wiring import build_container

log = logging.getLogger("sar_plimsoll.worker.ingest_job")


def main() -> int:
    configure_logging()
    job_id = os.environ.get("PLIMSOLL_INGEST_JOB_ID", "")
    if not job_id:
        log.error("PLIMSOLL_INGEST_JOB_ID is not set")
        return 2
    settings = get_settings()
    attempt = int(os.environ.get("CLOUD_RUN_TASK_ATTEMPT", "0"))
    container = build_container(settings)
    finished = container.ingest_runner.run(
        job_id, attempt=attempt, max_retries=settings.ingest_max_retries
    )
    job = container.store.get_ingest_job(job_id) or {}
    log.info("ingest job attempt finished", extra={"job_id": job_id, "status": job.get("status")})
    return 0 if job.get("status") == "done" or (finished and job.get("status") != "failed") else 1


if __name__ == "__main__":
    sys.exit(main())
