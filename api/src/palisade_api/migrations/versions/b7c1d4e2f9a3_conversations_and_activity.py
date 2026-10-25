"""The in-page agent's conversations and turns, and the agent activity log.

Revision ID: b7c1d4e2f9a3
Revises: a05c2a2b25ae
Create Date: 2026-10-25 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b7c1d4e2f9a3"
down_revision: str | Sequence[str] | None = "a05c2a2b25ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=True),
        sa.Column("history", JSONB(), nullable=False),
        sa.Column("busy_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"],
            ["wallets.id"],
            name=op.f("fk_conversations_wallet_id_wallets"),
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"], ["carts.id"], name=op.f("fk_conversations_cart_id_carts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("events", JSONB(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_turns_conversation_id_conversations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_turns")),
        sa.UniqueConstraint(
            "conversation_id",
            "position",
            name=op.f("uq_conversation_turns_conversation_id"),
        ),
    )
    op.create_index(
        op.f("ix_conversation_turns_conversation_id"),
        "conversation_turns",
        ["conversation_id"],
    )
    op.create_table(
        "activity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("mandate_id", sa.Uuid(), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("details", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_activity_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_activity_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["mandate_id"],
            ["mandates.id"],
            name=op.f("fk_activity_mandate_id_mandates"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_activity_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity")),
    )
    for column in ("kind", "conversation_id", "mandate_id", "order_id", "created_at"):
        op.create_index(op.f(f"ix_activity_{column}"), "activity", [column])


def downgrade() -> None:
    for column in ("created_at", "order_id", "mandate_id", "conversation_id", "kind"):
        op.drop_index(op.f(f"ix_activity_{column}"), table_name="activity")
    op.drop_table("activity")
    op.drop_index(
        op.f("ix_conversation_turns_conversation_id"), table_name="conversation_turns"
    )
    op.drop_table("conversation_turns")
    op.drop_table("conversations")
