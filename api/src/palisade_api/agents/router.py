from typing import Annotated

from fastapi import APIRouter, Depends, Request

from palisade_api.agents.registry import Agents, RegisteredAgent

router = APIRouter(prefix="/api/agents", tags=["agents"])


def agents(request: Request) -> Agents:
    # Starlette types lifespan state as Any; the lifespan in main.py puts an Agents here.
    service: Agents = request.state.agents  # pyright: ignore[reportAny]
    return service


@router.get("", operation_id="agents")
async def list_agents(
    service: Annotated[Agents, Depends(agents)],
) -> list[RegisteredAgent]:
    """The agents the merchant knows, with the public keys their signed requests must match."""
    return await service.list()
