"""Every table, imported in one place so the shared metadata Alembic compares against knows them all."""

from palisade_api.agents.models import AgentNonceRecord, AgentRecord
from palisade_api.database import Base
from palisade_api.mandates.models import (
    MandateRecord,
    PasskeyChallengeRecord,
    PasskeyRecord,
)
from palisade_api.orders.models import (
    OrderEventRecord,
    OrderLineRecord,
    OrderRecord,
    PayPalCallRecord,
    ShipmentRecord,
)
from palisade_api.policy.models import PolicyRecord
from palisade_api.shop.models import CartLineRecord, CartRecord, ProductRecord
from palisade_api.wallets.models import WalletRecord
from palisade_api.webhooks.models import WebhookEventRecord

__all__ = [
    "AgentNonceRecord",
    "AgentRecord",
    "Base",
    "CartLineRecord",
    "CartRecord",
    "MandateRecord",
    "OrderEventRecord",
    "OrderLineRecord",
    "OrderRecord",
    "PasskeyChallengeRecord",
    "PasskeyRecord",
    "PayPalCallRecord",
    "PolicyRecord",
    "ProductRecord",
    "ShipmentRecord",
    "WalletRecord",
    "WebhookEventRecord",
]
