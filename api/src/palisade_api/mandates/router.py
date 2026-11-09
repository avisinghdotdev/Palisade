from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from pydantic import JsonValue

from palisade_api.agents.registry import Agent
from palisade_api.agents.signatures import caller
from palisade_api.mandates.passkeys import (
    Passkey,
    PasskeyRefused,
    Passkeys,
    Registration,
    RegistrationStart,
    UnknownPasskey,
)
from palisade_api.mandates.service import (
    Approval,
    DemoSignerRefused,
    IssuedMandate,
    Mandate,
    MandateCartChanged,
    MandateKeyRefused,
    MandateNotPending,
    MandateRequest,
    Mandates,
    UnknownMandate,
)
from palisade_api.mandates.tokens import JWKS_PATH, MandateTokens
from palisade_api.mandates.verification import MandateVerification, Verifier
from palisade_api.paypal.client import PayPalError

router = APIRouter(tags=["mandates"])

mandate_key = APIKeyHeader(
    name="X-Mandate-Key",
    scheme_name="MandateKey",
    description="The key the requesting agent got with the mandate, in its consent link after `#key=`.",
    auto_error=False,
)


def mandates(request: Request) -> Mandates:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Mandates here.
    service: Mandates = request.state.mandates  # pyright: ignore[reportAny]
    return service


def passkeys(request: Request) -> Passkeys:
    service: Passkeys = request.state.passkeys  # pyright: ignore[reportAny]
    return service


def verifier(request: Request) -> Verifier:
    service: Verifier = request.state.verifier  # pyright: ignore[reportAny]
    return service


def tokens(request: Request) -> MandateTokens:
    service: MandateTokens = request.state.mandate_tokens  # pyright: ignore[reportAny]
    return service


async def bound(mandate_id: UUID) -> UUID:
    """The path's mandate id, also bound to the request's log event.

    Async, so it runs in the request's own context: FastAPI runs a plain function in a thread
    with a copy of it, and the binding would not reach the event.
    """
    structlog.contextvars.bind_contextvars(mandate_id=str(mandate_id))
    return mandate_id


MandateId = Annotated[UUID, Depends(bound)]
MandateKey = Annotated[str | None, Depends(mandate_key)]


@router.post(
    "/api/mandates", status_code=status.HTTP_201_CREATED, operation_id="create_mandate"
)
async def create_mandate(
    body: MandateRequest,
    service: Annotated[Mandates, Depends(mandates)],
    agent: Annotated[Agent | None, Depends(caller)],
) -> IssuedMandate:
    """An agent asks the buyer for a mandate; the answer holds the key and the consent link.

    Only a signed agent request may ask. A 404 names a wallet or cart that cannot be used; a 422
    a request that does not fit its kind, such as a cart mandate whose cap is not the cart total.
    """
    if agent is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "only a signed agent can ask for a mandate"
        )
    try:
        issued = await service.create(agent, body)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
    structlog.contextvars.bind_contextvars(mandate_id=str(issued.id))
    return issued


@router.get("/api/mandates/{mandate_id}", operation_id="mandate")
async def get_mandate(
    mandate_id: MandateId, service: Annotated[Mandates, Depends(mandates)]
) -> Mandate:
    try:
        return await service.get(mandate_id)
    except UnknownMandate as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.get("/api/mandates/{mandate_id}/verify", operation_id="verify_mandate")
async def verify_mandate(
    mandate_id: MandateId, service: Annotated[Verifier, Depends(verifier)]
) -> MandateVerification:
    """Check the mandate with public information alone.

    The checks: the issuer's signature on the token, the claims against the record, the hash
    the buyer signed, the buyer's proof over it, the policy it was signed under, and the custom
    id PayPal holds on every order it paid for. The issuer key is at `/.well-known/jwks.json`.
    A 502 means PayPal would not show an order.
    """
    try:
        return await service.verify(mandate_id)
    except UnknownMandate as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except PayPalError as error:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"PayPal could not show an order under this mandate: {', '.join(error.issues)}",
        ) from error


@router.get(JWKS_PATH, operation_id="jwks")
async def jwks(
    service: Annotated[MandateTokens, Depends(tokens)],
) -> dict[str, JsonValue]:
    """The issuer's public key as a JWK Set; a mandate token's `kid` names the key in it."""
    return service.jwks()


@router.post(
    "/api/mandates/{mandate_id}/passkey-registration",
    status_code=status.HTTP_201_CREATED,
    operation_id="start_passkey_registration",
)
async def start_passkey_registration(
    mandate_id: MandateId,
    key: MandateKey,
    service: Annotated[Mandates, Depends(mandates)],
) -> RegistrationStart:
    """A challenge and the options for a new passkey that signs for this mandate's wallet.

    The options are for `navigator.credentials.create()`. A 403 means the mandate key is
    missing or wrong.
    """
    try:
        return await service.passkey_registration(mandate_id, key)
    except UnknownMandate as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except MandateKeyRefused as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error


@router.get(
    "/api/mandates/{mandate_id}/passkey-options", operation_id="mandate_passkey_options"
)
async def mandate_passkey_options(
    mandate_id: MandateId,
    passkey_id: UUID,
    key: MandateKey,
    service: Annotated[Mandates, Depends(mandates)],
) -> dict[str, JsonValue]:
    """The browser options that make the given passkey sign this mandate.

    A 404 names a passkey that does not sign for this mandate's wallet; register a new one.
    """
    try:
        return await service.passkey_options(mandate_id, key, passkey_id)
    except (UnknownMandate, UnknownPasskey) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except MandateKeyRefused as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error


@router.post("/api/mandates/{mandate_id}/approval", operation_id="approve_mandate")
async def approve_mandate(
    mandate_id: MandateId,
    key: MandateKey,
    approval: Approval,
    service: Annotated[Mandates, Depends(mandates)],
) -> Mandate:
    """The buyer signs the mandate with a passkey, or the demo signer (simulated) for the demo buyer.

    A 409 means it is answered already or its cart changed since the request.
    """
    try:
        return await service.approve(mandate_id, key, approval)
    except (UnknownMandate, UnknownPasskey) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except MandateKeyRefused as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    except (MandateNotPending, MandateCartChanged) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except (PasskeyRefused, DemoSignerRefused, ValueError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error


@router.post("/api/mandates/{mandate_id}/decline", operation_id="decline_mandate")
async def decline_mandate(
    mandate_id: MandateId,
    key: MandateKey,
    service: Annotated[Mandates, Depends(mandates)],
) -> Mandate:
    try:
        return await service.decline(mandate_id, key)
    except UnknownMandate as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except MandateKeyRefused as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    except MandateNotPending as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.post(
    "/api/passkeys",
    status_code=status.HTTP_201_CREATED,
    operation_id="register_passkey",
)
async def register_passkey(
    registration: Registration, service: Annotated[Passkeys, Depends(passkeys)]
) -> Passkey:
    """Keep the passkey the browser created; its id signs that wallet's mandates from now on."""
    try:
        return await service.register(registration)
    except PasskeyRefused as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
