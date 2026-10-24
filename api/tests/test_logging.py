import logging
from collections.abc import Iterator
from io import StringIO

import pytest
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from paypal_stub import PayPalStub, json_answer
from pydantic import TypeAdapter

from palisade_api.logging import RequestLogMiddleware, configure_logging
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient

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


def healthy() -> dict[str, str]:
    return {"status": "ok"}


def unhealthy() -> None:
    raise HTTPException(503, "Not serving.")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)
    app.add_api_route("/items/{item_id}", item)
    app.add_api_route("/boom", boom)
    app.add_api_route("/api/health", healthy)
    return TestClient(app)


def events_of(stream: StringIO) -> list[dict[str, object]]:
    return [EVENT.validate_json(line) for line in stream.getvalue().splitlines()]


def events(stream: StringIO, name: str = "api_request") -> list[dict[str, object]]:
    """Parsed JSON lines named `name`; the test client's own HTTP log lines are skipped."""
    return [event for event in events_of(stream) if event["event"] == name]


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


def test_a_passing_probe_writes_no_event(
    json_log: StringIO, client: TestClient
) -> None:
    client.get("/api/health")

    assert events(json_log) == []


def test_a_failing_probe_is_logged(json_log: StringIO) -> None:
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)
    app.add_api_route("/api/health", unhealthy)

    TestClient(app).get("/api/health")

    [event] = events(json_log)
    assert event["status_code"] == 503


@pytest.mark.anyio
async def test_each_paypal_call_logs_its_own_debug_id(json_log: StringIO) -> None:
    paypal = PayPalStub()
    paypal.on("POST", "/v2/checkout/orders", json_answer(201, {"id": "ORDER-1"}))
    paypal.on("GET", "/v2/checkout/orders/ORDER-1", json_answer(200, {}))
    http = paypal.client()
    client = PayPalClient(http, PayPalAuth(http, "client-id", "client-secret"))

    await client.post("/v2/checkout/orders", {}, request_id="request-1")
    await client.get("/v2/checkout/orders/ORDER-1")

    calls = events(json_log, "paypal_call")
    assert [(call["path"], call["paypal_debug_id"]) for call in calls] == [
        ("/v1/oauth2/token", None),
        ("/v2/checkout/orders", "debug-201"),
        ("/v2/checkout/orders/ORDER-1", "debug-200"),
    ]
    assert all(event["logger"] != "httpx2" for event in events_of(json_log))


def test_stdlib_records_render_as_the_same_json(json_log: StringIO) -> None:
    logging.getLogger("uvicorn.error").info("Started server process [%d]", 7)

    [event] = events(json_log, "Started server process [7]")
    assert event["logger"] == "uvicorn.error"
    assert event["level"] == "info"
    assert "timestamp" in event
