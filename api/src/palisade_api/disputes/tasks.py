"""The dispute jobs: the hourly reconciliation, the response to a cue, and an alert's refund."""

from datetime import datetime

import psycopg
import structlog
from procrastinate import JobContext, RetryStrategy

from palisade_api.disputes.alerts import Alerts
from palisade_api.disputes.narrative import NarrativeUnavailable
from palisade_api.disputes.responses import NotYetOffered, Responses
from palisade_api.disputes.service import Disputes
from palisade_api.disputes.triage import TriageUnavailable
from palisade_api.jobs import (
    ALERTS_QUEUE,
    DISPUTES_QUEUE,
    EVIDENCE_QUEUE,
    context_value,
    jobs,
)
from palisade_api.paypal.client import PayPalError

logger = structlog.stdlib.get_logger(__name__)

# A claim opens a few minutes after an escalation; PayPal's own webhook usually cues the filing
# first, and this covers a dispute it stays quiet about. It also covers a call PayPal did not
# answer and a pick or notes Claude did not deliver, which no later event would cue again.
# Twelve tries three minutes apart.
WAIT_FOR_PAYPAL = RetryStrategy(
    max_attempts=12,
    wait=180,
    retry_exceptions=[NotYetOffered, TriageUnavailable, NarrativeUnavailable],
)
# A refund PayPal did not answer is tried again under the same request id, within the hour
# PayPal's window still leaves: four tries two minutes apart. A refusal is not raised.
REFUND_AGAIN = RetryStrategy(max_attempts=4, wait=120, retry_exceptions=[PayPalError])


# Hourly while a worker runs; the lock keeps two runs from overlapping across replicas.
@jobs.periodic(cron="41 * * * *")
@jobs.task(queue=DISPUTES_QUEUE, lock="reconcile_disputes", pass_context=True)
async def reconcile_disputes(context: JobContext, timestamp: int) -> None:
    result = await context_value(context, "disputes", Disputes).reconcile()
    logger.info(
        "disputes_reconciled",
        read=result.read,
        ignored=result.ignored,
        failed=result.failed,
        scheduled_for=timestamp,
        job_id=context.job.id,
    )


@jobs.task(queue=EVIDENCE_QUEUE, retry=WAIT_FOR_PAYPAL, pass_context=True)
async def respond_to_dispute(context: JobContext, dispute_id: str) -> None:
    """Prepare the seller's answer to the dispute and file what PayPal takes now.

    Queued on every change of the dispute's state; a run with nothing to do ends at once.
    """
    response = await context_value(context, "responses", Responses).respond(dispute_id)
    logger.info(
        "dispute_responded",
        dispute_id=dispute_id,
        action=response.action,
        filing_mode=response.filing_mode,
        job_id=context.job.id,
        attempt=context.job.attempts,
    )


async def enqueue_response(
    connection: psycopg.AsyncConnection[object], dispute_id: str
) -> int:
    """Queue `respond_to_dispute` on `connection`, inside the transaction it holds; the job id.

    The lock runs one job per dispute at a time, so two never file at once. There is no
    queueing lock: refusing a second waiting job is a unique violation, which aborts the
    caller's transaction and with it the dispute change being stored, and it also stopped a
    running job from scheduling its retry once that job had queued its successor (both seen
    live on 2026-11-03).
    """
    return await respond_to_dispute.configure(
        connection=connection, lock=f"dispute:{dispute_id}"
    ).defer_async(dispute_id=dispute_id)


@jobs.task(queue=ALERTS_QUEUE, retry=REFUND_AGAIN, pass_context=True)
async def refund_alert(context: JobContext, dispute_id: str) -> None:
    """Refund a pre-chargeback alert's order before PayPal's window closes."""
    await context_value(context, "alerts", Alerts).refund_on_time(dispute_id)
    logger.info(
        "alert_refund_ran",
        dispute_id=dispute_id,
        job_id=context.job.id,
        attempt=context.job.attempts,
    )


async def schedule_refund(
    connection: psycopg.AsyncConnection[object], dispute_id: str, at: datetime
) -> int:
    """Queue `refund_alert` to run at `at`, on `connection` inside its transaction; the job id.

    Its lock is the alert's own, not the dispute's: the worker holds back every job behind a
    waiting one under the same lock, so a refund due hours ahead under the dispute's lock would
    stall the dispute's response jobs until then.
    """
    return await refund_alert.configure(
        connection=connection, lock=f"alert-refund:{dispute_id}", schedule_at=at
    ).defer_async(dispute_id=dispute_id)


async def cancel_refund(
    connection: psycopg.AsyncConnection[object], job_id: int
) -> bool:
    """Cancel the waiting refund job on `connection`; False when it already ran or is running."""
    return await jobs.job_manager.cancel_job_by_id_async(job_id, connection=connection)
