import pytest
from fastapi.testclient import TestClient

from sar_plimsoll.api.app import create_app
from tests.helpers import local_container, local_settings


@pytest.fixture
def client():
    return TestClient(create_app(local_container()))


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer ", "Bearer not-a-dev-token"])
def test_reviews_require_a_valid_bearer_token(client, header):
    headers = {"Authorization": header} if header else {}
    response = client.post(
        "/v1/reviews", json={"filename": "a.py", "content": "x"}, headers=headers
    )
    assert response.status_code == 401


def test_admin_routes_reject_non_admins(client):
    response = client.post(
        "/admin/rules:ingest",
        files={"file": ("r.csv", b"1,security,x\n")},
        headers={"Authorization": "Bearer dev:alice"},
    )
    assert response.status_code == 403


def test_dev_auth_is_refused_on_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "plimsoll-api")
    with pytest.raises(ValueError, match="not allowed on Cloud Run"):
        local_settings()
