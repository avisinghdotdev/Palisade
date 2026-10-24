from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from palisade_api.paypal.client import PayPalError
from palisade_api.wallets.service import (
    LinkStart,
    NoDemoWallet,
    UnknownWallet,
    Wallet,
    Wallets,
)

router = APIRouter(prefix="/api/shop/wallets", tags=["wallets"])


def wallets(request: Request) -> Wallets:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Wallets here.
    service: Wallets = request.state.wallets  # pyright: ignore[reportAny]
    return service


class LinkConfirmation(BaseModel):
    setup_token_id: str


@router.post("/link", operation_id="start_wallet_link")
async def start_wallet_link(
    service: Annotated[Wallets, Depends(wallets)],
) -> LinkStart:
    """Begin saving the buyer's PayPal wallet; send the buyer to the approve URL."""
    try:
        return await service.start_link()
    except PayPalError as error:
        # The setup token carries only the shop's own settings, so any refusal is PayPal's side.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"PayPal could not start the wallet link: {', '.join(error.issues)}",
        ) from error


@router.post("/confirm", operation_id="confirm_wallet_link")
async def confirm_wallet_link(
    confirmation: LinkConfirmation, service: Annotated[Wallets, Depends(wallets)]
) -> Wallet:
    """Finish saving the wallet once the buyer has returned from PayPal.

    A 422 means PayPal refused the setup token (unknown, unapproved, or already used); a 502
    means PayPal failed to answer.
    """
    try:
        return await service.confirm_link(confirmation.setup_token_id)
    except PayPalError as error:
        code = (
            status.HTTP_502_BAD_GATEWAY
            if error.status_code >= 500
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            code, f"PayPal could not save the wallet: {', '.join(error.issues)}"
        ) from error


@router.get("/demo", operation_id="demo_wallet")
async def demo_wallet(service: Annotated[Wallets, Depends(wallets)]) -> Wallet:
    try:
        return await service.demo()
    except NoDemoWallet as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.get("/{wallet_id}", operation_id="wallet")
async def get_wallet(
    wallet_id: UUID, service: Annotated[Wallets, Depends(wallets)]
) -> Wallet:
    try:
        return await service.get(wallet_id)
    except UnknownWallet as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
