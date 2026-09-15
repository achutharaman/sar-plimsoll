"""Public API service."""

import logging
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from sar_plimsoll import __version__
from sar_plimsoll.analytics.history import daily_trend, file_histories
from sar_plimsoll.analytics.stats import build_stats
from sar_plimsoll.api.auth import User, admin_user, current_user
from sar_plimsoll.api.submission import (
    DailyLimitExceeded,
    EnqueueFailed,
    RetryNotAllowed,
    ReviewNotFound,
    is_stalled,
)
from sar_plimsoll.config import get_settings
from sar_plimsoll.logging_setup import configure_logging
from sar_plimsoll.review.languages import SUPPORTED_LANGUAGES, SubmissionError
from sar_plimsoll.rules.identity import rules_label, short_version
from sar_plimsoll.rules.ingest import CORPUS_META
from sar_plimsoll.rules.ingest_jobs import IngestLaunchFailed
from sar_plimsoll.storage.interfaces import TransientStorageError
from sar_plimsoll.wiring import Container, build_container

log = logging.getLogger(__name__)

_PUBLIC_FIELDS = (
    "id",
    "status",
    "filename",
    "language",
    "sha256",
    "size_bytes",
    "line_count",
    "created_at",
    "completed_at",
    "rubric_version",
    "prompt_version",
    "rules_corpus_version",
    "rules_corpus_size",
    "models",
    "score",
    "result",
    "cost",
    "error",
)
_JOB_FIELDS = (
    "id",
    "status",
    "filename",
    "size_bytes",
    "created_at",
    "started_at",
    "completed_at",
    "attempts",
    "progress",
    "report",
    "error",
)


