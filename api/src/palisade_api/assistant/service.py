"""Conversations with the in-page agent: starting one, replaying it, and running each turn."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import final
from uuid import UUID, uuid7

import structlog
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from palisade_api.activity.store import Activities
from palisade_api.agents.client import AgentHttp
from palisade_api.assistant.access import key_hash, key_matches, new_key
from palisade_api.assistant.events import (
    AssistantEvent,
    Conversation,
    CreatedConversation,
    DoneEvent,
    ErrorEvent,
    Incoming,
    NewConversation,
    ShopperMessage,
    TextEvent,
    Turn,
    TurnEvent,
    TurnRequest,
)
from palisade_api.assistant.model import Model, Refused, Usage
from palisade_api.assistant.models import ConversationRecord, TurnRecord
from palisade_api.assistant.tools import Shopping
from palisade_api.mandates.service import Mandates
from palisade_api.shop.models import CartRecord
from palisade_api.wallets.models import WalletRecord

# A turn still marked busy after this long crashed; the next message takes the conversation over.
STALE_TURN = timedelta(minutes=5)
TURN_EVENTS = TypeAdapter(list[TurnEvent])
TURN_REQUEST = TypeAdapter(TurnRequest)
# The shopper's side of a turn that a consent page answered, by the mandate's status.
ANSWERED: dict[str, TurnRequest] = {
    "signed": TurnRequest(
        kind="mandate_approved", text="You approved the request on the consent page."
    ),
    "declined": TurnRequest(kind="mandate_declined", text="You declined the request."),
    "expired": TurnRequest(
        kind="mandate_expired", text="The request expired before it was answered."
    ),
}

logger = structlog.stdlib.get_logger(__name__)


class UnknownConversation(LookupError):
    def __init__(self, conversation_id: UUID) -> None:
        super().__init__(f"no conversation has the id {conversation_id}")


class ConversationBusy(Exception):
    def __init__(self, conversation_id: UUID) -> None:
        super().__init__(f"conversation {conversation_id} is still answering")


class ConversationRefused(Exception):
    """The request did not carry the key the conversation was created with."""

    def __init__(self, conversation_id: UUID) -> None:
        super().__init__(f"the key does not open conversation {conversation_id}")


class MandateUnanswered(Exception):
    def __init__(self, mandate_id: UUID) -> None:
        super().__init__(f"mandate {mandate_id} has not been answered yet")


@dataclass(frozen=True)
class Started:
    """A turn the conversation has taken on: what runs it, and what the model is told."""

    conversation_id: UUID
    wallet_id: UUID
    cart_id: UUID | None
    history: list[JsonValue]
    position: int
    request: TurnRequest
    # The user message the model gets; the shopper's words, or a note on what they did.
    prompt: str


def turn(record: TurnRecord) -> Turn:
    return Turn(
        position=record.position,
        request=TURN_REQUEST.validate_python(record.request),
        events=TURN_EVENTS.validate_python(record.events),
        created_at=record.created_at,
        finished_at=record.finished_at,
    )


def conversation(record: ConversationRecord) -> Conversation:
    return Conversation(
        id=record.id,
        cart_id=record.cart_id,
        busy=record.busy_since is not None,
        turns=[turn(item) for item in record.turns],
        created_at=record.created_at,
    )


def merged(events: list[TurnEvent]) -> list[TurnEvent]:
    """The turn's events with each run of streamed text joined into one block."""
    result: list[TurnEvent] = []
    for event in events:
        last = result[-1] if result else None
        if isinstance(event, TextEvent) and isinstance(last, TextEvent):
            result[-1] = TextEvent(type="text", text=last.text + event.text)
        else:
            result.append(event)
    return result


