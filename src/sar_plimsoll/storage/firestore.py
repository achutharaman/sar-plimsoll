"""Firestore review store.

users/{uid}/reviews/{review_id}   tenant-scoped review records (history = recency query)
review_cache/{cache_key}          cached review results
users/{uid}/usage/{YYYY-MM-DD}    daily fresh-review counter (cost cap)
system/{name}                     small metadata (rules corpus version)
ingest_jobs/{job_id}              rule ingestion job status and report
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import firestore

from sar_plimsoll.storage.gcp_clients import Lazy, shared_credentials
from sar_plimsoll.storage.interfaces import TransientStorageError

_TRANSIENT = (
    gexc.ServiceUnavailable,
    gexc.DeadlineExceeded,
    gexc.Aborted,
    gexc.InternalServerError,
)


def _guard(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _TRANSIENT as exc:
            raise TransientStorageError(type(exc).__name__) from exc

    return wrapper


class FirestoreReviewStore:
    def __init__(self, project: str, database: str = "(default)"):
        self._lazy = Lazy(
            lambda: firestore.Client(
                project=project, database=database, credentials=shared_credentials()
            )
        )

    @property
    def _db(self) -> firestore.Client:
        return self._lazy.get()

    def _review_ref(self, uid: str, review_id: str):
        return self._db.collection("users").document(uid).collection("reviews").document(review_id)

    @_guard
    def create_review(self, uid: str, review: dict[str, Any]) -> None:
        self._review_ref(uid, review["id"]).create(review)

    @_guard
    def get_review(self, uid: str, review_id: str) -> dict[str, Any] | None:
        snap = self._review_ref(uid, review_id).get()
        return snap.to_dict() if snap.exists else None

    @_guard
    def update_review(self, uid: str, review_id: str, fields: dict[str, Any]) -> None:
        self._review_ref(uid, review_id).update(fields)

    @_guard
    def list_reviews(self, uid: str, limit: int) -> list[dict[str, Any]]:
        query = (
            self._db.collection("users")
            .document(uid)
            .collection("reviews")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [snap.to_dict() for snap in query.stream()]

    @_guard
    def list_review_summaries(self, uid: str, limit: int) -> list[dict[str, Any]]:
        # Field mask: history views never download findings or results.
        query = (
            self._db.collection("users")
            .document(uid)
            .collection("reviews")
            .select(
                [
                    "id",
                    "filename",
                    "language",
                    "status",
                    "created_at",
                    "score",
                    "finding_counts",
                    "rules_corpus_version",
                    "cost.cost_usd",
                    "cost.cache_hit",
                    "cost.escalated",
                ]
            )
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [snap.to_dict() for snap in query.stream()]

    @_guard
    def reserve_daily_review(self, uid: str, day: str, limit: int) -> bool:
        ref = self._db.collection("users").document(uid).collection("usage").document(day)

        @firestore.transactional
        def reserve(tx) -> bool:
            snap = ref.get(transaction=tx)
            used = (snap.to_dict() or {}).get("reviews", 0) if snap.exists else 0
            if used >= limit:
                return False
            tx.set(ref, {"reviews": used + 1, "updated_at": datetime.now(UTC)})
            return True

        return reserve(self._db.transaction())

    @_guard
    def claim_review(self, uid: str, review_id: str, lease_s: int) -> dict[str, Any] | None:
        ref = self._review_ref(uid, review_id)

        @firestore.transactional
        def claim(tx) -> dict[str, Any] | None:
            snap = ref.get(transaction=tx)
            if not snap.exists:
                return None
            review = snap.to_dict()
            now = datetime.now(UTC)
            lease = review.get("lease_expires_at")
            expired = review["status"] == "running" and lease is not None and lease < now
            if review["status"] != "queued" and not expired:
                return None
            update = {
                "status": "running",
                "attempts": review.get("attempts", 0) + 1,
                "lease_expires_at": now + timedelta(seconds=lease_s),
            }
            tx.update(ref, update)
            return review | update

        return claim(self._db.transaction())

    @_guard
    def get_cache(self, key: str) -> dict[str, Any] | None:
        snap = self._db.collection("review_cache").document(key).get()
        return snap.to_dict() if snap.exists else None

    @_guard
    def put_cache(self, key: str, entry: dict[str, Any]) -> None:
        self._db.collection("review_cache").document(key).set(entry)

    @_guard
    def get_meta(self, name: str) -> dict[str, Any] | None:
        snap = self._db.collection("system").document(name).get()
        return snap.to_dict() if snap.exists else None

    @_guard
    def set_meta(self, name: str, value: dict[str, Any]) -> None:
        self._db.collection("system").document(name).set(value)

    @_guard
    def create_ingest_job(self, job: dict[str, Any]) -> None:
        self._db.collection("ingest_jobs").document(job["id"]).create(job)

    @_guard
    def get_ingest_job(self, job_id: str) -> dict[str, Any] | None:
        snap = self._db.collection("ingest_jobs").document(job_id).get()
        return snap.to_dict() if snap.exists else None

    @_guard
    def update_ingest_job(self, job_id: str, fields: dict[str, Any]) -> None:
        self._db.collection("ingest_jobs").document(job_id).update(fields)
