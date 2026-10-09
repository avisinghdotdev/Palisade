import json
from collections.abc import Iterator
from typing import Literal, final

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter

from palisade_api.main import app
from palisade_api.paypal.auth import SANDBOX_BASE_URL, PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.webhooks.router import webhook_store, webhook_verifier
from palisade_api.webhooks.store import ReceivedEvent, WebhookStore
from palisade_api.webhooks.verification import WebhookVerifier

WEBHOOK_ID = "9HH12345AB678901C"
EVENT = {
    "id": "WH-1",
    "event_type": "PAYMENT.CAPTURE.COMPLETED",
    "create_time": "2026-10-09T10:00:00.000Z",
    "resource_type": "capture",
    "resource_version": "2.0",
    "summary": "Payment completed for $ 95.0 USD",
    "resource": {"id": "5GS7344552642101Y", "amount": {"value": "95.00"}},
}
RAW_EVENT = json.dumps(EVENT, separators=(",", ":")).encode()
SIGNATURE_HEADERS = {
    "paypal-transmission-id": "a2d7b9c0-1",
    "paypal-transmission-time": "2026-10-09T10:00:01Z",
    "paypal-transmission-sig": "c2lnbmF0dXJl",
    "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "paypal-auth-algo": "SHA256withRSA",
}
RECEIVED_EVENTS = TypeAdapter(list[ReceivedEvent])


class Postback(BaseModel):
    webhook_id: str
    transmission_id: str


@final
class PayPalStub:
    """Answers the token call and the verification postback, keeping each postback body."""

    def __init__(self) -> None:
        self.verification_status: Literal["SUCCESS", "FAILURE"] = "SUCCESS"
        self.postbacks: list[bytes] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx2.Response(
                200,
                json={
                    "access_token": "sandbox-access-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "",
                    "app_id": "APP-1",
                },
            )
        self.postbacks.append(request.content)
        return httpx2.Response(
            200, json={"verification_status": self.verification_status}
        )


@pytest.fixture
def paypal() -> Iterator[PayPalStub]:
    stub = PayPalStub()
    http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(stub.handle), base_url=SANDBOX_BASE_URL
    )
    verifier = WebhookVerifier(
        PayPalClient(http, PayPalAuth(http, "client-id", "client-secret")), WEBHOOK_ID
    )
    store = WebhookStore()
    app.dependency_overrides[webhook_verifier] = lambda: verifier
    app.dependency_overrides[webhook_store] = lambda: store
    yield stub
    app.dependency_overrides.clear()


def listed_events(client: TestClient) -> list[ReceivedEvent]:
    return RECEIVED_EVENTS.validate_json(client.get("/api/webhooks/events").content)


def test_verified_delivery_is_stored_and_listed(paypal: PayPalStub) -> None:
    client = TestClient(app)

    response = client.post(
        "/api/webhooks/paypal", content=RAW_EVENT, headers=SIGNATURE_HEADERS
    )

    assert response.status_code == 204
    postback = Postback.model_validate_json(paypal.postbacks[0])
    assert postback.webhook_id == WEBHOOK_ID
    assert postback.transmission_id == SIGNATURE_HEADERS["paypal-transmission-id"]
    assert RAW_EVENT in paypal.postbacks[0]
    events = listed_events(client)
    assert [item.event.id for item in events] == ["WH-1"]
    assert events[0].event.resource["amount"] == {"value": "95.00"}


def test_rejected_signature_is_a_400_and_nothing_is_stored(paypal: PayPalStub) -> None:
    paypal.verification_status = "FAILURE"
    client = TestClient(app)

    response = client.post(
        "/api/webhooks/paypal", content=RAW_EVENT, headers=SIGNATURE_HEADERS
    )

    assert response.status_code == 400
    assert listed_events(client) == []


def test_repeat_delivery_is_stored_once(paypal: PayPalStub) -> None:
    client = TestClient(app)

    first = client.post(
        "/api/webhooks/paypal", content=RAW_EVENT, headers=SIGNATURE_HEADERS
    )
    second = client.post(
        "/api/webhooks/paypal", content=RAW_EVENT, headers=SIGNATURE_HEADERS
    )

    assert (first.status_code, second.status_code) == (204, 204)
    assert len(paypal.postbacks) == 2
    assert len(listed_events(client)) == 1


def test_delivery_without_signature_headers_is_a_422(paypal: PayPalStub) -> None:
    response = TestClient(app).post("/api/webhooks/paypal", content=RAW_EVENT)

    assert response.status_code == 422
    assert paypal.postbacks == []
