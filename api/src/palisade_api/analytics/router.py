from typing import Annotated

from fastapi import APIRouter, Depends, Request

from palisade_api.analytics.service import Analytics, AnalyticsRows

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def analytics(request: Request) -> Analytics:
    # Starlette types lifespan state as Any; the lifespan in main.py puts an Analytics here.
    service: Analytics = request.state.analytics  # pyright: ignore[reportAny]
    return service


@router.get("", operation_id="analytics")
async def get_analytics(
    service: Annotated[Analytics, Depends(analytics)],
) -> AnalyticsRows:
    """Every paid order and every dispute as rows, with the licence key of the dashboard that charts them."""
    return await service.data()
