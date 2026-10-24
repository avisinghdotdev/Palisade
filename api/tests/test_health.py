from importlib.metadata import version

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from palisade_api.database import create_engine
from palisade_api.main import app, database
from palisade_api.settings import DatabaseSettings

# Nothing listens on port 1, so the connection is refused or times out.
UNREACHABLE = "postgresql://palisade@127.0.0.1:1/palisade"


def test_health_reports_ok_and_package_version() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": version("palisade-api")}


async def ready_answer(
    client: httpx2.AsyncClient, engine: AsyncEngine
) -> httpx2.Response:
    app.dependency_overrides[database] = lambda: engine
    try:
        return await client.get("/api/ready")
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.anyio
async def test_ready_when_the_database_answers(client: httpx2.AsyncClient) -> None:
    # pydantic-settings fills the field from the environment; pyright only sees the synthesized constructor.
    settings = DatabaseSettings()  # pyright: ignore[reportCallIssue]

    response = await ready_answer(
        client, create_engine(settings.database_url.get_secret_value())
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.anyio
async def test_not_ready_when_the_database_does_not_answer(
    client: httpx2.AsyncClient,
) -> None:
    response = await ready_answer(client, create_engine(UNREACHABLE))

    assert response.status_code == 503