@final
class Assistant:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model: Model,
        shop: AgentHttp,
        mandates: Mandates,
        activities: Activities,
        agent_id: str,
    ) -> None:
        self._sessions = sessions
        self._model = model
        self._shop = shop
        self._mandates = mandates
        self._activities = activities
        self._agent_id = agent_id
        # Turns outlive the response that streams them, so a closed tab never loses a purchase.
        self._turns: set[asyncio.Task[None]] = set()

    async def create(self, request: NewConversation) -> CreatedConversation:
        """A new conversation for the wallet, filling the browser's cart when it names one.

        The answer carries the conversation's key, the only time it is handed out.

        Raises:
            LookupError: the wallet cannot pay, or the cart does not exist.
        """
        key = new_key()
        async with self._sessions.begin() as session:
            wallet = await session.get(WalletRecord, request.wallet_id)
            if wallet is None or wallet.status != "active":
                raise LookupError(f"wallet {request.wallet_id} cannot pay")
            if (
                request.cart_id is not None
                and await session.get(CartRecord, request.cart_id) is None
            ):
                raise LookupError(f"no cart has the id {request.cart_id}")
            record = ConversationRecord(
                id=uuid7(),
                wallet_id=request.wallet_id,
                cart_id=request.cart_id,
                key_hash=key_hash(key),
                history=[],
                busy_since=None,
                turns=[],
            )
            session.add(record)
            await session.flush()
            self._activities.add(
                session,
                "conversation_started",
                agent_id=self._agent_id,
                conversation_id=record.id,
                with_cart=request.cart_id is not None,
            )
            created = conversation(await self._load(session, record.id))
            return CreatedConversation(
                id=created.id,
                cart_id=created.cart_id,
                busy=created.busy,
                turns=created.turns,
                created_at=created.created_at,
                key=key,
            )

    async def get(self, conversation_id: UUID, key: str | None) -> Conversation:
        """The conversation as stored, for the holder of its key.

        Raises:
            UnknownConversation: no such conversation.
            ConversationRefused: the key is not the conversation's.
        """
        async with self._sessions() as session:
            record = await self._load(session, conversation_id)
            if not key_matches(key, record.key_hash):
                raise ConversationRefused(conversation_id)
            return conversation(record)

    async def start_turn(
        self, conversation_id: UUID, key: str | None, incoming: Incoming
    ) -> Started:
        """Take the conversation for one turn and note what the shopper sent.

        Raises:
            UnknownConversation: no such conversation.
            ConversationRefused: the key is not the conversation's.
            ConversationBusy: another turn is still running.
            UnknownMandate, MandateUnanswered: the mandate outcome names no answered mandate.
        """
        request, prompt = await self._compose(incoming)
        async with self._sessions.begin() as session:
            record = await session.get(
                ConversationRecord, conversation_id, with_for_update=True
            )
            if record is None:
                raise UnknownConversation(conversation_id)
            if not key_matches(key, record.key_hash):
                raise ConversationRefused(conversation_id)
            now = await session.scalar(select(func.now()))
            if now is None:
                raise RuntimeError("the database returned no clock")
            if record.busy_since is not None and record.busy_since > now - STALE_TURN:
                raise ConversationBusy(conversation_id)
            record.busy_since = now
            taken = await session.scalar(
                select(func.count())
                .select_from(TurnRecord)
                .where(TurnRecord.conversation_id == conversation_id)
            )
            self._activities.add(
                session,
                "shopper_message",
                agent_id=self._agent_id,
                conversation_id=conversation_id,
                request_kind=request.kind,
                text=request.text,
            )
            return Started(
                conversation_id=conversation_id,
                wallet_id=record.wallet_id,
                cart_id=record.cart_id,
                history=list(record.history),
                position=(taken or 0) + 1,
                request=request,
                prompt=prompt,
            )

    async def events(self, started: Started) -> AsyncIterator[AssistantEvent]:
        """Run the turn and hand out its events as they happen; the last is `done` or `error`.

        The turn runs in its own task and is stored when it ends, whether or not anyone is
        still reading.
        """
        queue: asyncio.Queue[AssistantEvent | None] = asyncio.Queue()
        task = asyncio.create_task(self._run(started, queue))
        self._turns.add(task)
        task.add_done_callback(self._turns.discard)
        while (event := await queue.get()) is not None:
            yield event

    async def _run(
        self, started: Started, queue: asyncio.Queue[AssistantEvent | None]
    ) -> None:
        events: list[TurnEvent] = []

        def emit(event: TurnEvent) -> None:
            events.append(event)
            queue.put_nowait(event)

        shopping = Shopping(
            self._shop,
            self._activities,
            self._agent_id,
            started.conversation_id,
            started.wallet_id,
            started.cart_id,
            emit,
        )
        history: list[JsonValue] = [
            *started.history,
            {"role": "user", "content": started.prompt},
        ]
        try:
            reply = await self._model.reply(
                history,
                shopping.tools(),
                lambda text: emit(TextEvent(type="text", text=text)),
            )
            finished = await self._finish(
                started,
                shopping.cart_id,
                [*history, *reply.messages],
                merged(events),
                reply.usage,
            )
        except Refused:
            logger.warning(
                "assistant_refused", conversation_id=str(started.conversation_id)
            )
            await self._release(started.conversation_id)
            queue.put_nowait(
                ErrorEvent(
                    type="error",
                    detail="The assistant will not answer that. Ask about shoes, sizes, or your order.",
                )
            )
        except Exception:
            # The task is the last line of defence: an error here would otherwise vanish with
            # it and leave the conversation marked busy.
            logger.exception(
                "assistant_turn_failed", conversation_id=str(started.conversation_id)
            )
            await self._release(started.conversation_id)
            queue.put_nowait(
                ErrorEvent(
                    type="error",
                    detail="The assistant could not finish answering. Send your message again.",
                )
            )
        else:
            queue.put_nowait(
                DoneEvent(type="done", turn=finished, cart_id=shopping.cart_id)
            )
        finally:
            queue.put_nowait(None)

    async def _compose(self, incoming: Incoming) -> tuple[TurnRequest, str]:
        """The shopper's side of the turn: what the panel shows, and what the model is told."""
        if isinstance(incoming, ShopperMessage):
            return TurnRequest(kind="message", text=incoming.text), incoming.text
        mandate = await self._mandates.get(incoming.mandate_id)
        if mandate.status == "pending":
            raise MandateUnanswered(incoming.mandate_id)
        request = ANSWERED[mandate.status].model_copy(update={"mandate_id": mandate.id})
        signer = f", signed by {mandate.signed_by}" if mandate.signed_by else ""
        prompt = (
            f"[The shopper answered mandate {mandate.id} on the consent page: it is now "
            f"{mandate.status}{signer}. Continue from here.]"
        )
        return request, prompt

    async def _finish(
        self,
        started: Started,
        cart_id: UUID | None,
        history: list[JsonValue],
        events: list[TurnEvent],
        usage: Usage,
    ) -> Turn:
        async with self._sessions.begin() as session:
            record = await session.get_one(ConversationRecord, started.conversation_id)
            record.history = history
            record.cart_id = cart_id
            record.busy_since = None
            stored = TurnRecord(
                id=uuid7(),
                conversation_id=started.conversation_id,
                position=started.position,
                request=started.request.model_dump(mode="json"),
                events=TURN_EVENTS.dump_python(events, mode="json"),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                finished_at=func.now(),
            )
            session.add(stored)
            await session.flush()
            await session.refresh(stored)
            return turn(stored)

    async def _release(self, conversation_id: UUID) -> None:
        async with self._sessions.begin() as session:
            record = await session.get_one(ConversationRecord, conversation_id)
            record.busy_since = None

    @staticmethod
    async def _load(session: AsyncSession, conversation_id: UUID) -> ConversationRecord:
        record = await session.scalar(
            select(ConversationRecord)
            .where(ConversationRecord.id == conversation_id)
            .options(selectinload(ConversationRecord.turns))
        )
        if record is None:
            raise UnknownConversation(conversation_id)
        return record
