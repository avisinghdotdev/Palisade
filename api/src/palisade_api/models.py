"""Every table, imported in one place so the shared metadata Alembic compares against knows them all."""

from palisade_api.database import Base
from palisade_api.orders.models import (
    OrderEventRecord,
    OrderLineRecord,
    OrderRecord,
    PayPalCallRecord,
)
from palisade_api.shop.models import CartLineRecord, CartRecord, ProductRecord
from palisade_api.wallets.models import WalletRecord
from palisade_api.webhooks.models import WebhookEventRecord

__all__ = [
    "Base",
    "CartLineRecord",
    "CartRecord",
    "OrderEventRecord",
    "OrderLineRecord",
    "OrderRecord",
    "PayPalCallRecord",
    "ProductRecord",
    "WalletRecord",
    "WebhookEventRecord",
]
