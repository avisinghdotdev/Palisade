"""Every stored dispute names one of the shop's orders and its capture.

Palisade keeps no copy of a dispute on a payment made outside the shop. No stored dispute lacks
either value, so the columns become required as they are.

Revision ID: e0daa5c56490
Revises: 424db02f8b79
Create Date: 2026-11-02 02:08:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e0daa5c56490"
down_revision: str | Sequence[str] | None = "424db02f8b79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("disputes", "order_id", nullable=False)
    op.alter_column("disputes", "capture_id", nullable=False)


def downgrade() -> None:
    op.alter_column("disputes", "capture_id", nullable=True)
    op.alter_column("disputes", "order_id", nullable=True)
