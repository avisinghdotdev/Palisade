import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from palisade_api.webhooks.store import ReceivedEvent, WebhookEvent, WebhookStore
from palisade_api.webhooks.verification import Transmission, WebhookVerifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def webhook_verifier(request: Request) -> WebhookVerifier:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a WebhookVerifier here.
    verifier: WebhookVerifier = request.state.webhook_verifier  # pyright: ignore[reportAny]
    return verifier


def webhook_store(request: Request) -> WebhookStore:
    store: WebhookStore = request.state.webhook_store  # pyright: ignore[reportAny]
    return store


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

    A 2xx stops PayPal's retries, a 400 answers a signature PayPal rejects, and an error reaching
    PayPal surfaces as a 500 so PayPal delivers the event again later.
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
    outcome = "stored" if await store.add(event) else "already stored"
    logger.info("paypal webhook %s %s %s", event.event_type, event.id, outcome)


@router.get("/events", operation_id="webhook_events")
async def webhook_events(
    store: Annotated[WebhookStore, Depends(webhook_store)],
) -> list[ReceivedEvent]:
    return await store.events()
