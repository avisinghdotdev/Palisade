import json
from collections.abc import AsyncIterator, Iterator
from typing import Literal, final
from uuid import uuid4

import httpx2
import pytest
from pydantic import BaseModel, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.main import app
from palisade_api.paypal.auth import SANDBOX_BASE_URL, PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.webhooks.router import webhook_store, webhook_verifier
from palisade_api.webhooks.store import ReceivedEvent, WebhookStore
from palisade_api.webhooks.verification import WebhookVerifier

pytestmark = pytest.mark.anyio

WEBHOOK_ID = "9HH12345AB678901C"
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


def delivery(event_id: str) -> bytes:
    """A capture event as PayPal sends it; each test uses its own id, since the database is shared."""
    event = {
        "id": event_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "create_time": "2026-10-09T10:00:00.000Z",
        "resource_type": "capture",
        "resource_version": "2.0",
        "summary": "Payment completed for $ 95.0 USD",
        "resource": {"id": "5GS7344552642101Y", "amount": {"value": "95.00"}},
    }
    return json.dumps(event, separators=(",", ":")).encode()


@pytest.fixture
def event_id() -> str:
    return f"WH-TEST-{uuid4().hex}"


@pytest.fixture
def paypal(sessions: async_sessionmaker[AsyncSession]) -> Iterator[PayPalStub]:
    stub = PayPalStub()
    http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(stub.handle), base_url=SANDBOX_BASE_URL
    )
    verifier = WebhookVerifier(
        PayPalClient(http, PayPalAuth(http, "client-id", "client-secret")), WEBHOOK_ID
    )
    store = WebhookStore(sessions)
    app.dependency_overrides[webhook_verifier] = lambda: verifier
    app.dependency_overrides[webhook_store] = lambda: store
    yield stub
    app.dependency_overrides.clear()


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://palisade.test"
    ) as http:
        yield http


async def listed_ids(client: httpx2.AsyncClient) -> list[str]:
    response = await client.get("/api/webhooks/events")
    return [item.event.id for item in RECEIVED_EVENTS.validate_json(response.content)]


async def test_verified_delivery_is_stored_and_listed(
    paypal: PayPalStub, client: httpx2.AsyncClient, event_id: str
) -> None:
    raw_event = delivery(event_id)

    response = await client.post(
        "/api/webhooks/paypal", content=raw_event, headers=SIGNATURE_HEADERS
    )

    assert response.status_code == 204
    postback = Postback.model_validate_json(paypal.postbacks[0])
    assert postback.webhook_id == WEBHOOK_ID
    assert postback.transmission_id == SIGNATURE_HEADERS["paypal-transmission-id"]
    assert raw_event in paypal.postbacks[0]
    listed = RECEIVED_EVENTS.validate_json(
        (await client.get("/api/webhooks/events")).content
    )
    [stored] = [item for item in listed if item.event.id == event_id]
    assert stored.event.resource["amount"] == {"value": "95.00"}


async def test_rejected_signature_is_a_400_and_nothing_is_stored(
    paypal: PayPalStub, client: httpx2.AsyncClient, event_id: str
) -> None:
    paypal.verification_status = "FAILURE"

    response = await client.post(
        "/api/webhooks/paypal", content=delivery(event_id), headers=SIGNATURE_HEADERS
    )

    assert response.status_code == 400
    assert event_id not in await listed_ids(client)


async def test_repeat_delivery_is_stored_once(
    paypal: PayPalStub, client: httpx2.AsyncClient, event_id: str
) -> None:
    raw_event = delivery(event_id)

    first = await client.post(
        "/api/webhooks/paypal", content=raw_event, headers=SIGNATURE_HEADERS
    )
    second = await client.post(
        "/api/webhooks/paypal", content=raw_event, headers=SIGNATURE_HEADERS
    )

    assert (first.status_code, second.status_code) == (204, 204)
    assert len(paypal.postbacks) == 2
    assert (await listed_ids(client)).count(event_id) == 1


async def test_delivery_without_signature_headers_is_a_422(
    paypal: PayPalStub, client: httpx2.AsyncClient, event_id: str
) -> None:
    response = await client.post("/api/webhooks/paypal", content=delivery(event_id))

    assert response.status_code == 422
    assert paypal.postbacks == []
