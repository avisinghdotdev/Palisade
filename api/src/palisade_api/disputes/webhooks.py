"""Dispute events from PayPal: each one is a cue to read the dispute as PayPal holds it now."""

import structlog
from pydantic import BaseModel

from palisade_api.disputes.service import Disputes, NotTheShops
from palisade_api.webhooks.store import WebhookEvent
from palisade_api.webhooks.tasks import Handler

EVENTS = (
    "CUSTOMER.DISPUTE.CREATED",
    "CUSTOMER.DISPUTE.UPDATED",
    "CUSTOMER.DISPUTE.RESOLVED",
)

logger = structlog.stdlib.get_logger(__name__)


class DisputeResource(BaseModel):
    dispute_id: str


def handlers(disputes: Disputes) -> dict[str, Handler]:
    """One handler for the three events, which reads the live dispute.

    A delivery's body may be older than one delivered before it; the live dispute never is. A
    dispute on a payment that is none of the shop's orders is noted and left to PayPal.
    """

    async def sync(event: WebhookEvent) -> None:
        dispute_id = DisputeResource.model_validate(event.resource).dispute_id
        try:
            _ = await disputes.sync(dispute_id, "webhook")
        except NotTheShops as error:
            logger.info("dispute_ignored", dispute_id=dispute_id, reason=str(error))

    return dict.fromkeys(EVENTS, sync)
