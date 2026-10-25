"""Mandates, the passkeys that sign them, and the mandate on an order.

Revision ID: a05c2a2b25ae
Revises: 04481226fad4
Create Date: 2026-10-25 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "a05c2a2b25ae"
down_revision: str | Sequence[str] | None = "04481226fad4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mandates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=True),
        sa.Column("cap_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("categories", ARRAY(sa.Text()), nullable=False),
        sa.Column("policy_hash", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("signed_by", sa.Text(), nullable=True),
        sa.Column("proof", JSONB(), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_mandates_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["wallets.id"], name=op.f("fk_mandates_wallet_id_wallets")
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"], ["carts.id"], name=op.f("fk_mandates_cart_id_carts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mandates")),
    )
    op.create_table(
        "passkeys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Text(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_passkeys")),
        sa.UniqueConstraint("credential_id", name=op.f("uq_passkeys_credential_id")),
    )
    op.create_table(
        "passkey_challenges",
        sa.Column("challenge", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("challenge", name=op.f("pk_passkey_challenges")),
    )
    op.add_column("orders", sa.Column("mandate_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_orders_mandate_id_mandates"),
        "orders",
        "mandates",
        ["mandate_id"],
        ["id"],
    )
    op.create_index(op.f("ix_orders_mandate_id"), "orders", ["mandate_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_mandate_id"), table_name="orders")
    op.drop_constraint(
        op.f("fk_orders_mandate_id_mandates"), "orders", type_="foreignkey"
    )
    op.drop_column("orders", "mandate_id")
    op.drop_table("passkey_challenges")
    op.drop_table("passkeys")
    op.drop_table("mandates")
