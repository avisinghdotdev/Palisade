"""Verified webhook events in PostgreSQL."""

from datetime import datetime
from typing import final

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.webhooks.models import WebhookEventRecord

EVENTS_LISTED = 100


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


@final
class WebhookStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(self, event: WebhookEvent) -> bool:
        """Store the event; False when one with the same id is already stored."""
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
        return inserted is not None

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
            )
            for record in records
        ]
