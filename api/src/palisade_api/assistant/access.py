"""What opens the chat: one shared code the merchant hands out, and one key per conversation.

The code says who may use Claude at all. The key says whose conversation it is: the browser
that started it got the key once, and nobody else can read or continue it.
"""

import secrets
from typing import Annotated, final

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

CHAT_KEY_HEADER = "X-Chat-Key"
CONVERSATION_KEY_HEADER = "X-Conversation-Key"

chat_key = APIKeyHeader(
    name=CHAT_KEY_HEADER,
    scheme_name="ChatKey",
    description="The access code the chat panel asks for, sent with every chat request.",
    auto_error=False,
)
conversation_key = APIKeyHeader(
    name=CONVERSATION_KEY_HEADER,
    scheme_name="ConversationKey",
    description="The key a conversation was created with, sent when reading or continuing it.",
    auto_error=False,
)


@final
class ChatAccess:
    def __init__(self, key: str) -> None:
        self._key = key.encode()

    def allows(self, key: str | None) -> bool:
        """Whether the header value is the configured code, compared in constant time."""
        return key is not None and secrets.compare_digest(key.encode(), self._key)


def chat_access(request: Request) -> ChatAccess:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a ChatAccess here.
    access: ChatAccess = request.state.chat_access  # pyright: ignore[reportAny]
    return access


async def require_chat_key(
    key: Annotated[str | None, Depends(chat_key)],
    access: Annotated[ChatAccess, Depends(chat_access)],
) -> None:
    """Refuse the request with a 401 unless it carries the access code."""
    if not access.allows(key):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "The access code is not right."
        )
