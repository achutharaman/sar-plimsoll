"""Worker service: the Cloud Tasks HTTP target.

Cloud Run IAM guards this service (only the tasks service account holds run.invoker), so requests
that reach the handler were authenticated by the platform with an OIDC token.
"""

from fastapi import FastAPI, Response, status
from pydantic import BaseModel

from sar_plimsoll import __version__
from sar_plimsoll.config import get_settings
from sar_plimsoll.logging_setup import configure_logging
from sar_plimsoll.wiring import Container, build_container


class ReviewTask(BaseModel):
    uid: str
    review_id: str


def create_worker_app(container: Container | None = None) -> FastAPI:
    app = FastAPI(title="sar-plimsoll-worker", version=__version__)
    if container is None:
        configure_logging()
        container = build_container(get_settings())
    app.state.container = container

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/health/parsers")
    def parser_check(response: Response) -> dict:
        from sar_plimsoll.review.chunking import parser_health

        results = parser_health()
        if any(v != "ok" for v in results.values()):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return results

    @app.post("/tasks/review")
    def run_review(task: ReviewTask, response: Response) -> dict:
        outcome = container.runner.process(task.uid, task.review_id)
        if outcome == "retry":
            # Non-2xx tells Cloud Tasks to redeliver with backoff.
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"outcome": outcome}

    return app


def __getattr__(name: str):
    if name == "app":
        return create_worker_app()
    raise AttributeError(name)
