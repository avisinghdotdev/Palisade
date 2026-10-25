"""Agents, their nonces, the merchant policy, and the agent and policy hash on an order.

Revision ID: 04481226fad4
Revises: 20d9338df765
Create Date: 2026-10-25 13:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "04481226fad4"
down_revision: str | Sequence[str] | None = "20d9338df765"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
    )
    op.create_table(
        "agent_nonces",
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_nonces_agent_id_agents")
        ),
        sa.PrimaryKeyConstraint("agent_id", "nonce", name=op.f("pk_agent_nonces")),
    )
    op.create_table(
        "policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("allowed_agents", ARRAY(sa.Text()), nullable=False),
        sa.Column("per_order_cap_cents", sa.Integer(), nullable=False),
        sa.Column("per_agent_daily_cap_cents", sa.Integer(), nullable=False),
        sa.Column("blocked_categories", ARRAY(sa.Text()), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policies")),
    )
    op.create_index(op.f("ix_policies_created_at"), "policies", ["created_at"])
    op.add_column("orders", sa.Column("agent_id", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("policy_hash", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_orders_agent_id_agents"), "orders", "agents", ["agent_id"], ["id"]
    )
    op.create_index(op.f("ix_orders_agent_id"), "orders", ["agent_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_agent_id"), table_name="orders")
    op.drop_constraint(op.f("fk_orders_agent_id_agents"), "orders", type_="foreignkey")
    op.drop_column("orders", "policy_hash")
    op.drop_column("orders", "agent_id")
    op.drop_index(op.f("ix_policies_created_at"), table_name="policies")
    op.drop_table("policies")
    op.drop_table("agent_nonces")
    op.drop_table("agents")
