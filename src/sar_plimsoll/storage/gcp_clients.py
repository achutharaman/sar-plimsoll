"""Shared credentials and lazily constructed Google Cloud clients.

Each client resolving credentials on its own cost ~0.6–1.1 s, and constructing all of them at
startup made the API's cold start ~10 s (measured). Credentials are now resolved once, and each
client is built on first use, so a request only pays for the clients it touches.
"""

import threading
from collections.abc import Callable
from functools import lru_cache

import google.auth

_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


@lru_cache(maxsize=1)
def shared_credentials():
    credentials, _ = google.auth.default(scopes=_SCOPES)
    return credentials


class Lazy[T]:
    def __init__(self, factory: Callable[[], T]):
        self._factory = factory
        self._value: T | None = None
        self._lock = threading.Lock()

    def get(self) -> T:
        if self._value is None:
            with self._lock:
                if self._value is None:
                    self._value = self._factory()
        return self._value


class LazyProxy:
    """Stands in for an adapter and builds it (importing its SDK) on first attribute access.

    Each service touches only some adapters — the API never calls a model, the worker never
    verifies Firebase tokens — so deferring construction keeps SDK imports out of cold starts.
    """

    def __init__(self, factory: Callable[[], object]):
        object.__setattr__(self, "_lazy_target", Lazy(factory))

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_lazy_target").get(), name)
