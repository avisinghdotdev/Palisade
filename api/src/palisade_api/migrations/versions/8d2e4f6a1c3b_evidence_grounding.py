"""The grounding check on Claude's notes: each sentence, its cited record lines, kept or dropped.

Additive: the column starts null on every answer prepared before it existed.

Revision ID: 8d2e4f6a1c3b
Revises: 5c8e1d7a3b20
Create Date: 2026-11-09 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "8d2e4f6a1c3b"
down_revision: str | Sequence[str] | None = "5c8e1d7a3b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence", sa.Column("grounding", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("evidence", "grounding")
