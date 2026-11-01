from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from palisade_api.disputes.service import (
    Dispute,
    Disputes,
    DisputeSummary,
    NotTheShops,
    UnknownDispute,
)
from palisade_api.paypal.client import PayPalError

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


def disputes(request: Request) -> Disputes:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Disputes here.
    service: Disputes = request.state.disputes  # pyright: ignore[reportAny]
    return service


async def bound(
    dispute_id: Annotated[str, Path(pattern=r"^PP-[A-Z0-9-]{4,40}$")],
) -> str:
    """The path's dispute id, checked to be PayPal's shape, since it goes into PayPal's paths.

    Async, so it runs in the request's own context and the binding reaches the log event.
    """
    structlog.contextvars.bind_contextvars(dispute_id=dispute_id)
    return dispute_id


DisputeId = Annotated[str, Depends(bound)]


def paypal_refused(error: PayPalError, what: str) -> HTTPException:
    """A refusal by PayPal as the console reads it: 502 when PayPal failed, 422 when it said no."""
    code = (
        status.HTTP_502_BAD_GATEWAY
        if error.status_code >= 500
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    return HTTPException(code, f"PayPal {what}: {', '.join(error.issues)}")


@router.get("", operation_id="disputes")
async def list_disputes(
    service: Annotated[Disputes, Depends(disputes)], order_number: str | None = None
) -> list[DisputeSummary]:
    """The disputes PayPal opened on the store's payments, newest first."""
    return await service.list(order_number)


@router.get("/{dispute_id}", operation_id="dispute")
async def get_dispute(
    dispute_id: DisputeId, service: Annotated[Disputes, Depends(disputes)]
) -> Dispute:
    try:
        return await service.get(dispute_id)
    except UnknownDispute as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/{dispute_id}/sync", operation_id="sync_dispute")
async def sync_dispute(
    dispute_id: DisputeId, service: Annotated[Disputes, Depends(disputes)]
) -> Dispute:
    """Read the dispute from PayPal now, as a webhook would.

    A 404 means the dispute names a payment that is none of the shop's orders; a 422 names
    what PayPal refused.
    """
    try:
        return await service.sync(dispute_id, "console")
    except NotTheShops as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except PayPalError as error:
        raise paypal_refused(error, "did not return the dispute") from error
