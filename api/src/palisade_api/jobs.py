"""The job queue: Procrastinate on the API's database, with the worker inside the API process."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import cast

import psycopg
import structlog
from procrastinate import App, JobContext, PsycopgConnector
from sqlalchemy.ext.asyncio import AsyncSession

# Jobs still running when the API stops get this long to finish before they are aborted for a retry.
SHUTDOWN_GRACE_SECONDS = 20.0

logger = structlog.stdlib.get_logger(__name__)

# Each feature's tasks sit on their own queue, and the worker listens only to the queues this
# build knows. During a rolling release the old build's worker still runs for a few seconds; a
# job it fetched for a task it had never heard of failed for good, because an unknown task is
# not retried. A task added in a release goes on a new queue listed here, so an older worker
# never fetches it.
WEBHOOKS_QUEUE = "webhooks"
ORDERS_QUEUE = "orders"
WORKER_QUEUES = (WEBHOOKS_QUEUE, ORDERS_QUEUE)

# Tasks register when their module is imported; the worker imports these before it starts. This
# connector is never opened: a job is deferred on the caller's own connection, and the worker
# opens its own connector in `run_worker`.
jobs = App(
    connector=PsycopgConnector(),
    import_paths=["palisade_api.webhooks.tasks", "palisade_api.orders.tasks"],
)


def context_value[T](context: JobContext, key: str, kind: type[T]) -> T:
    """`key` from the context `run_worker` was given, checked to be a `kind`."""
    # Procrastinate types the context as a bare dict; the lifespan fills it.
    value = cast("dict[str, object]", context.additional_context)[key]
    if not isinstance(value, kind):
        raise TypeError(f"{key} in the job context is a {type(value).__name__}")
    return value


async def driver_connection(session: AsyncSession) -> psycopg.AsyncConnection[object]:
    """The psycopg connection under `session`, so a job can be deferred inside its transaction."""
    proxied = await (await session.connection()).get_raw_connection()
    connection = proxied.driver_connection
    if not isinstance(connection, psycopg.AsyncConnection):
        raise TypeError(
            f"the session runs on {type(connection).__name__}, not a psycopg AsyncConnection"
        )
    return connection


@asynccontextmanager
async def run_worker(
    database_url: str, context: dict[str, object]
) -> AsyncGenerator[None]:
    """Run the worker on its own small pool while the block is open.

    `context` reaches every task as `JobContext.additional_context`.
    """
    connector = PsycopgConnector(conninfo=database_url, min_size=1, max_size=2)
    with jobs.replace_connector(connector):
        async with jobs.open_async():
            worker = asyncio.create_task(
                jobs.run_worker_async(
                    queues=WORKER_QUEUES,
                    install_signal_handlers=False,
                    additional_context=context,
                    shutdown_graceful_timeout=SHUTDOWN_GRACE_SECONDS,
                ),
                name="job worker",
            )
            worker.add_done_callback(_report_death)
            try:
                yield
            finally:
                if not worker.done():
                    worker.cancel()
                    with suppress(asyncio.CancelledError):
                        await worker


def _report_death(worker: asyncio.Task[None]) -> None:
    # A worker that dies leaves every queued job waiting, so its death goes to the API's logs.
    if not worker.cancelled() and worker.exception() is not None:
        logger.error("job_worker_died", exc_info=worker.exception())
