from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from palisade_api.activity.store import Activities, Activity, ActivityKind

router = APIRouter(tags=["activity"])


def activities(request: Request) -> Activities:
    # Starlette types lifespan state as Any; the lifespan in main.py puts an Activities here.
    service: Activities = request.state.activities  # pyright: ignore[reportAny]
    return service


@router.get("/api/activity", operation_id="activity")
async def list_activity(
    service: Annotated[Activities, Depends(activities)],
    kind: ActivityKind | None = None,
) -> list[Activity]:
    """The agent-side events the store recorded, newest first; `kind` narrows them to one kind."""
    return await service.list(kind)


@router.get("/api/orders/{number}/activity", operation_id="order_activity")
async def order_activity(
    number: str, service: Annotated[Activities, Depends(activities)]
) -> list[Activity]:
    """The agent-side events behind one order, oldest first, from the shopper's request on."""
    try:
        return await service.for_order(number)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
