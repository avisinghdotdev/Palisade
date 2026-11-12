from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from palisade_api.disputes.router import DisputeId, paypal_refused
from palisade_api.disputes.service import Dispute
from palisade_api.orders.router import OrderNumber
from palisade_api.orders.store import UnknownOrder
from palisade_api.paypal.client import PayPalError
from palisade_api.sandbox.simulator import (
    AdjudicationRequest,
    DisputeRequest,
    NotAdjudicable,
    NotDisputable,
    NotSearchableYet,
    Simulator,
)

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])


def simulator(request: Request) -> Simulator:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Simulator here.
    service: Simulator = request.state.simulator  # pyright: ignore[reportAny]
    return service


@router.post(
    "/orders/{number}/disputes",
    status_code=status.HTTP_201_CREATED,
    operation_id="simulate_dispute",
)
async def simulate_dispute(
    number: OrderNumber,
    request: DisputeRequest,
    service: Annotated[Simulator, Depends(simulator)],
) -> Dispute:
    """Sandbox simulator: file a dispute on a paid demo-buyer order as the demo buyer.

    PayPal's sandbox takes no buyer text by API, so Palisade keeps the complaint itself and
    marks it as the simulator's wherever it shows. A 409 means the order cannot be disputed
    this way, or PayPal's search does not list the payment yet; a 422 means PayPal refused
    the dispute.
    """
    try:
        return await service.file_dispute(number, request.reason, request.complaint)
    except UnknownOrder as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (NotDisputable, NotSearchableYet) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except PayPalError as error:
        raise paypal_refused(error, "did not open the dispute") from error


@router.post("/disputes/{dispute_id}/adjudicate", operation_id="simulate_adjudication")
async def simulate_adjudication(
    dispute_id: DisputeId,
    request: AdjudicationRequest,
    service: Annotated[Simulator, Depends(simulator)],
) -> Dispute:
    """Sandbox simulator: have PayPal decide the dispute for the seller or the buyer.

    A 409 means PayPal does not offer a decision yet.
    """
    try:
        return await service.adjudicate(dispute_id, request.outcome)
    except NotAdjudicable as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except PayPalError as error:
        raise paypal_refused(error, "did not decide the dispute") from error
