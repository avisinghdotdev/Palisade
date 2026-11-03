"""The seller's evidence on a dispute, the dispute a PayPal call answers, and the filing mode.

All additive: the evidence table starts empty, the call column starts null, and every policy
row reads "review", the mode Palisade starts in.

Revision ID: 7b3f2a9c1d4e
Revises: e0daa5c56490
Create Date: 2026-11-04 01:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b3f2a9c1d4e"
down_revision: str | Sequence[str] | None = "e0daa5c56490"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("paypal_calls", sa.Column("dispute_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_paypal_calls_dispute_id_disputes"),
        "paypal_calls",
        "disputes",
        ["dispute_id"],
        ["id"],
    )
    op.create_index(op.f("ix_paypal_calls_dispute_id"), "paypal_calls", ["dispute_id"])
    op.add_column(
        "policies",
        sa.Column("dispute_filing", sa.Text(), server_default="review", nullable=False),
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dispute_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("evidence_type", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("document_name", sa.Text(), nullable=False),
        sa.Column("document", sa.LargeBinary(), nullable=False),
        sa.Column("document_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column(
            "prepared_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["paypal_calls.id"],
            name=op.f("fk_evidence_call_id_paypal_calls"),
        ),
        sa.ForeignKeyConstraint(
            ["dispute_id"],
            ["disputes.id"],
            name=op.f("fk_evidence_dispute_id_disputes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
    )
    op.create_index(op.f("ix_evidence_dispute_id"), "evidence", ["dispute_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_evidence_dispute_id"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_column("policies", "dispute_filing")
    op.drop_index(op.f("ix_paypal_calls_dispute_id"), table_name="paypal_calls")
    op.drop_constraint(
        op.f("fk_paypal_calls_dispute_id_disputes"), "paypal_calls", type_="foreignkey"
    )
    op.drop_column("paypal_calls", "dispute_id")
