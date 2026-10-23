"""Wallets, carts, orders, and the PayPal calls and status events behind each order.

Revision ID: f96d5b16984f
Revises: 44b07abe537d
Create Date: 2026-10-23 13:21:25.627867
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f96d5b16984f"
down_revision: str | Sequence[str] | None = "44b07abe537d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def created_at(name: str = "created_at") -> sa.Column[datetime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("paypal_customer_id", sa.Text(), nullable=False),
        sa.Column("payment_token_id", sa.Text(), nullable=False),
        sa.Column("payer_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("demo", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallets")),
        sa.UniqueConstraint(
            "payment_token_id", name=op.f("uq_wallets_payment_token_id")
        ),
    )
    op.create_table(
        "carts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        created_at(),
        created_at("updated_at"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["wallets.id"], name=op.f("fk_carts_wallet_id_wallets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_carts")),
    )
    op.create_table(
        "cart_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("size", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        created_at(),
        sa.ForeignKeyConstraint(
            ["cart_id"],
            ["carts.id"],
            name=op.f("fk_cart_lines_cart_id_carts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_cart_lines_product_id_products"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cart_lines")),
        sa.UniqueConstraint(
            "cart_id", "product_id", "size", name=op.f("uq_cart_lines_cart_id")
        ),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Text(), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("custom_id", sa.Text(), nullable=True),
        sa.Column("paypal_order_id", sa.Text(), nullable=True),
        sa.Column("authorization_id", sa.Text(), nullable=True),
        sa.Column(
            "authorization_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("capture_id", sa.Text(), nullable=True),
        sa.Column("payer_email", sa.Text(), nullable=True),
        created_at(),
        created_at("updated_at"),
        sa.ForeignKeyConstraint(
            ["cart_id"], ["carts.id"], name=op.f("fk_orders_cart_id_carts")
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["wallets.id"], name=op.f("fk_orders_wallet_id_wallets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("number", name=op.f("uq_orders_number")),
    )
    op.create_index(op.f("ix_orders_cart_id"), "orders", ["cart_id"])
    op.create_table(
        "order_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("size", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_lines_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_order_lines_product_id_products"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_lines")),
    )
    op.create_table(
        "paypal_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("request_body", JSONB(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("debug_id", sa.Text(), nullable=True),
        sa.Column("response_body", JSONB(), nullable=True),
        created_at("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_paypal_calls_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paypal_calls")),
        sa.UniqueConstraint("request_id", name=op.f("uq_paypal_calls_request_id")),
    )
    op.create_table(
        "order_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("webhook_event_id", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        created_at("at"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_events_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["webhook_event_id"],
            ["webhook_events.id"],
            name=op.f("fk_order_events_webhook_event_id_webhook_events"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
    )


def downgrade() -> None:
    op.drop_table("order_events")
    op.drop_table("paypal_calls")
    op.drop_table("order_lines")
    op.drop_table("orders")
    op.drop_table("cart_lines")
    op.drop_table("carts")
    op.drop_table("wallets")
