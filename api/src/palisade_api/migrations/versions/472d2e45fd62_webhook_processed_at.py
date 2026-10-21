"""Webhook events record when their processing job finished.

Revision ID: 472d2e45fd62
Revises: 52d5714b38c1
Create Date: 2026-10-21 07:23:01.531994
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "472d2e45fd62"
down_revision: str | Sequence[str] | None = "52d5714b38c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "webhook_events",
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhook_events", "processed_at")
