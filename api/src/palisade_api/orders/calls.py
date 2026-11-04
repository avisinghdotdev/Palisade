"""Requests to PayPal for an order, each recorded before it leaves and again when it is answered."""

from typing import Literal, final
from uuid import UUID, uuid4, uuid7

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders.models import PayPalCallRecord
from palisade_api.paypal.client import (
    Attachment,
    PayPalClient,
    PayPalError,
    PayPalResponse,
)

# The order's own calls, then the three that answer a dispute on it.
CallAction = Literal[
    "create", "capture", "void", "track", "escalate", "provide_evidence", "refund"
]


@final
class PayPalCalls:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], paypal: PayPalClient
    ) -> None:
        self._sessions = sessions
        self._paypal = paypal

    async def once(
        self,
        order_id: UUID,
        action: CallAction,
        path: str,
        body: dict[str, JsonValue],
        *,
        dispute_id: str | None = None,
    ) -> PayPalResponse:
        """The order's one call for `action`: the first sends `body`, a later one repeats it.

        A repeat goes under the first call's `PayPal-Request-Id` with the first call's body, so
        PayPal answers with the first result when it already acted. `dispute_id` names the
        dispute a first call answers.

        Raises:
            PayPalError: PayPal refused the request or did not answer.
        """
        async with self._sessions.begin() as session:
            call = await session.scalar(
                select(PayPalCallRecord)
                .where(
                    PayPalCallRecord.order_id == order_id,
                    PayPalCallRecord.action == action,
                )
                .order_by(PayPalCallRecord.id.desc())
                .limit(1)
            )
            if call is None:
                call = self.add(session, order_id, action, body, dispute_id=dispute_id)
            call_id, request_id, sent = call.id, call.request_id, call.request_body
        return await self.post(call_id, path, sent, request_id)

    async def send(
        self, order_id: UUID, action: CallAction, path: str, body: dict[str, JsonValue]
    ) -> PayPalResponse:
        """A new call under a fresh `PayPal-Request-Id`, for an action an order may repeat.

        Raises:
            PayPalError: PayPal refused the request or did not answer.
        """
        async with self._sessions.begin() as session:
            call = self.add(session, order_id, action, body)
            call_id, request_id = call.id, call.request_id
        return await self.post(call_id, path, body, request_id)

    @staticmethod
    def add(
        session: AsyncSession,
        order_id: UUID,
        action: CallAction,
        body: dict[str, JsonValue],
        *,
        dispute_id: str | None = None,
    ) -> PayPalCallRecord:
        """Record a call about to be made, in the caller's transaction, under a fresh request id.

        `post` sends it; a row the caller needs to reference lands with what references it.
        """
        call = PayPalCallRecord(
            id=uuid7(),
            order_id=order_id,
            dispute_id=dispute_id,
            action=action,
            request_id=str(uuid4()),
            request_body=body,
        )
        session.add(call)
        return call

    async def post(
        self,
        call_id: UUID,
        path: str,
        body: dict[str, JsonValue],
        request_id: str,
        attachment: Attachment | None = None,
    ) -> PayPalResponse:
        """Send a recorded call and keep PayPal's answer on its row, a refusal included.

        Raises:
            PayPalError: PayPal refused the request or did not answer.
        """
        try:
            if attachment is None:
                response = await self._paypal.post(path, body, request_id=request_id)
            else:
                response = await self._paypal.post_multipart(
                    path, body, attachment, request_id=request_id
                )
        except PayPalError as error:
            await self._answered(
                call_id, error.status_code, error.debug_id, error.error.model_dump()
            )
            raise
        await self._answered(
            call_id, response.status_code, response.debug_id, response.body
        )
        return response

    async def _answered(
        self,
        call_id: UUID,
        status_code: int,
        debug_id: str | None,
        response_body: dict[str, JsonValue],
    ) -> None:
        """Keep the latest answer; a repeat overwrites the one before it."""
        async with self._sessions.begin() as session:
            call = await session.get_one(PayPalCallRecord, call_id)
            call.status_code = status_code
            call.debug_id = debug_id
            call.response_body = response_body
            # The database's clock, like every other timestamp, so the API host's clock never skews it.
            call.finished_at = func.now()
