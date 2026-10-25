"""A key per conversation, so the shared chat code alone opens nobody else's chat.

Conversations that already exist get a hash nobody holds the key to, which closes them.

Revision ID: c3d9e7a1b5f2
Revises: b7c1d4e2f9a3
Create Date: 2026-10-25 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d9e7a1b5f2"
down_revision: str | Sequence[str] | None = "b7c1d4e2f9a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("key_hash", sa.Text(), nullable=True))
    # The app role refuses an UPDATE without a WHERE, so the clause names every row.
    op.execute(
        "UPDATE conversations SET key_hash = md5(gen_random_uuid()::text) WHERE key_hash IS NULL"
    )
    op.alter_column("conversations", "key_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("conversations", "key_hash")
