"""Requests to the shop's API as one of Palisade's own agents, signed the way the verifier checks them."""

import secrets
from datetime import UTC, datetime
from typing import final, override

import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from http_message_signatures import algorithms
from http_message_signatures.resolvers import HTTPSignatureKeyResolver
from http_message_signatures.signatures import HTTPMessageSigner
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from palisade_api.agents.keys import private_key
from palisade_api.agents.signatures import (
    BODY_COMPONENT,
    REQUIRED_COMPONENTS,
    content_digest,
)


@final
class AgentKey(HTTPSignatureKeyResolver):
    def __init__(self, seed: str) -> None:
        self._key = private_key(seed)

    @override
    def resolve_private_key(self, key_id: str) -> Ed25519PrivateKey:
        return self._key


@final
class AgentSigner:
    """Signs a request under the agent's id: the method, the target URI, and the body's digest."""

    def __init__(self, agent_id: str, seed: str) -> None:
        self.agent_id: str = agent_id
        # The library ships no export list, so its public names read as private to pyright.
        self._signer = HTTPMessageSigner(
            signature_algorithm=algorithms.ED25519,  # pyright: ignore[reportPrivateImportUsage]
            key_resolver=AgentKey(seed),
        )

    def sign(
        self,
        request: httpx2.Request,
        *,
        created: datetime | None = None,
        nonce: str | None = None,
    ) -> httpx2.Request:
        """Add the signature headers to the request; `created` and `nonce` default to fresh values."""
        components = sorted(REQUIRED_COMPONENTS)
        if request.content:
            request.headers["Content-Digest"] = content_digest(request.content)
            components.append(BODY_COMPONENT)
        self._signer.sign(  # pyright: ignore[reportUnknownMemberType]
            request,
            key_id=self.agent_id,
            covered_component_ids=tuple(components),
            created=created or datetime.now(UTC),
            nonce=nonce or secrets.token_urlsafe(16),
        )
        return request


class ShopError(Exception):
    """The shop's API refused the agent's request; `detail` is the message it gave."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"the shop answered {status_code}: {detail}")
        self.status_code: int = status_code
        self.detail: str = detail


JSON = TypeAdapter(JsonValue)


class ErrorBody(BaseModel):
    detail: str


def error_detail(response: httpx2.Response) -> str:
    """FastAPI's `detail` from an error body, or the raw body when it is not that shape."""
    try:
        return ErrorBody.model_validate_json(response.content).detail
    except ValidationError:
        return response.text


@final
class AgentHttp:
    """An HTTP client that signs every request as one agent and raises on any error answer."""

    def __init__(self, http: httpx2.AsyncClient, signer: AgentSigner) -> None:
        self._http = http
        self._signer = signer

    async def get(self, path: str) -> JsonValue:
        return await self.request("GET", path)

    async def post(self, path: str, body: JsonValue) -> JsonValue:
        return await self.request("POST", path, body)

    async def put(self, path: str, body: JsonValue) -> JsonValue:
        return await self.request("PUT", path, body)

    async def request(
        self, method: str, path: str, body: JsonValue | None = None
    ) -> JsonValue:
        """Send a signed request; the parsed JSON answer.

        Raises:
            ShopError: the answer was not a success.
        """
        request = self._http.build_request(method, path, json=body)
        response = await self._http.send(self._signer.sign(request))
        if response.is_error:
            raise ShopError(response.status_code, error_detail(response))
        return JSON.validate_json(response.content)
