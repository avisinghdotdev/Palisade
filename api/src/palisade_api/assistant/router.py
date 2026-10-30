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
    NewConversation,
    ShopperMessage,
)
from palisade_api.assistant.service import (
    Assistant,
    ConversationBusy,
    ConversationRefused,
    ForeignCart,
    UnknownConversation,
)

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
    """The conversation, for the holder of its key.

    A 401 means the chat access code is wrong; a 403 means the conversation key is.
    """
    try:
        return await service.get(conversation_id, key)
    except UnknownConversation as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ConversationRefused as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error


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
    message: ShopperMessage,
    service: Annotated[Assistant, Depends(assistant)],
) -> EventStream:
    """Send the shopper's message and stream the agent's turn.

    An answer on the consent page needs no message: the API carries the conversation on by
    itself, and the conversation shows that turn as it runs. A 401 means the chat access code
    is wrong; a 403 means the conversation's key is missing or wrong. A 404 names a
    conversation or cart that cannot be used. A 409 means the agent is still answering.
    """
    try:
        started = await service.start_turn(conversation_id, key, message)
    except (UnknownConversation, ForeignCart) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ConversationRefused as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    except ConversationBusy as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return EventStream(service.events(started))
