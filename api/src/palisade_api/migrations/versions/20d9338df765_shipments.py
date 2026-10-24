"""Shipments: the tracking added to an order's PayPal capture.

Revision ID: 20d9338df765
Revises: ead411705912
Create Date: 2026-10-24 13:15:03.300688
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20d9338df765"
down_revision: str | Sequence[str] | None = "ead411705912"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("carrier", sa.Text(), nullable=False),
        sa.Column("tracking_number", sa.Text(), nullable=False),
        sa.Column("paypal_tracker_id", sa.Text(), nullable=False),
        sa.Column(
            "shipped_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_shipments_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
        sa.UniqueConstraint("order_id", name=op.f("uq_shipments_order_id")),
    )


def downgrade() -> None:
    op.drop_table("shipments")
