"""Conversations with the in-page agent: starting one, replaying it, and running each turn."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import final
from uuid import UUID, uuid7

import structlog
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from palisade_api.activity.store import Activities
from palisade_api.agents.client import AgentHttp
from palisade_api.assistant.events import (
    AssistantEvent,
    Conversation,
    CreatedConversation,
    DoneEvent,
    ErrorEvent,
    LiveTurn,
    MandateAttachment,
    NewConversation,
    ShopperMessage,
    TextEvent,
    ToolResultEvent,
    Turn,
    TurnEvent,
    TurnRequest,
)
from palisade_api.assistant.model import Model, Refused, Usage
from palisade_api.assistant.models import ConversationRecord, TurnRecord
from palisade_api.assistant.tools import Shopping
from palisade_api.bearer import key_hash, key_matches, new_key
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
REFUSED = "The assistant will not answer that. Ask about shoes, sizes, or your order."
FAILED = "The assistant could not finish answering. Send your message again."
# Shown when the turn already changed something: sending the message again would repeat it.
STOPPED = (
    "The assistant stopped partway. What it did is kept above; ask it to carry on."
)
# The last words of a turn the API ran by itself that failed before doing anything.
HALTED = (
    "The assistant could not carry on from your answer. Send a message to continue."
)

# Where a turn's events go as they happen: the response streaming it; None closes the stream.
Deliver = Callable[[AssistantEvent | None], None]

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


class ForeignCart(LookupError):
    """The cart does not exist, or another wallet pays for it."""

    def __init__(self, cart_id: UUID) -> None:
        super().__init__(f"no cart {cart_id} belongs to this wallet")


@dataclass(frozen=True)
class Started:
    """A turn the conversation has taken on: what runs it, and what the model is told."""

    conversation_id: UUID
    wallet_id: UUID
    cart_id: UUID | None
    history: list[JsonValue]
    request: TurnRequest
    # The user message the model gets; the shopper's words, or a note on what they did.
    prompt: str
    # When the turn took the conversation; it saves its progress only while this still holds.
    lease: datetime


def turn(record: TurnRecord) -> Turn:
    return Turn(
        position=record.position,
        request=TURN_REQUEST.validate_python(record.request),
        events=TURN_EVENTS.validate_python(record.events),
        created_at=record.created_at,
        finished_at=record.finished_at,
    )


def running(record: ConversationRecord, now: datetime) -> bool:
    """Whether a turn holds the conversation; one that outlived its lease crashed."""
    return record.busy_since is not None and record.busy_since > now - STALE_TURN


def conversation(record: ConversationRecord, now: datetime) -> Conversation:
    busy = running(record, now)
    return Conversation(
        id=record.id,
        cart_id=record.cart_id,
        busy=busy,
        turns=[turn(item) for item in record.turns],
        live=LiveTurn.model_validate(record.live)
        if busy and record.live is not None
        else None,
        awaiting=record.awaiting_mandate_id,
        created_at=record.created_at,
    )


def steps_taken(events: list[TurnEvent]) -> list[ToolResultEvent]:
    return [event for event in events if isinstance(event, ToolResultEvent)]


def awaited(events: list[TurnEvent]) -> UUID | None:
    """The mandate the turn's last mandate card leaves waiting for the shopper, if it does."""
    cards = [
        step.attachment.mandate
        for step in steps_taken(events)
        if isinstance(step.attachment, MandateAttachment)
    ]
    return cards[-1].id if cards and cards[-1].status == "pending" else None


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
            LookupError: the wallet cannot pay, or the cart is not the wallet's.
        """
        key = new_key()
        async with self._sessions.begin() as session:
            wallet = await session.get(WalletRecord, request.wallet_id)
            if wallet is None or wallet.status != "active":
                raise LookupError(f"wallet {request.wallet_id} cannot pay")
            if request.cart_id is not None:
                await self._own_cart(session, request.cart_id, request.wallet_id)
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
            created = conversation(
                await self._load(session, record.id), await self._clock(session)
            )
            return CreatedConversation.model_validate(
                {**created.model_dump(), "key": key}
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
            return conversation(record, await self._clock(session))

    async def start_turn(
        self, conversation_id: UUID, key: str | None, message: ShopperMessage
    ) -> Started:
        """Take the conversation for one turn and note what the shopper sent.

        Raises:
            UnknownConversation: no such conversation.
            ConversationRefused: the key is not the conversation's.
            ConversationBusy: another turn is still running.
            ForeignCart: the message names a cart the conversation's wallet does not pay for.
        """
        async with self._sessions.begin() as session:
            record = await session.get(
                ConversationRecord, conversation_id, with_for_update=True
            )
            if record is None:
                raise UnknownConversation(conversation_id)
            if not key_matches(key, record.key_hash):
                raise ConversationRefused(conversation_id)
            now = await self._clock(session)
            if running(record, now):
                raise ConversationBusy(conversation_id)
            # The bag the shopper sees now, which may be one they filled in the shop since.
            if message.cart_id is not None:
                await self._own_cart(session, message.cart_id, record.wallet_id)
                record.cart_id = message.cart_id
            return self._take(
                session,
                record,
                now,
                TurnRequest(kind="message", text=message.text),
                message.text,
            )

    async def continue_after(self, mandate_id: UUID) -> None:
        """Carry on the conversation waiting on the mandate once it is answered, in a turn of its own.

        The mandates service calls this after storing an answer, and the turn that asked for the
        mandate calls it as it ends, in case the answer came first. The answer is stored either
        way, so a failure here is logged and not passed back to whoever answered; the shopper's
        next message carries the conversation on instead.
        """
        try:
            started = await self._take_answered(mandate_id)
        except Exception:
            logger.exception("assistant_continue_failed", mandate_id=str(mandate_id))
            return
        if started is not None:
            self._spawn(started, None)

    async def events(self, started: Started) -> AsyncIterator[AssistantEvent]:
        """Run the turn and hand out its events as they happen; the last is `done` or `error`.

        The turn runs in its own task and is stored when it ends, whether or not anyone is
        still reading.
        """
        queue: asyncio.Queue[AssistantEvent | None] = asyncio.Queue()
        self._spawn(started, queue.put_nowait)
        while (event := await queue.get()) is not None:
            yield event

    async def idle(self) -> None:
        """Wait until no turn runs, including one a finishing turn starts."""
        while self._turns:
            _ = await asyncio.wait(set(self._turns))

    def _spawn(self, started: Started, deliver: Deliver | None) -> None:
        task = asyncio.create_task(self._run(started, deliver))
        self._turns.add(task)
        task.add_done_callback(self._turns.discard)

    async def _run(self, started: Started, deliver: Deliver | None) -> None:
        """Run the turn to its end; `deliver` is None when the API runs it with nobody streaming."""
        events: list[TurnEvent] = []

        def stream(event: AssistantEvent | None) -> None:
            if deliver is not None:
                deliver(event)

        def on_text(text: str) -> None:
            event = TextEvent(type="text", text=text)
            events.append(event)
            stream(event)

        async def emit(event: TurnEvent) -> None:
            events.append(event)
            stream(event)
            await self._show(started, merged(events))

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
        watched = deliver is not None
        try:
            reply = await self._model.reply(history, shopping.tools(), on_text)
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
            shown = await self._stopped(
                started, shopping.cart_id, history, events, REFUSED, watched=watched
            )
            stream(ErrorEvent(type="error", detail=shown))
        except Exception:
            # The task is the last line of defence: an error here would otherwise vanish with
            # it and leave the conversation marked busy.
            logger.exception(
                "assistant_turn_failed", conversation_id=str(started.conversation_id)
            )
            shown = await self._stopped(
                started, shopping.cart_id, history, events, FAILED, watched=watched
            )
            stream(ErrorEvent(type="error", detail=shown))
        else:
            waiting = awaited(finished.events)
            if waiting is not None:
                await self.continue_after(waiting)
            stream(
                DoneEvent(
                    type="done",
                    turn=finished,
                    cart_id=shopping.cart_id,
                    awaiting=waiting,
                )
            )
        finally:
            stream(None)

    async def _take_answered(self, mandate_id: UUID) -> Started | None:
        """The conversation waiting on the mandate, taken for a turn that carries on from its answer.

        None while the mandate is unanswered, or when no conversation waits on it.
        """
        # Read before the conversation is locked: a read that finds the mandate expired tells
        # the listeners, which take the conversation themselves.
        mandate = await self._mandates.get(mandate_id)
        if mandate.status == "pending":
            return None
        request = ANSWERED[mandate.status].model_copy(update={"mandate_id": mandate.id})
        signer = f", signed by {mandate.signed_by}" if mandate.signed_by else ""
        prompt = (
            f"[The shopper answered mandate {mandate.id} on the consent page: it is now "
            f"{mandate.status}{signer}. Continue from here.]"
        )
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(ConversationRecord)
                .where(ConversationRecord.awaiting_mandate_id == mandate_id)
                .with_for_update()
            )
            if record is None:
                return None
            return self._take(
                session, record, await self._clock(session), request, prompt
            )

    def _take(
        self,
        session: AsyncSession,
        record: ConversationRecord,
        now: datetime,
        request: TurnRequest,
        prompt: str,
    ) -> Started:
        """Mark the locked conversation busy with a new turn and log what started it."""
        record.busy_since = now
        record.awaiting_mandate_id = None
        record.live = LiveTurn(request=request, events=[]).model_dump(mode="json")
        self._activities.add(
            session,
            "shopper_message",
            agent_id=self._agent_id,
            conversation_id=record.id,
            request_kind=request.kind,
            text=request.text,
        )
        return Started(
            conversation_id=record.id,
            wallet_id=record.wallet_id,
            cart_id=record.cart_id,
            history=list(record.history),
            request=request,
            prompt=prompt,
            lease=now,
        )

    async def _show(self, started: Started, events: list[TurnEvent]) -> None:
        """Save the running turn as far as it got, unless another turn took the conversation over."""
        live = LiveTurn(request=started.request, events=events)
        async with self._sessions.begin() as session:
            _ = await session.execute(
                update(ConversationRecord)
                .where(
                    ConversationRecord.id == started.conversation_id,
                    ConversationRecord.busy_since == started.lease,
                )
                .values(live=live.model_dump(mode="json"))
            )

    async def _stopped(
        self,
        started: Started,
        cart_id: UUID | None,
        history: list[JsonValue],
        events: list[TurnEvent],
        failure: str,
        *,
        watched: bool,
    ) -> str:
        """End a turn that failed; what the shopper is shown.

        A turn that ran a tool may have filled the cart or paid, so it is stored as far as it
        got, and the model's history notes what happened, so neither the shopper's screen nor
        the model's memory loses a purchase. A turn nobody streams is stored even when it did
        nothing, ending in the words the shopper is shown, since no response is there to show
        them. Otherwise the conversation is only freed, and the shopper sends the message again.
        """
        steps = steps_taken(events)
        if watched and not steps:
            await self._release(started.conversation_id)
            return failure
        shown = STOPPED if steps else HALTED
        before = (
            f"Before it stopped: {'; '.join(f'{step.tool}: {step.summary}' for step in steps)}."
            if steps
            else "Nothing was done before it stopped."
        )
        note: JsonValue = {
            "role": "assistant",
            "content": f"[My answer stopped partway because of an error. {before} I tell the shopper what happened and carry on from there.]",
        }
        stored = merged(events)
        if not watched:
            stored.append(TextEvent(type="text", text=shown))
        # The usage of a failed run is not known; the turn records none.
        _ = await self._finish(started, cart_id, [*history, note], stored, Usage())
        return shown

    async def _finish(
        self,
        started: Started,
        cart_id: UUID | None,
        history: list[JsonValue],
        events: list[TurnEvent],
        usage: Usage,
    ) -> Turn:
        async with self._sessions.begin() as session:
            record = await session.get_one(
                ConversationRecord, started.conversation_id, with_for_update=True
            )
            record.history = history
            record.cart_id = cart_id
            record.busy_since = None
            record.live = None
            record.awaiting_mandate_id = awaited(events)
            # Counted under the conversation's lock, so a turn that outlived its lease and the
            # one that took over never claim the same place.
            taken = await session.scalar(
                select(func.count())
                .select_from(TurnRecord)
                .where(TurnRecord.conversation_id == started.conversation_id)
            )
            stored = TurnRecord(
                id=uuid7(),
                conversation_id=started.conversation_id,
                position=(taken or 0) + 1,
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
            record.live = None

    @staticmethod
    async def _clock(session: AsyncSession) -> datetime:
        now = await session.scalar(select(func.now()))
        if now is None:
            raise RuntimeError("the database returned no clock")
        return now

    @staticmethod
    async def _own_cart(session: AsyncSession, cart_id: UUID, wallet_id: UUID) -> None:
        cart = await session.get(CartRecord, cart_id)
        if cart is None or cart.wallet_id != wallet_id:
            raise ForeignCart(cart_id)

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
