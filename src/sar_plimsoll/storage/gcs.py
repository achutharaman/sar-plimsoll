"""Cloud Storage for submitted source."""

from google.api_core import exceptions as gexc
from google.cloud import storage

from sar_plimsoll.storage.gcp_clients import Lazy, shared_credentials
from sar_plimsoll.storage.interfaces import TransientStorageError


class GcsBlobStore:
    def __init__(self, project: str, bucket: str):
        if not bucket:
            raise ValueError("PLIMSOLL_GCS_BUCKET is required when store_backend=gcp")
        self._lazy = Lazy(
            lambda: storage.Client(project=project, credentials=shared_credentials()).bucket(bucket)
        )

    @property
    def _bucket(self) -> storage.Bucket:
        return self._lazy.get()

    def put(self, path: str, data: bytes) -> None:
        blob = self._bucket.blob(path)
        if blob.exists():
            return  # content-addressed path: identical bytes already stored
        blob.upload_from_string(data, content_type="text/plain; charset=utf-8")

    def get(self, path: str) -> bytes:
        try:
            return self._bucket.blob(path).download_as_bytes()
        except (gexc.ServiceUnavailable, gexc.TooManyRequests, gexc.InternalServerError) as exc:
            raise TransientStorageError(type(exc).__name__) from exc
