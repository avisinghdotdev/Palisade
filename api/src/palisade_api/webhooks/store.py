"""Verified webhook events, kept in memory until the database slice persists them."""

from collections import OrderedDict
from datetime import UTC, datetime
from typing import final

from pydantic import BaseModel, JsonValue


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
    """Events by id, newest first, capped so memory stays bounded."""

    def __init__(self, capacity: int = 1000) -> None:
        self._capacity = capacity
        self._events: OrderedDict[str, ReceivedEvent] = OrderedDict()

    def add(self, event: WebhookEvent) -> bool:
        """Store the event; False when one with the same id is already here."""
        if event.id in self._events:
            return False
        self._events[event.id] = ReceivedEvent(
            event=event, received_at=datetime.now(UTC)
        )
        while len(self._events) > self._capacity:
            self._events.popitem(last=False)
        return True

    def events(self) -> list[ReceivedEvent]:
        return list(reversed(self._events.values()))
