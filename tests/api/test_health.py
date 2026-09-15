from fastapi.testclient import TestClient

from sar_plimsoll.api.app import create_app
from tests.helpers import local_container


def test_health():
    client = TestClient(create_app(local_container()))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
