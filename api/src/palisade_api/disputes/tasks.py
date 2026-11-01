"""The periodic job that reads disputes PayPal changed without telling the webhook."""

import structlog
from procrastinate import JobContext

from palisade_api.disputes.service import Disputes
from palisade_api.jobs import DISPUTES_QUEUE, context_value, jobs

logger = structlog.stdlib.get_logger(__name__)


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
