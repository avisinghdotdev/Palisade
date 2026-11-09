from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from palisade_api.analytics.agent import (
    AgentAnswer,
    AgentTurn,
    AgentUnavailable,
    StudioAgent,
)
from palisade_api.analytics.service import Analytics, AnalyticsRows
from palisade_api.assistant.access import require_chat_key

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def analytics(request: Request) -> Analytics:
    # Starlette types lifespan state as Any; the lifespan in main.py puts an Analytics here.
    service: Analytics = request.state.analytics  # pyright: ignore[reportAny]
    return service


def studio_agent(request: Request) -> StudioAgent:
    service: StudioAgent = request.state.studio_agent  # pyright: ignore[reportAny]
    return service


@router.get("", operation_id="analytics")
async def get_analytics(
    service: Annotated[Analytics, Depends(analytics)],
) -> AnalyticsRows:
    """Every paid order and every dispute as rows, with the licence key of the dashboard that charts them."""
    return await service.data()


@router.post(
    "/agent/turn",
    operation_id="analytics_agent_turn",
    dependencies=[Depends(require_chat_key)],
)
async def agent_turn(
    body: AgentTurn, service: Annotated[StudioAgent, Depends(studio_agent)]
) -> AgentAnswer:
    """One turn of a dashboard agent on Claude, for AG Studio's agent loop in the browser.

    The browser sends the conversation, the agent's instructions, and its tools; the answer is
    Claude's content blocks, which the browser shows, runs the tool calls of, and sends back on
    the next turn. A 401 means the chat access code is wrong; a 502 means Claude did not answer.
    """
    try:
        return await service.turn(body)
    except AgentUnavailable as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error