class ReviewSubmission(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: str
    language: str | None = None


def present_job(job: dict[str, Any]) -> dict[str, Any]:
    return {k: job.get(k) for k in _JOB_FIELDS}


def _error(code: str, detail: str, status_code: int, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status_code, headers=headers)


def create_app(container: Container | None = None) -> FastAPI:
    app = FastAPI(title="sar-plimsoll", version=__version__)
    if container is None:
        configure_logging()
        container = build_container(get_settings())
    app.state.container = container
    settings = container.settings
    # JSON escaping can inflate source up to 6x (\uXXXX); anything larger cannot be a valid file.
    max_review_body = settings.max_file_bytes * 6 + 16_384

    def present(review: dict[str, Any]) -> dict[str, Any]:
        body = {k: review.get(k) for k in _PUBLIC_FIELDS}
        body["stalled"] = is_stalled(review, settings.review_stall_minutes)
        body["rules_label"] = rules_label(review.get("rules_corpus_version"))
        body["rules_short"] = short_version(review.get("rules_corpus_version"))
        return body

    # ------------------------------------------------------------------ failure handling
    @app.middleware("http")
    async def limit_review_body(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/v1/reviews":
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > max_review_body:
                return _error("payload_too_large", "request body exceeds the file size limit", 413)
        return await call_next(request)

    @app.exception_handler(SubmissionError)
    def _submission_error(_: Request, exc: SubmissionError) -> JSONResponse:
        return _error(exc.code, str(exc), exc.status)

    @app.exception_handler(TransientStorageError)
    def _storage_unavailable(_: Request, exc: TransientStorageError) -> JSONResponse:
        log.warning("storage temporarily unavailable", extra={"error": str(exc)})
        return _error(
            "unavailable",
            "storage temporarily unavailable; retry shortly",
            503,
            {"Retry-After": "5"},
        )

    @app.exception_handler(Exception)
    def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log with context, return nothing internal to the caller.
        log.exception("unhandled error", extra={"path": request.url.path})
        return _error("internal", "unexpected error", 500)

    # ------------------------------------------------------------------ public
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/config")
    def client_config() -> dict:
        project = settings.gcp_project
        return {
            "firebase": {
                "apiKey": settings.firebase_web_api_key,
                "authDomain": f"{project}.firebaseapp.com" if project else "",
                "projectId": project,
            },
            "auth_mode": settings.auth_mode,
            "limits": {
                "max_file_bytes": settings.max_file_bytes,
                "max_file_lines": settings.max_file_lines,
                "daily_review_limit": settings.daily_review_limit,
            },
            "languages": sorted(SUPPORTED_LANGUAGES),
            "rubric_version": container.rubric.version,
        }

    @app.get("/v1/me")
    def me(user: Annotated[User, Depends(current_user)]) -> dict:
        # The UI asks rather than decoding claims: admin can come from a claim or admin_uids.
        return {"uid": user.uid, "email": user.email, "admin": user.admin}

    # ------------------------------------------------------------------ reviews
    @app.post("/v1/reviews", status_code=status.HTTP_202_ACCEPTED)
    def submit_review(
        body: ReviewSubmission, response: Response, user: Annotated[User, Depends(current_user)]
    ) -> dict:
        try:
            review, outcome = container.submissions.submit(
                uid=user.uid,
                filename=body.filename,
                content=body.content.encode("utf-8"),
                language=body.language,
                exempt=user.admin,
            )
        except DailyLimitExceeded as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from None
        except EnqueueFailed:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "review queue unavailable"
            ) from None
        if outcome == "cached":
            response.status_code = status.HTTP_200_OK
        else:
            response.headers["Location"] = f"/v1/reviews/{review['id']}"
        return present(review)

    @app.get("/v1/reviews/{review_id}")
    def get_review(review_id: str, user: Annotated[User, Depends(current_user)]) -> dict:
        review = container.store.get_review(user.uid, review_id)
        if review is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "review not found")
        return present(review)

    @app.post("/v1/reviews/{review_id}:retry", status_code=status.HTTP_202_ACCEPTED)
    def retry_review(review_id: str, user: Annotated[User, Depends(current_user)]) -> dict:
        try:
            return present(container.submissions.retry(uid=user.uid, review_id=review_id))
        except ReviewNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "review not found") from None
        except RetryNotAllowed as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        except EnqueueFailed:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "review queue unavailable"
            ) from None

    @app.get("/v1/reviews")
    def list_reviews(
        user: Annotated[User, Depends(current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict:
        reviews = container.store.list_reviews(user.uid, limit)
        return {"reviews": [present(r) | {"result": None} for r in reviews]}

    # ------------------------------------------------------------------ analytics
    @app.get("/v1/history")
    def history(
        user: Annotated[User, Depends(current_user)],
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> dict:
        summaries = container.store.list_review_summaries(user.uid, limit)
        return {"files": file_histories(summaries), "trend": daily_trend(summaries)}

    @app.get("/v1/stats")
    def stats(
        user: Annotated[User, Depends(current_user)],
        days: Annotated[int, Query(ge=1, le=365)] = 30,
    ) -> dict:
        queries = container.analytics_queries
        body = {"me": build_stats(queries.mode_aggregates(user.uid, days), scope="me", days=days)}
        if user.admin:
            body["system"] = build_stats(
                queries.mode_aggregates(None, days), scope="system", days=days
            )
        return body

    @app.get("/v1/insights")
    def insights(
        user: Annotated[User, Depends(current_user)],
        days: Annotated[int, Query(ge=1, le=365)] = 90,
    ) -> dict:
        return container.analytics_queries.recurring(user.uid, days) | {"window_days": days}

    # ------------------------------------------------------------------ admin
    @app.post("/admin/rules:ingest", status_code=status.HTTP_202_ACCEPTED)
    async def ingest_rules(
        file: Annotated[UploadFile, File()],
        response: Response,
        user: Annotated[User, Depends(admin_user)],
    ) -> dict:
        data = await file.read(settings.max_ingest_bytes + 1)
        if len(data) > settings.max_ingest_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "CSV too large")
        try:
            job = await run_in_threadpool(
                container.ingest_jobs.submit,
                uid=user.uid,
                filename=file.filename or "rules.csv",
                data=data,
            )
        except IngestLaunchFailed:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "could not start ingest job"
            ) from None
        response.headers["Location"] = f"/admin/rules/ingest-jobs/{job['id']}"
        return present_job(job)

    @app.get("/admin/rules/ingest-jobs/{job_id}")
    def get_ingest_job(job_id: str, _: Annotated[User, Depends(admin_user)]) -> dict:
        job = container.ingest_jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ingest job not found")
        return present_job(job)

    @app.get("/admin/rules/corpus")
    def corpus(_: Annotated[User, Depends(admin_user)]) -> dict:
        meta = container.store.get_meta(CORPUS_META) or {"version": "empty", "rule_count": 0}
        return meta | {
            "label": rules_label(meta["version"]),
            "short": short_version(meta["version"]),
        }

    return app


def __getattr__(name: str):
    # `uvicorn sar_plimsoll.api.app:app` builds the app lazily, so importing this module in tests
    # never constructs production wiring.
    if name == "app":
        return create_app()
    raise AttributeError(name)
