"""The refund job waiting on a pre-chargeback alert, and when it runs.

Additive: both columns start null on every dispute.

Revision ID: 5c8e1d7a3b20
Revises: 7b3f2a9c1d4e
Create Date: 2026-11-04 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c8e1d7a3b20"
down_revision: str | Sequence[str] | None = "7b3f2a9c1d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "disputes", sa.Column("refund_job_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "disputes",
        sa.Column("refund_due_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("disputes", "refund_due_at")
    op.drop_column("disputes", "refund_job_id")
