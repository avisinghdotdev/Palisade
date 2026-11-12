"""The answer Claude picks for a dispute in its inquiry, and the simulator's buyer complaint.

Additive: the table starts empty, a dispute answered before it existed keeps its evidence, and
the complaint column starts null on every dispute.

Revision ID: 9e3b5d7f1a2c
Revises: 8d2e4f6a1c3b
Create Date: 2026-11-12 03:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "9e3b5d7f1a2c"
down_revision: str | Sequence[str] | None = "8d2e4f6a1c3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("disputes", sa.Column("sandbox_complaint", sa.Text(), nullable=True))
    op.create_table(
        "triage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dispute_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("complaint", sa.Text(), nullable=False),
        sa.Column("reasons", JSONB(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("offer_cents", sa.Integer(), nullable=True),
        sa.Column("options", JSONB(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["paypal_calls.id"],
            name=op.f("fk_triage_call_id_paypal_calls"),
        ),
        sa.ForeignKeyConstraint(
            ["dispute_id"],
            ["disputes.id"],
            name=op.f("fk_triage_dispute_id_disputes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triage")),
    )
    op.create_index(op.f("ix_triage_dispute_id"), "triage", ["dispute_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_triage_dispute_id"), table_name="triage")
    op.drop_table("triage")
    op.drop_column("disputes", "sandbox_complaint")
