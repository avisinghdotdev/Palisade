"""Verified webhook events in PostgreSQL, each queued for processing as it is stored."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import final

import psycopg
from pydantic import BaseModel, JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.jobs import driver_connection
from palisade_api.webhooks.models import WebhookEventRecord

EVENTS_LISTED = 100

# Queues the processing of the event with the given id on the given connection; the job id.
Enqueue = Callable[[psycopg.AsyncConnection[object], str], Awaitable[int]]


class WebhookEvent(BaseModel):
    id: str
    event_type: str
    create_time: datetime
    resource_type: str
    resource_version: str | None = None
    summary: str
    resource: dict[str, JsonValue]


class ReceivedEvent(BaseModel):
    event: WebhookEvent
    received_at: datetime
    processed_at: datetime | None


class UnknownEvent(LookupError):
    def __init__(self, event_id: str) -> None:
        super().__init__(f"no stored webhook event has the id {event_id}")


@final
class WebhookStore:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], enqueue: Enqueue
    ) -> None:
        self._sessions = sessions
        self._enqueue = enqueue

    async def add(self, event: WebhookEvent) -> int | None:
        """Store the event and queue its processing in one transaction; the job id.

        None when an event with the same id is already stored: nothing is written or queued.
        """
        statement = (
            insert(WebhookEventRecord)
            .values(
                id=event.id,
                event_type=event.event_type,
                payload=event.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(index_elements=[WebhookEventRecord.id])
            .returning(WebhookEventRecord.id)
        )
        async with self._sessions.begin() as session:
            inserted = (await session.execute(statement)).scalar_one_or_none()
            if inserted is None:
                return None
            return await self._enqueue(await driver_connection(session), event.id)

    async def replay(self, event_id: str) -> int:
        """Queue a stored event for processing again; the job id."""
        stored = select(WebhookEventRecord.id).where(WebhookEventRecord.id == event_id)
        async with self._sessions.begin() as session:
            if await session.scalar(stored) is None:
                raise UnknownEvent(event_id)
            return await self._enqueue(await driver_connection(session), event_id)

    async def mark_processed(self, event_id: str) -> WebhookEvent:
        """Record that the event's processing finished; the event itself."""
        statement = (
            update(WebhookEventRecord)
            .where(WebhookEventRecord.id == event_id)
            .values(processed_at=func.now())
            .returning(WebhookEventRecord.payload)
        )
        async with self._sessions.begin() as session:
            payload = (await session.execute(statement)).scalar_one_or_none()
        if payload is None:
            raise UnknownEvent(event_id)
        return WebhookEvent.model_validate(payload)

    async def events(self) -> list[ReceivedEvent]:
        """The most recent events, newest first."""
        statement = (
            select(WebhookEventRecord)
            .order_by(WebhookEventRecord.received_at.desc())
            .limit(EVENTS_LISTED)
        )
        async with self._sessions() as session:
            records = (await session.scalars(statement)).all()
        return [
            ReceivedEvent(
                event=WebhookEvent.model_validate(record.payload),
                received_at=record.received_at,
                processed_at=record.processed_at,
            )
            for record in records
        ]
