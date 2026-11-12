from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status

from palisade_api.disputes.alerts import AlertRefund, Alerts, NotAnAlert, NotRefundable
from palisade_api.disputes.narrative import NarrativeTooLong, NarrativeUnavailable
from palisade_api.disputes.responses import (
    DisputeResponse,
    NothingToFile,
    Responses,
    UnknownEvidence,
)
from palisade_api.disputes.service import (
    Dispute,
    Disputes,
    DisputeSummary,
    NotTheShops,
    UnknownDispute,
)
from palisade_api.disputes.triage import (
    NothingToConfirm,
    TriageUnavailable,
    UnknownTriage,
)
from palisade_api.paypal.client import PayPalError

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


def disputes(request: Request) -> Disputes:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Disputes here.
    service: Disputes = request.state.disputes  # pyright: ignore[reportAny]
    return service


def responses(request: Request) -> Responses:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Responses here.
    service: Responses = request.state.responses  # pyright: ignore[reportAny]
    return service


def alerts(request: Request) -> Alerts:
    # Starlette types lifespan state as Any; the lifespan in main.py puts an Alerts here.
    service: Alerts = request.state.alerts  # pyright: ignore[reportAny]
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


@router.get("/{dispute_id}/response", operation_id="dispute_response")
async def get_response(
    dispute_id: DisputeId, service: Annotated[Responses, Depends(responses)]
) -> DisputeResponse:
    """Palisade's answer to the dispute as it stands: what it can do next, and the evidence so far."""
    try:
        return await service.view(dispute_id)
    except UnknownDispute as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/{dispute_id}/response", operation_id="prepare_response")
async def prepare_response(
    dispute_id: DisputeId, service: Annotated[Responses, Depends(responses)]
) -> DisputeResponse:
    """Take the dispute one step on now, as the job would on PayPal's cue.

    An inquiry that waits for the seller is triaged: Claude picks the answer. A picked fight,
    and every dispute past its inquiry, gets the notes and the record. A 502 means Claude
    did not deliver; a 422 names what PayPal refused of a picked answer.
    """
    try:
        return await service.prepare(dispute_id)
    except UnknownDispute as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (TriageUnavailable, NarrativeUnavailable, NarrativeTooLong) as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error
    except PayPalError as error:
        raise paypal_refused(error, "did not take the answer") from error


@router.post("/{dispute_id}/triage/{triage_id}/confirm", operation_id="confirm_triage")
async def confirm_triage(
    dispute_id: DisputeId,
    triage_id: UUID,
    service: Annotated[Responses, Depends(responses)],
) -> DisputeResponse:
    """The merchant's go on the answer Claude picked for an inquiry: PayPal carries it out.

    A message is posted in PayPal's thread, an offer is made, or the claim is accepted with a
    full refund. A 409 means the answer went to PayPal already; a 422 names what PayPal
    refused.
    """
    try:
        return await service.confirm(dispute_id, triage_id)
    except UnknownTriage as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except NothingToConfirm as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except PayPalError as error:
        raise paypal_refused(error, "did not take the answer") from error


@router.post("/{dispute_id}/evidence/{evidence_id}/file", operation_id="file_evidence")
async def file_evidence(
    dispute_id: DisputeId,
    evidence_id: UUID,
    service: Annotated[Responses, Depends(responses)],
) -> DisputeResponse:
    """Approve the prepared evidence and send what PayPal takes now.

    An inquiry that must become a claim is escalated, and the evidence follows once the claim
    takes it. A 409 means the evidence was filed or refused already; a 422 names what PayPal
    refused.
    """
    try:
        return await service.file(dispute_id, evidence_id)
    except (UnknownDispute, UnknownEvidence) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except NothingToFile as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except PayPalError as error:
        raise paypal_refused(error, "did not take the evidence") from error


@router.get(
    "/{dispute_id}/evidence/{evidence_id}/document",
    operation_id="evidence_document",
    response_class=Response,
    responses={
        200: {
            "description": "The record PDF Palisade attached to the evidence.",
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def evidence_document(
    dispute_id: DisputeId,
    evidence_id: UUID,
    service: Annotated[Responses, Depends(responses)],
) -> Response:
    try:
        document = await service.document(dispute_id, evidence_id)
    except UnknownEvidence as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return Response(
        document.content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{document.name}"'},
    )


@router.get("/{dispute_id}/alert", operation_id="dispute_alert")
async def get_alert(
    dispute_id: DisputeId, service: Annotated[Alerts, Depends(alerts)]
) -> AlertRefund:
    """Where a pre-chargeback alert's refund stands: PayPal's deadline, the timer, the refund.

    A 404 means no stored dispute has the id, or the dispute is not an alert.
    """
    try:
        return await service.view(dispute_id)
    except (UnknownDispute, NotAnAlert) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/{dispute_id}/alert/refund", operation_id="refund_alert")
async def refund_alert(
    dispute_id: DisputeId, service: Annotated[Alerts, Depends(alerts)]
) -> AlertRefund:
    """Refund the alert's order in full now; asking again answers with the refund made.

    A 409 means the alert is resolved or the order is not a captured payment; a 422 names
    what PayPal refused.
    """
    try:
        return await service.refund(dispute_id, "console")
    except (UnknownDispute, NotAnAlert) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except NotRefundable as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except PayPalError as error:
        raise paypal_refused(error, "did not refund the payment") from error
