"""Jobs that process stored webhook events."""

from typing import cast

import psycopg
import structlog
from procrastinate import JobContext, RetryStrategy

from palisade_api.jobs import jobs
from palisade_api.webhooks.store import WebhookStore

logger = structlog.stdlib.get_logger(__name__)

# Five retries waiting 4, 16, 64, 256, and 1,024 seconds, about 23 minutes in all: longer than
# PayPal's minutes-long delay before a new dispute accepts actions.
RETRY = RetryStrategy(max_attempts=5, exponential_wait=4)


def webhook_store(context: JobContext) -> WebhookStore:
    # Procrastinate types the context as a bare dict; the lifespan fills it with the store.
    store = cast("dict[str, object]", context.additional_context)["webhook_store"]
    if not isinstance(store, WebhookStore):
        raise TypeError(f"webhook_store in the job context is a {type(store).__name__}")
    return store


@jobs.task(retry=RETRY, pass_context=True)
async def process_webhook_event(context: JobContext, event_id: str) -> None:
    """Handle one stored event; a failure is retried with backoff."""
    event = await webhook_store(context).mark_processed(event_id)
    logger.info(
        "webhook_event_processed",
        event_id=event_id,
        event_type=event.event_type,
        job_id=context.job.id,
        attempt=context.job.attempts,
    )


async def enqueue_processing(
    connection: psycopg.AsyncConnection[object], event_id: str
) -> int:
    """Queue `process_webhook_event` on `connection`, inside the transaction it holds; the job id."""
    return await process_webhook_event.configure(connection=connection).defer_async(
        event_id=event_id
    )
