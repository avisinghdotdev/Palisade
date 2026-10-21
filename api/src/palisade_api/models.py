"""Every table, imported in one place so the shared metadata Alembic compares against knows them all."""

from palisade_api.database import Base
from palisade_api.shop.models import ProductRecord
from palisade_api.webhooks.models import WebhookEventRecord

__all__ = ["Base", "ProductRecord", "WebhookEventRecord"]
