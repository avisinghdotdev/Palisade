"""The dispute jobs: the hourly read of what PayPal changed quietly, and the response to a cue."""

import psycopg
import structlog
from procrastinate import JobContext, RetryStrategy

from palisade_api.disputes.responses import NotYetOffered, Responses
from palisade_api.disputes.service import Disputes
from palisade_api.jobs import DISPUTES_QUEUE, EVIDENCE_QUEUE, context_value, jobs

logger = structlog.stdlib.get_logger(__name__)

# A claim opens a few minutes after an escalation; PayPal's own webhook usually cues the filing
# first, and this covers a dispute it stays quiet about. Twelve tries three minutes apart.
WAIT_FOR_PAYPAL = RetryStrategy(
    max_attempts=12, wait=180, retry_exceptions=[NotYetOffered]
)


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

    Queued on every change of the dispute's state. One job runs per dispute at a time, and one
    waits at most, so a burst of webhooks costs one run.
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

    Raises:
        AlreadyEnqueued: a job for the dispute waits already. The refusal is a unique
            violation, which aborts the transaction unless the caller wrapped the call in a
            savepoint.
    """
    return await respond_to_dispute.configure(
        connection=connection,
        lock=f"dispute:{dispute_id}",
        queueing_lock=f"respond:{dispute_id}",
    ).defer_async(dispute_id=dispute_id)
