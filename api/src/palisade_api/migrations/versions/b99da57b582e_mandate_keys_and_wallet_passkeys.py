"""Mandate keys and signed lines, passkeys bound to a wallet, and a quieter activity feed.

A mandate gets the hash of a key only its requesting agent holds; mandates that already exist
get a hash nobody holds the key to, so they can no longer be answered or spent. Each mandate
keeps the cart lines its token signs, read here from the token's disclosures.

A passkey now signs for one wallet: one that already signed a mandate keeps that mandate's
wallet, and any other is removed, with every open registration challenge.

Activity rows lose what the feed must not publish: cart ids, raw request paths, ids an agent
named that were not proven to be its mandate, and texts longer than the feed keeps.

Revision ID: b99da57b582e
Revises: c3d9e7a1b5f2
Create Date: 2026-10-28 16:11:50.768419
"""

import base64
import json
from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.dialects import postgresql

revision: str = "b99da57b582e"
down_revision: str | Sequence[str] | None = "c3d9e7a1b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DISCLOSURE = TypeAdapter(tuple[str, str, JsonValue])
MANDATE_ROW = TypeAdapter(tuple[UUID, str])


def signed_lines(token: str) -> str:
    """The `lines` disclosure of an SD-JWT, as JSON text; `[]` when the token discloses none."""
    for part in token.split("~")[1:]:
        if not part:
            continue
        # A disclosure is the base64url JSON array [salt, claim name, claim value].
        _salt, name, value = DISCLOSURE.validate_json(
            base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        )
        if name == "lines":
            return json.dumps(value)
    return "[]"


def upgrade() -> None:
    connection = op.get_bind()
    op.add_column("mandates", sa.Column("key_hash", sa.Text(), nullable=True))
    op.add_column(
        "mandates",
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # The app role refuses an UPDATE without a WHERE, so every clause names its rows.
    op.execute(
        """
        UPDATE mandates SET key_hash = encode(sha256(gen_random_uuid()::text::bytea), 'hex')
        WHERE key_hash IS NULL
        """
    )
    for row in connection.execute(sa.text("SELECT id, token FROM mandates")).all():
        mandate_id, token = MANDATE_ROW.validate_python(tuple(row))
        _ = connection.execute(
            sa.text("UPDATE mandates SET lines = CAST(:lines AS jsonb) WHERE id = :id"),
            {"lines": signed_lines(token), "id": mandate_id},
        )
    op.alter_column("mandates", "key_hash", nullable=False)
    op.alter_column("mandates", "lines", nullable=False)

    op.add_column("passkeys", sa.Column("wallet_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE passkeys SET wallet_id = signer.wallet_id
        FROM (
            SELECT DISTINCT ON (proof->>'passkey_id') proof->>'passkey_id' AS passkey_id, wallet_id
            FROM mandates
            WHERE signed_by = 'passkey'
            ORDER BY proof->>'passkey_id', signed_at DESC
        ) AS signer
        WHERE passkeys.id::text = signer.passkey_id
        """
    )
    op.execute("DELETE FROM passkeys WHERE wallet_id IS NULL")
    op.alter_column("passkeys", "wallet_id", nullable=False)
    op.create_foreign_key(
        "fk_passkeys_wallet_id_wallets", "passkeys", "wallets", ["wallet_id"], ["id"]
    )
    op.create_index("ix_passkeys_wallet_id", "passkeys", ["wallet_id"])

    op.execute("DELETE FROM passkey_challenges WHERE true")
    op.add_column(
        "passkey_challenges", sa.Column("wallet_id", sa.Uuid(), nullable=False)
    )
    op.create_foreign_key(
        "fk_passkey_challenges_wallet_id_wallets",
        "passkey_challenges",
        "wallets",
        ["wallet_id"],
        ["id"],
    )

    op.execute(
        """
        UPDATE activity SET details = details - 'cart_id' - 'path' - 'named_mandate'
        WHERE details ?| array['cart_id', 'path', 'named_mandate']
        """
    )
    op.execute(
        """
        UPDATE activity
        SET details = jsonb_set(details, '{input}', (details->'input') - 'mandate_id' - 'mandate_key')
        WHERE details->'input' ?| array['mandate_id', 'mandate_key']
        """
    )
    # 300 characters: what the activity feed keeps of any text from now on.
    op.execute(
        """
        UPDATE activity
        SET details = jsonb_set(
            details, '{input,query}', to_jsonb(left(details->'input'->>'query', 300) || '…')
        )
        WHERE length(details->'input'->>'query') > 300
        """
    )
    op.execute(
        """
        UPDATE activity SET details = jsonb_set(details, '{reason}', '"no agent has that key id"')
        WHERE kind = 'signature_refused' AND details->>'reason' LIKE 'no agent has the key id %'
        """
    )


def downgrade() -> None:
    # The removed passkeys, challenges, and activity details cannot be brought back.
    op.drop_constraint(
        "fk_passkey_challenges_wallet_id_wallets",
        "passkey_challenges",
        type_="foreignkey",
    )
    op.drop_column("passkey_challenges", "wallet_id")
    op.drop_index("ix_passkeys_wallet_id", table_name="passkeys")
    op.drop_constraint("fk_passkeys_wallet_id_wallets", "passkeys", type_="foreignkey")
    op.drop_column("passkeys", "wallet_id")
    op.drop_column("mandates", "lines")
    op.drop_column("mandates", "key_hash")
