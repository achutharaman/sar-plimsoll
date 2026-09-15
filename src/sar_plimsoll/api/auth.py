"""Firebase ID-token verification. Dev mode exists only for offline local runs."""

from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class User:
    uid: str
    email: str | None
    admin: bool


class TokenVerifier(Protocol):
    def verify(self, token: str) -> User: ...


class InvalidToken(Exception):
    pass


class FirebaseVerifier:
    def __init__(self, project_id: str, admin_uids: list[str]):
        import firebase_admin
        from firebase_admin import auth

        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={"projectId": project_id})
        self._auth = auth
        self._admins = set(admin_uids)

    def verify(self, token: str) -> User:
        try:
            claims = self._auth.verify_id_token(token)
        except Exception as exc:
            raise InvalidToken(type(exc).__name__) from exc
        uid = claims["uid"]
        return User(
            uid=uid,
            email=claims.get("email"),
            admin=claims.get("admin") is True or uid in self._admins,
        )


class DevVerifier:
    """Accepts `dev:<uid>` or `dev:<uid>:admin`. Refused on Cloud Run by Settings validation."""

    def verify(self, token: str) -> User:
        parts = token.split(":")
        if len(parts) not in (2, 3) or parts[0] != "dev" or not parts[1]:
            raise InvalidToken("expected dev:<uid>[:admin]")
        return User(uid=parts[1], email=None, admin=len(parts) == 3 and parts[2] == "admin")


def current_user(request: Request) -> User:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return request.app.state.container.verifier.verify(token)
    except InvalidToken:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid token", headers={"WWW-Authenticate": "Bearer"}
        ) from None


def admin_user(request: Request) -> User:
    user = current_user(request)
    if not user.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user
