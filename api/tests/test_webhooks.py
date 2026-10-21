import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Literal, final
from uuid import uuid4

import httpx2
import pytest
from procrastinate.jobs import Job
from procrastinate.testing import InMemoryConnector
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.jobs import jobs
from palisade_api.main import app
from palisade_api.paypal.auth import SANDBOX_BASE_URL, PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.webhooks.router import webhook_store, webhook_verifier
from palisade_api.webhooks.store import ReceivedEvent, WebhookStore
from palisade_api.webhooks.tasks import (
    RETRY,
    enqueue_processing,
    process_webhook_event,
)
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
QUEUED_JOBS = text(
    "SELECT task_name, status FROM procrastinate_jobs WHERE args ->> 'event_id' = :event_id"
)
JOB_ROWS = TypeAdapter(list[tuple[str, str]])


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
def store(sessions: async_sessionmaker[AsyncSession]) -> WebhookStore:
    return WebhookStore(sessions, enqueue_processing)


@pytest.fixture
def paypal(store: WebhookStore) -> Iterator[PayPalStub]:
    stub = PayPalStub()
    http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(stub.handle), base_url=SANDBOX_BASE_URL
    )
    verifier = WebhookVerifier(
        PayPalClient(http, PayPalAuth(http, "client-id", "client-secret")), WEBHOOK_ID
    )
    app.dependency_overrides[webhook_verifier] = lambda: verifier
    app.dependency_overrides[webhook_store] = lambda: store
    yield stub
    app.dependency_overrides.clear()


@pytest.fixture
async def queue() -> AsyncIterator[InMemoryConnector]:
    """Jobs kept in memory instead of the database, so the worker can run them inside the test."""
    connector = InMemoryConnector()
    with jobs.replace_connector(connector):
        async with jobs.open_async():
            yield connector


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://palisade.test"
    ) as http:
        yield http


async def run_queued_jobs(store: WebhookStore) -> None:
    await jobs.run_worker_async(
        wait=False,
        install_signal_handlers=False,
        additional_context={"webhook_store": store},
    )


async def listed(client: httpx2.AsyncClient) -> list[ReceivedEvent]:
    response = await client.get("/api/webhooks/events")
    return RECEIVED_EVENTS.validate_json(response.content)


async def listed_event(client: httpx2.AsyncClient, event_id: str) -> ReceivedEvent:
    [stored] = [item for item in await listed(client) if item.event.id == event_id]
    return stored


async def queued_jobs(
    sessions: async_sessionmaker[AsyncSession], event_id: str
) -> list[tuple[str, str]]:
    """The jobs the database holds for the event, inside the test's transaction."""
    async with sessions() as session:
        rows = await session.execute(QUEUED_JOBS, {"event_id": event_id})
    return JOB_ROWS.validate_python([tuple(row) for row in rows])


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
    stored = await listed_event(client, event_id)
    assert stored.event.resource["amount"] == {"value": "95.00"}
    assert stored.processed_at is None


@pytest.mark.usefixtures("paypal")
async def test_stored_delivery_is_queued_in_the_same_transaction(
    client: httpx2.AsyncClient,
    event_id: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    response = await client.post(
        "/api/webhooks/paypal", content=delivery(event_id), headers=SIGNATURE_HEADERS
    )

    assert response.status_code == 204
    assert await queued_jobs(sessions, event_id) == [
        (process_webhook_event.name, "todo")
    ]


async def test_rejected_signature_is_a_400_and_nothing_is_stored(
    paypal: PayPalStub, client: httpx2.AsyncClient, event_id: str
) -> None:
    paypal.verification_status = "FAILURE"

    response = await client.post(
        "/api/webhooks/paypal", content=delivery(event_id), headers=SIGNATURE_HEADERS
    )

    assert response.status_code == 400
    assert event_id not in [item.event.id for item in await listed(client)]


async def test_repeat_delivery_is_stored_and_queued_once(
    paypal: PayPalStub,
    client: httpx2.AsyncClient,
    event_id: str,
    sessions: async_sessionmaker[AsyncSession],
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
    assert [item.event.id for item in await listed(client)].count(event_id) == 1
    assert len(await queued_jobs(sessions, event_id)) == 1


async def test_delivery_without_signature_headers_is_a_422(
    paypal: PayPalStub, client: httpx2.AsyncClient, event_id: str
) -> None:
    response = await client.post("/api/webhooks/paypal", content=delivery(event_id))

    assert response.status_code == 422
    assert paypal.postbacks == []


@pytest.mark.usefixtures("paypal")
async def test_the_worker_marks_the_event_processed(
    client: httpx2.AsyncClient,
    event_id: str,
    store: WebhookStore,
    queue: InMemoryConnector,
) -> None:
    await client.post(
        "/api/webhooks/paypal", content=delivery(event_id), headers=SIGNATURE_HEADERS
    )
    [job] = queue.jobs.values()
    assert (job["task_name"], job["args"]) == (
        process_webhook_event.name,
        {"event_id": event_id},
    )

    await run_queued_jobs(store)

    assert job["status"] == "succeeded"
    assert (await listed_event(client, event_id)).processed_at is not None


@pytest.mark.usefixtures("paypal")
async def test_replay_queues_a_stored_event_again(
    client: httpx2.AsyncClient,
    event_id: str,
    store: WebhookStore,
    queue: InMemoryConnector,
) -> None:
    await client.post(
        "/api/webhooks/paypal", content=delivery(event_id), headers=SIGNATURE_HEADERS
    )

    response = await client.post(f"/api/webhooks/events/{event_id}/replay")

    assert response.status_code == 202
    assert response.json() == {"job_id": 2}
    assert queue.jobs[2]["args"] == {"event_id": event_id}
    await run_queued_jobs(store)
    assert [job["status"] for job in queue.jobs.values()] == ["succeeded"] * 2


@pytest.mark.usefixtures("paypal")
async def test_replay_of_an_unknown_event_is_a_404(
    client: httpx2.AsyncClient, queue: InMemoryConnector
) -> None:
    response = await client.post("/api/webhooks/events/WH-TEST-unknown/replay")

    assert response.status_code == 404
    assert queue.jobs == {}


async def test_a_failed_job_is_retried_later(
    store: WebhookStore, queue: InMemoryConnector
) -> None:
    await process_webhook_event.defer_async(event_id="WH-TEST-unknown")

    await run_queued_jobs(store)

    [job] = queue.jobs.values()
    assert (job["status"], job["attempts"]) == ("todo", 1)
    wait = scheduled_at(job) - datetime.now(UTC)
    assert timedelta(seconds=3) < wait <= timedelta(seconds=4)


def scheduled_at(job: dict[str, object]) -> datetime:
    value = job["scheduled_at"]
    assert isinstance(value, datetime)
    return value


def test_retries_grow_and_stop_after_five() -> None:
    def retry_in(attempts: int) -> timedelta | None:
        job = Job(
            queue="default",
            lock=None,
            queueing_lock=None,
            task_name="t",
            attempts=attempts,
        )
        decision = RETRY.get_retry_decision(exception=RuntimeError(), job=job)
        if decision is None or decision.retry_at is None:
            return None
        return decision.retry_at - datetime.now(UTC)

    first, last = retry_in(0), retry_in(4)
    assert first is not None
    assert first <= timedelta(seconds=4)
    assert last is not None
    assert last > timedelta(seconds=1_020)
    assert retry_in(5) is None
