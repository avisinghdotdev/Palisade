"""RFC 9421 signatures on agent requests: who signed, checked against the registry.

An agent signs the method, the public target URI, and the body's digest with its Ed25519 key,
under its agent id as `keyid`, a `created` time, and a nonce. The verifier rebuilds the target
URI from the site's public origin, because the API sits behind a proxy that rewrites the host.
"""

import base64
import hashlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, final, override

import structlog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, HTTPException, Request, status
from http_message_signatures import algorithms
from http_message_signatures.exceptions import InvalidSignature
from http_message_signatures.resolvers import HTTPSignatureKeyResolver
from http_message_signatures.signatures import HTTPMessageVerifier
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.keys import public_key
from palisade_api.agents.models import AgentNonceRecord
from palisade_api.agents.registry import Agent, Agents

# A signature older than this is refused, so a captured request cannot be replayed later.
MAX_AGE = timedelta(minutes=5)
REQUIRED_COMPONENTS = frozenset({"@method", "@target-uri"})
BODY_COMPONENT = "content-digest"
# Web Bot Auth's header naming the agent; present without a signature, the request is refused.
AGENT_HEADER = "signature-agent"

logger = structlog.stdlib.get_logger(__name__)


class UnsignedAgentRequest(Exception):
    def __init__(self) -> None:
        super().__init__("an agent request must carry an RFC 9421 signature")


class InvalidAgentSignature(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(f"the agent signature was refused: {reason}")
        self.reason: str = reason


@dataclass(frozen=True)
class SignedMessage:
    """The request as the agent saw it, in the shape the signature library reads."""

    method: str
    url: str
    headers: dict[str, str]


@final
class RegistryKeys(HTTPSignatureKeyResolver):
    def __init__(self, keys: dict[str, str]) -> None:
        self._keys = keys

    @override
    def resolve_public_key(self, key_id: str) -> Ed25519PublicKey:
        encoded = self._keys.get(key_id)
        if encoded is None:
            raise InvalidSignature(f"no agent has the key id {key_id}")
        return public_key(encoded)


def content_digest(body: bytes) -> str:
    """The `Content-Digest` value for a body, as RFC 9530 spells it."""
    return f"sha-256=:{base64.b64encode(hashlib.sha256(body).digest()).decode()}:"


@final
class AgentSignatures:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: Agents,
        public_origin: str,
    ) -> None:
        self._sessions = sessions
        self._agents = agents
        self._public_origin = public_origin

    async def verify(self, request: Request, body: bytes) -> Agent | None:
        """The agent that signed the request; None for an unsigned, human request.

        Raises:
            UnsignedAgentRequest: the request names an agent but carries no signature.
            InvalidAgentSignature: the signature, its age, its nonce, or the body digest failed.
        """
        if "signature-input" not in request.headers:
            if AGENT_HEADER in request.headers:
                raise UnsignedAgentRequest
            return None
        message = SignedMessage(
            method=request.method,
            url=self._public_origin
            + request.url.path
            + (f"?{request.url.query}" if request.url.query else ""),
            headers=dict(request.headers),
        )
        # The library ships no export list, so its public names read as private to pyright.
        verifier = HTTPMessageVerifier(
            signature_algorithm=algorithms.ED25519,  # pyright: ignore[reportPrivateImportUsage]
            key_resolver=RegistryKeys(await self._agents.public_keys()),
        )
        try:
            [result] = verifier.verify(message, max_age=MAX_AGE)  # pyright: ignore[reportUnknownMemberType]
        except InvalidSignature as error:
            raise InvalidAgentSignature(str(error)) from error
        # The library serializes the covered identifiers with their quotes, as the base does.
        covered = {identifier.strip('"') for identifier in result.covered_components}
        if not covered >= REQUIRED_COMPONENTS:
            raise InvalidAgentSignature("the method and target URI must be signed")
        if body and (
            BODY_COMPONENT not in covered
            or request.headers.get(BODY_COMPONENT) != content_digest(body)
        ):
            raise InvalidAgentSignature("the body digest is missing or does not match")
        if result.nonce is None:
            raise InvalidAgentSignature("a nonce is required")
        agent_id = str(result.parameters["keyid"])
        await self._record_nonce(agent_id, result.nonce)
        return await self._agents.get(agent_id)

    async def _record_nonce(self, agent_id: str, nonce: str) -> None:
        statement = (
            insert(AgentNonceRecord)
            .values(agent_id=agent_id, nonce=nonce)
            .on_conflict_do_nothing()
            .returning(AgentNonceRecord.nonce)
        )
        async with self._sessions.begin() as session:
            if (await session.execute(statement)).scalar_one_or_none() is None:
                raise InvalidAgentSignature("the nonce was already used")


def agent_signatures(request: Request) -> AgentSignatures:
    # Starlette types lifespan state as Any; the lifespan in main.py puts an AgentSignatures here.
    service: AgentSignatures = request.state.agent_signatures  # pyright: ignore[reportAny]
    return service


async def caller(
    request: Request,
    signatures: Annotated[AgentSignatures, Depends(agent_signatures)],
) -> Agent | None:
    """The signed agent behind the request, or None when a person's browser sent it.

    A request that claims to be an agent but fails the check is a 401 with the reason.
    """
    try:
        return await signatures.verify(request, await request.body())
    except (UnsignedAgentRequest, InvalidAgentSignature) as error:
        logger.info("agent_request_refused", reason=str(error))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
