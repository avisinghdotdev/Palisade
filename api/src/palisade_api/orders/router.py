from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from palisade_api.orders.shipping import (
    AlreadyShipped,
    NotShippable,
    ShipmentRequest,
    Shipments,
)
from palisade_api.orders.store import Order, Orders, OrderSummary, UnknownOrder
from palisade_api.paypal.client import PayPalError

router = APIRouter(prefix="/api/orders", tags=["orders"])


def orders(request: Request) -> Orders:
    # Starlette types lifespan state as Any; the lifespan in main.py puts an Orders here.
    service: Orders = request.state.orders  # pyright: ignore[reportAny]
    return service


def shipments(request: Request) -> Shipments:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Shipments here.
    service: Shipments = request.state.shipments  # pyright: ignore[reportAny]
    return service


@router.get("", operation_id="orders")
async def list_orders(
    service: Annotated[Orders, Depends(orders)],
) -> list[OrderSummary]:
    return await service.list()


@router.get("/{number}", operation_id="order")
async def get_order(number: str, service: Annotated[Orders, Depends(orders)]) -> Order:
    try:
        return await service.get(number)
    except UnknownOrder as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/{number}/shipment", operation_id="ship_order")
async def ship_order(
    number: str,
    shipment: ShipmentRequest,
    service: Annotated[Shipments, Depends(shipments)],
) -> Order:
    """Mark a paid order shipped: its tracking goes onto the PayPal capture.

    A 409 means the order is not captured or already shipped; a 422 means PayPal refused the
    tracking; a 502 means PayPal failed to answer.
    """
    try:
        return await service.ship(number, shipment)
    except UnknownOrder as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (NotShippable, AlreadyShipped) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except PayPalError as error:
        code = (
            status.HTTP_502_BAD_GATEWAY
            if error.status_code >= 500
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            code, f"PayPal could not add the tracking: {', '.join(error.issues)}"
        ) from error
