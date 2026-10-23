"""Jobs that process stored webhook events."""

from collections.abc import Awaitable, Callable
from typing import cast

import psycopg
import structlog
from procrastinate import JobContext, RetryStrategy

from palisade_api.jobs import jobs
from palisade_api.webhooks.store import WebhookEvent, WebhookStore

logger = structlog.stdlib.get_logger(__name__)

# Handles one event of the type it is registered for; a raised error retries the job.
Handler = Callable[[WebhookEvent], Awaitable[None]]

# Five retries waiting 4, 16, 64, 256, and 1,024 seconds, about 23 minutes in all: longer than
# PayPal's minutes-long delay before a new dispute accepts actions.
RETRY = RetryStrategy(max_attempts=5, exponential_wait=4)


def webhook_store(context: JobContext) -> WebhookStore:
    store = _from_context(context, "webhook_store")
    if not isinstance(store, WebhookStore):
        raise TypeError(f"webhook_store in the job context is a {type(store).__name__}")
    return store


def webhook_handlers(context: JobContext) -> dict[str, Handler]:
    handlers = _from_context(context, "webhook_handlers")
    if not isinstance(handlers, dict):
        raise TypeError(
            f"webhook_handlers in the job context is a {type(handlers).__name__}"
        )
    return cast("dict[str, Handler]", handlers)


def _from_context(context: JobContext, key: str) -> object:
    # Procrastinate types the context as a bare dict; the lifespan fills it.
    return cast("dict[str, object]", context.additional_context)[key]


@jobs.task(retry=RETRY, pass_context=True)
async def process_webhook_event(context: JobContext, event_id: str) -> None:
    """Hand one stored event to the handler for its type; a failure is retried with backoff."""
    store = webhook_store(context)
    event = await store.get(event_id)
    handler = webhook_handlers(context).get(event.event_type)
    if handler is not None:
        await handler(event)
    await store.mark_processed(event_id)
    logger.info(
        "webhook_event_processed",
        event_id=event_id,
        event_type=event.event_type,
        handled=handler is not None,
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
