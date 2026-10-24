from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from palisade_api.webhooks.store import (
    ReceivedEvent,
    UnknownEvent,
    WebhookEvent,
    WebhookStore,
)
from palisade_api.webhooks.verification import Transmission, WebhookVerifier

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def webhook_verifier(request: Request) -> WebhookVerifier:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a WebhookVerifier here.
    verifier: WebhookVerifier = request.state.webhook_verifier  # pyright: ignore[reportAny]
    return verifier


def webhook_store(request: Request) -> WebhookStore:
    store: WebhookStore = request.state.webhook_store  # pyright: ignore[reportAny]
    return store


class QueuedJob(BaseModel):
    job_id: int


@router.post(
    "/paypal", status_code=status.HTTP_204_NO_CONTENT, operation_id="paypal_webhook"
)
async def receive_paypal_webhook(
    request: Request,
    paypal_transmission_id: Annotated[str, Header()],
    paypal_transmission_time: Annotated[str, Header()],
    paypal_transmission_sig: Annotated[str, Header()],
    paypal_cert_url: Annotated[str, Header()],
    paypal_auth_algo: Annotated[str, Header()],
    verifier: Annotated[WebhookVerifier, Depends(webhook_verifier)],
    store: Annotated[WebhookStore, Depends(webhook_store)],
) -> None:
    """Accept a delivery once PayPal confirms its signature; a repeat delivery is a no-op.

    The event is stored and its processing queued in one transaction before the 2xx that stops
    PayPal's retries. A 400 answers a signature PayPal rejects, and an error reaching PayPal or the
    database surfaces as a 500 so PayPal delivers the event again later.
    """
    raw_event = await request.body()
    transmission = Transmission(
        id=paypal_transmission_id,
        time=paypal_transmission_time,
        signature=paypal_transmission_sig,
        cert_url=paypal_cert_url,
        auth_algo=paypal_auth_algo,
    )
    if not await verifier.verify(transmission, raw_event):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "PayPal rejected the delivery's signature"
        )
    event = WebhookEvent.model_validate_json(raw_event)
    job_id = await store.add(event)
    logger.info(
        "paypal_webhook_received",
        event_id=event.id,
        event_type=event.event_type,
        repeat_delivery=job_id is None,
        job_id=job_id,
    )


@router.get("/events", operation_id="webhook_events")
async def webhook_events(
    store: Annotated[WebhookStore, Depends(webhook_store)],
) -> list[ReceivedEvent]:
    return await store.events()


@router.post(
    "/events/{event_id}/replay",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="replay_webhook_event",
)
async def replay_webhook_event(
    event_id: str, store: Annotated[WebhookStore, Depends(webhook_store)]
) -> QueuedJob:
    """Queue a stored event for processing again, as if it had just arrived."""
    try:
        job_id = await store.replay(event_id)
    except UnknownEvent as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return QueuedJob(job_id=job_id)
