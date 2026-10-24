"""The periodic job that finishes orders a checkout left halfway."""

import structlog
from procrastinate import JobContext

from palisade_api.jobs import context_value, jobs
from palisade_api.orders.repair import Repairs

logger = structlog.stdlib.get_logger(__name__)


# Every five minutes while a worker runs; on a worker's start the latest missed run is caught up
# when it is under ten minutes old. The lock keeps two runs from overlapping across replicas.
@jobs.periodic(cron="*/5 * * * *")
@jobs.task(lock="repair_orders", pass_context=True)
async def repair_orders(context: JobContext, timestamp: int) -> None:
    looked_at = await context_value(context, "repairs", Repairs).run()
    if looked_at:
        logger.info(
            "orders_repair_run",
            order_numbers=looked_at,
            scheduled_for=timestamp,
            job_id=context.job.id,
        )
