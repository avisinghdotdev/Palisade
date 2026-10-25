"""Signed agent requests and policies the agent and mandate tests share."""

import base64
import secrets
from datetime import UTC, datetime
from typing import final, override
from uuid import uuid7

import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from http_message_signatures import algorithms
from http_message_signatures.resolvers import HTTPSignatureKeyResolver
from http_message_signatures.signatures import HTTPMessageSigner
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.keys import private_key
from palisade_api.agents.signatures import content_digest
from palisade_api.mandates.passkeys import Passkeys
from palisade_api.mandates.service import Mandates
from palisade_api.mandates.tokens import MandateTokens
from palisade_api.policy.engine import Policies, Rules
from palisade_api.policy.models import PolicyRecord

ORIGIN = "http://palisade.test"
AGENT = "test-agent"


def seed() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def mandates_for(sessions: async_sessionmaker[AsyncSession]) -> Mandates:
    """A mandate service with fresh issuer and demo signer keys, for checkouts under test."""
    return Mandates(
        sessions,
        MandateTokens(seed()),
        Passkeys(sessions, ORIGIN),
        Policies(sessions),
        seed(),
        ORIGIN,
    )


@final
class AgentKey(HTTPSignatureKeyResolver):
    def __init__(self, agent_seed: str) -> None:
        self._key = private_key(agent_seed)

    @override
    def resolve_private_key(self, key_id: str) -> Ed25519PrivateKey:
        return self._key


def signed(
    client: httpx2.AsyncClient,
    path: str,
    agent_seed: str,
    *,
    json: dict[str, JsonValue] | None = None,
    agent_id: str = AGENT,
    created: datetime | None = None,
    nonce: str | None = None,
) -> httpx2.Request:
    """A POST signed the way an agent signs it; a body is covered through its digest."""
    request = client.build_request("POST", path, json=json)
    components = ["@method", "@target-uri"]
    if json is not None:
        request.headers["Content-Digest"] = content_digest(request.content)
        components.append("content-digest")
    signer = HTTPMessageSigner(
        signature_algorithm=algorithms.ED25519,  # pyright: ignore[reportPrivateImportUsage]
        key_resolver=AgentKey(agent_seed),
    )
    signer.sign(  # pyright: ignore[reportUnknownMemberType]
        request,
        key_id=agent_id,
        covered_component_ids=tuple(components),
        created=created or datetime.now(UTC),
        nonce=nonce or secrets.token_urlsafe(16),
    )
    return request


async def allow(sessions: async_sessionmaker[AsyncSession], **changes: object) -> str:
    """A policy allowing the test agent, with any rule overridden; its hash."""
    rules = Rules(
        allowed_agents=[AGENT],
        per_order_cap_cents=100_000,
        per_agent_daily_cap_cents=1_000_000,
        blocked_categories=[],
    ).model_copy(update=changes)
    async with sessions.begin() as session:
        session.add(PolicyRecord(id=uuid7(), hash=rules.hash(), **rules.model_dump()))
    return rules.hash()
