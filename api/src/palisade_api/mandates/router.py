from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
    Mandate,
    MandateNotPending,
    MandateRequest,
    Mandates,
    UnknownMandate,
)

router = APIRouter(tags=["mandates"])


def mandates(request: Request) -> Mandates:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Mandates here.
    service: Mandates = request.state.mandates  # pyright: ignore[reportAny]
    return service


def passkeys(request: Request) -> Passkeys:
    service: Passkeys = request.state.passkeys  # pyright: ignore[reportAny]
    return service


@router.post(
    "/api/mandates", status_code=status.HTTP_201_CREATED, operation_id="create_mandate"
)
async def create_mandate(
    body: MandateRequest,
    service: Annotated[Mandates, Depends(mandates)],
    agent: Annotated[Agent | None, Depends(caller)],
) -> Mandate:
    """An agent asks the buyer for a mandate; the answer holds the consent page's path.

    Only a signed agent request may ask; a 404 names a wallet or cart that cannot be used.
    """
    if agent is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "only a signed agent can ask for a mandate"
        )
    try:
        return await service.create(agent, body)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.get("/api/mandates/{mandate_id}", operation_id="mandate")
async def get_mandate(
    mandate_id: UUID, service: Annotated[Mandates, Depends(mandates)]
) -> Mandate:
    try:
        return await service.get(mandate_id)
    except UnknownMandate as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.get(
    "/api/mandates/{mandate_id}/passkey-options", operation_id="mandate_passkey_options"
)
async def mandate_passkey_options(
    mandate_id: UUID, passkey_id: UUID, service: Annotated[Mandates, Depends(mandates)]
) -> dict[str, JsonValue]:
    """The browser options that make the given passkey sign this mandate."""
    try:
        return await service.passkey_options(mandate_id, passkey_id)
    except (UnknownMandate, UnknownPasskey) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/api/mandates/{mandate_id}/approval", operation_id="approve_mandate")
async def approve_mandate(
    mandate_id: UUID,
    approval: Approval,
    service: Annotated[Mandates, Depends(mandates)],
) -> Mandate:
    """The buyer signs the mandate with a passkey or the demo signer (simulated)."""
    try:
        return await service.approve(mandate_id, approval)
    except (UnknownMandate, UnknownPasskey) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except MandateNotPending as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except (PasskeyRefused, ValueError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error


@router.post("/api/mandates/{mandate_id}/decline", operation_id="decline_mandate")
async def decline_mandate(
    mandate_id: UUID, service: Annotated[Mandates, Depends(mandates)]
) -> Mandate:
    try:
        return await service.decline(mandate_id)
    except UnknownMandate as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except MandateNotPending as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.post(
    "/api/passkeys/registration",
    status_code=status.HTTP_201_CREATED,
    operation_id="start_passkey_registration",
)
async def start_passkey_registration(
    service: Annotated[Passkeys, Depends(passkeys)],
) -> RegistrationStart:
    """A challenge and the options for `navigator.credentials.create()`."""
    return await service.start_registration()


@router.post(
    "/api/passkeys",
    status_code=status.HTTP_201_CREATED,
    operation_id="register_passkey",
)
async def register_passkey(
    registration: Registration, service: Annotated[Passkeys, Depends(passkeys)]
) -> Passkey:
    """Keep the passkey the browser created; its id signs mandates from now on."""
    try:
        return await service.register(registration)
    except PasskeyRefused as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
