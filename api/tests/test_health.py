from importlib.metadata import version

from fastapi.testclient import TestClient

from palisade_api.main import app


def test_health_reports_ok_and_package_version() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": version("palisade-api")}
