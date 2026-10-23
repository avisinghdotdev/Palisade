from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from palisade_api.orders.store import Order, Orders, UnknownOrder

router = APIRouter(prefix="/api/orders", tags=["orders"])


def orders(request: Request) -> Orders:
    # Starlette types lifespan state as Any; the lifespan in main.py puts an Orders here.
    service: Orders = request.state.orders  # pyright: ignore[reportAny]
    return service


@router.get("", operation_id="orders")
async def list_orders(service: Annotated[Orders, Depends(orders)]) -> list[Order]:
    return await service.list()


@router.get("/{number}", operation_id="order")
async def get_order(number: str, service: Annotated[Orders, Depends(orders)]) -> Order:
    try:
        return await service.get(number)
    except UnknownOrder as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
