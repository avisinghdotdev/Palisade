"""Disputes PayPal opened on the store's payments, and activity rows that name a dispute.

Both are new: the table starts empty, and the activity column starts null on every row.

Revision ID: 424db02f8b79
Revises: ce8dd440c797
Create Date: 2026-11-02 00:54:29.321172
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "424db02f8b79"
down_revision: str | Sequence[str] | None = "ce8dd440c797"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "disputes",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("capture_id", sa.Text(), nullable=True),
        sa.Column("buyer_transaction_id", sa.Text(), nullable=True),
        sa.Column("custom", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("seller_response_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paypal_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_disputes_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disputes")),
    )
    op.create_index(op.f("ix_disputes_order_id"), "disputes", ["order_id"])
    op.create_index(op.f("ix_disputes_opened_at"), "disputes", ["opened_at"])
    op.add_column("activity", sa.Column("dispute_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_activity_dispute_id_disputes"),
        "activity",
        "disputes",
        ["dispute_id"],
        ["id"],
    )
    op.create_index(op.f("ix_activity_dispute_id"), "activity", ["dispute_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_activity_dispute_id"), table_name="activity")
    op.drop_constraint(
        op.f("fk_activity_dispute_id_disputes"), "activity", type_="foreignkey"
    )
    op.drop_column("activity", "dispute_id")
    op.drop_index(op.f("ix_disputes_opened_at"), table_name="disputes")
    op.drop_index(op.f("ix_disputes_order_id"), table_name="disputes")
    op.drop_table("disputes")
