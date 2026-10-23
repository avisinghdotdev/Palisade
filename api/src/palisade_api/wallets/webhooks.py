"""Vault events: a buyer removing the merchant from their PayPal account ends the wallet."""

import structlog

from palisade_api.wallets.service import Wallets
from palisade_api.webhooks.store import WebhookEvent
from palisade_api.webhooks.tasks import Handler

logger = structlog.stdlib.get_logger(__name__)


def handlers(wallets: Wallets) -> dict[str, Handler]:
    async def token_deleted(event: WebhookEvent) -> None:
        token_id = event.resource["id"]
        if not isinstance(token_id, str):
            raise TypeError(
                f"payment token id in event {event.id} is {type(token_id).__name__}"
            )
        found = await wallets.mark_deleted(token_id)
        logger.info("wallet_token_deleted", event_id=event.id, wallet_found=found)

    return {"VAULT.PAYMENT-TOKEN.DELETED": token_deleted}
