from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from palisade_api.paypal.auth import PayPalAuth

router = APIRouter(prefix="/api/paypal", tags=["paypal"])


def paypal_auth(request: Request) -> PayPalAuth:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a PayPalAuth here.
    auth: PayPalAuth = request.state.paypal_auth  # pyright: ignore[reportAny]
    return auth


class PayPalHealth(BaseModel):
    status: Literal["ok"]
    app_id: str
    scopes: list[str]
    token_expires_at: datetime


@router.get("/health", operation_id="paypal_health")
async def paypal_health(
    auth: Annotated[PayPalAuth, Depends(paypal_auth)],
) -> PayPalHealth:
    token = await auth.token()
    return PayPalHealth(
        status="ok",
        app_id=token.app_id,
        scopes=list(token.scopes),
        token_expires_at=token.expires_at,
    )
