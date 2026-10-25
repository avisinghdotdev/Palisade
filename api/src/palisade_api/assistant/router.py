from collections.abc import AsyncIterator
from typing import Annotated, final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from palisade_api.assistant.access import conversation_key, require_chat_key
from palisade_api.assistant.events import (
    AssistantEvent,
    Conversation,
    CreatedConversation,
    Incoming,
    NewConversation,
)
from palisade_api.assistant.service import (
    Assistant,
    ConversationBusy,
    ConversationRefused,
    MandateUnanswered,
    UnknownConversation,
)
from palisade_api.mandates.service import UnknownMandate

router = APIRouter(
    prefix="/api/assistant",
    tags=["assistant"],
    dependencies=[Depends(require_chat_key)],
)


@final
class EventStream(StreamingResponse):
    """Server-sent events: one `data:` line per event, left unbuffered by the proxies."""

    media_type = "text/event-stream"

    def __init__(self, events: AsyncIterator[AssistantEvent]) -> None:
        super().__init__(
            frames(events),
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


async def frames(events: AsyncIterator[AssistantEvent]) -> AsyncIterator[str]:
    async for event in events:
        yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"


def assistant(request: Request) -> Assistant:
    # Starlette types lifespan state as Any; the lifespan in main.py puts an Assistant here.
    service: Assistant = request.state.assistant  # pyright: ignore[reportAny]
    return service


@router.get(
    "/access", status_code=status.HTTP_204_NO_CONTENT, operation_id="assistant_access"
)
async def access() -> None:
    """Answers 204 when the access code is right; the chat panel's code screen checks it here."""


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_conversation",
)
async def create_conversation(
    body: NewConversation, service: Annotated[Assistant, Depends(assistant)]
) -> CreatedConversation:
    """Start a conversation paid from the wallet; a 404 names a wallet or cart that cannot be used.

    The answer carries the conversation's key, handed out this once; reading or continuing the
    conversation needs it.
    """
    try:
        return await service.create(body)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.get("/conversations/{conversation_id}", operation_id="conversation")
async def get_conversation(
    conversation_id: UUID,
    key: Annotated[str | None, Depends(conversation_key)],
    service: Annotated[Assistant, Depends(assistant)],
) -> Conversation:
    """The conversation, for the holder of its key; a 401 without it."""
    try:
        return await service.get(conversation_id, key)
    except UnknownConversation as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ConversationRefused as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error


@router.post(
    "/conversations/{conversation_id}/messages",
    operation_id="send_message",
    status_code=status.HTTP_200_OK,
    response_class=EventStream,
    responses={
        200: {
            "model": AssistantEvent,
            "description": "The agent's turn as server-sent events, ending with `done` or `error`.",
        }
    },
)
async def send_message(
    conversation_id: UUID,
    key: Annotated[str | None, Depends(conversation_key)],
    incoming: Incoming,
    service: Annotated[Assistant, Depends(assistant)],
) -> EventStream:
    """Send the shopper's message, or the outcome of a consent page, and stream the agent's turn.

    A 401 means the conversation's key is missing or wrong. A 409 means the agent is still
    answering the last message, or the mandate named has no answer yet.
    """
    try:
        started = await service.start_turn(conversation_id, key, incoming)
    except (UnknownConversation, UnknownMandate) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ConversationRefused as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
    except (ConversationBusy, MandateUnanswered) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return EventStream(service.events(started))
