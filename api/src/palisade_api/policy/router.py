from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from palisade_api.policy.engine import FilingModeChange, Policies, Policy

router = APIRouter(prefix="/api/policy", tags=["policy"])


def policies(request: Request) -> Policies:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Policies here.
    service: Policies = request.state.policies  # pyright: ignore[reportAny]
    return service


@router.get("", operation_id="policy")
async def get_policy(service: Annotated[Policies, Depends(policies)]) -> Policy:
    """The merchant's policy in force: the rules agents are checked against, and the filing mode."""
    try:
        return await service.get()
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.put("/dispute-filing", operation_id="set_dispute_filing")
async def set_dispute_filing(
    change: FilingModeChange, service: Annotated[Policies, Depends(policies)]
) -> Policy:
    """Choose whether Palisade waits for the merchant before filing evidence, or files on its own."""
    try:
        return await service.set_dispute_filing(change.mode)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
