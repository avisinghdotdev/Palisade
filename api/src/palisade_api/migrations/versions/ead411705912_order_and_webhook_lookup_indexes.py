"""Indexes for the order and webhook lookups, and one order event per webhook event.

Revision ID: ead411705912
Revises: f96d5b16984f
Create Date: 2026-10-24 09:07:51.521053
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ead411705912"
down_revision: str | Sequence[str] | None = "f96d5b16984f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) pairs the API filters, joins, or sorts by.
INDEXED = (
    ("orders", "authorization_id"),
    ("orders", "capture_id"),
    ("orders", "created_at"),
    ("order_lines", "order_id"),
    ("order_events", "order_id"),
    ("paypal_calls", "order_id"),
    ("webhook_events", "received_at"),
)


def upgrade() -> None:
    for table, column in INDEXED:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column])
    op.create_unique_constraint(
        op.f("uq_order_events_webhook_event_id"), "order_events", ["webhook_event_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_order_events_webhook_event_id"), "order_events", type_="unique"
    )
    for table, column in reversed(INDEXED):
        op.drop_index(op.f(f"ix_{table}_{column}"), table_name=table)
