"""A conversation keeps its running turn and the mandate it waits on.

The running turn is saved as it goes, so a browser that is not streaming it can follow it, and
the mandate the agent asked for lets the API carry the conversation on once the consent page
answers it. Both start empty.

Revision ID: ce8dd440c797
Revises: b99da57b582e
Create Date: 2026-10-30 23:38:42.882772
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ce8dd440c797"
down_revision: str | Sequence[str] | None = "b99da57b582e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("live", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "conversations", sa.Column("awaiting_mandate_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_conversations_awaiting_mandate_id_mandates"),
        "conversations",
        "mandates",
        ["awaiting_mandate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_conversations_awaiting_mandate_id"),
        "conversations",
        ["awaiting_mandate_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_conversations_awaiting_mandate_id"), table_name="conversations"
    )
    op.drop_constraint(
        op.f("fk_conversations_awaiting_mandate_id_mandates"),
        "conversations",
        type_="foreignkey",
    )
    op.drop_column("conversations", "awaiting_mandate_id")
    op.drop_column("conversations", "live")
