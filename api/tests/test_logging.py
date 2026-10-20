import logging
from collections.abc import Iterator
from io import StringIO

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from palisade_api.logging import RequestLogMiddleware, configure_logging

EVENT = TypeAdapter(dict[str, object])
RAY = "230b030023ae2822-SJC"


@pytest.fixture
def json_log() -> Iterator[StringIO]:
    stream = StringIO()
    configure_logging("INFO", "json", stream)
    yield stream
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def item(item_id: str) -> dict[str, str]:
    return {"item_id": item_id}


def boom() -> None:
    raise RuntimeError("boom")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)
    app.add_api_route("/items/{item_id}", item)
    app.add_api_route("/boom", boom)
    return TestClient(app)


def events(stream: StringIO, name: str = "api_request") -> list[dict[str, object]]:
    """Parsed JSON lines named `name`; the test client's own HTTP log lines are skipped."""
    parsed = [EVENT.validate_json(line) for line in stream.getvalue().splitlines()]
    return [event for event in parsed if event["event"] == name]


def test_each_request_logs_one_event_carrying_the_ids(
    json_log: StringIO, client: TestClient
) -> None:
    response = client.get(
        "/items/42?page=2", headers={"x-request-id": "abc-123", "cf-ray": RAY}
    )

    assert response.headers["x-request-id"] == "abc-123"
    [event] = events(json_log)
    assert event["event"] == "api_request"
    assert event["request_id"] == "abc-123"
    assert event["cf_ray"] == RAY
    assert event["route"] == "/items/{item_id}"
    assert event["path"] == "/items/42"
    assert event["status_code"] == 200
    assert event["outcome"] == "success"
    assert "page" not in str(event)


def test_a_malformed_request_id_is_replaced_not_truncated(
    json_log: StringIO, client: TestClient
) -> None:
    response = client.get("/items/1", headers={"x-request-id": "x" * 65})

    [event] = events(json_log)
    assert response.headers["x-request-id"] == event["request_id"]
    assert str(event["request_id"]).startswith("req_")


def test_an_unhandled_error_is_logged_with_its_traceback_and_re_raised(
    json_log: StringIO, client: TestClient
) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        client.get("/boom")

    [event] = events(json_log)
    assert event["status_code"] == 500
    assert event["outcome"] == "error"
    assert event["level"] == "error"
    assert "exception" in event


def test_a_client_error_is_not_an_error(json_log: StringIO, client: TestClient) -> None:
    response = client.get("/missing")

    assert response.status_code == 404
    [event] = events(json_log)
    assert event["outcome"] == "client_error"
    assert event["route"] is None


def test_stdlib_records_render_as_the_same_json(json_log: StringIO) -> None:
    logging.getLogger("uvicorn.error").info("Started server process [%d]", 7)

    [event] = events(json_log, "Started server process [7]")
    assert event["logger"] == "uvicorn.error"
    assert event["level"] == "info"
    assert "timestamp" in event
