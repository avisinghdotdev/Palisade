"""Requests to PayPal for an order, each recorded before it leaves and again when it is answered."""

from typing import Literal, final
from uuid import UUID, uuid4, uuid7

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders.models import PayPalCallRecord
from palisade_api.paypal.client import PayPalClient, PayPalError, PayPalResponse

CallAction = Literal["create", "capture", "void", "track"]


@final
class PayPalCalls:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], paypal: PayPalClient
    ) -> None:
        self._sessions = sessions
        self._paypal = paypal

    async def once(
        self, order_id: UUID, action: CallAction, path: str, body: dict[str, JsonValue]
    ) -> PayPalResponse:
        """The order's one call for `action`: the first sends `body`, a later one repeats it.

        A repeat goes under the first call's `PayPal-Request-Id` with the first call's body, so
        PayPal answers with the first result when it already acted.

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
                call = PayPalCallRecord(
                    id=uuid7(),
                    order_id=order_id,
                    action=action,
                    request_id=str(uuid4()),
                    request_body=body,
                )
                session.add(call)
            call_id, request_id, sent = call.id, call.request_id, call.request_body
        return await self._post(call_id, path, sent, request_id)

    async def send(
        self, order_id: UUID, action: CallAction, path: str, body: dict[str, JsonValue]
    ) -> PayPalResponse:
        """A new call under a fresh `PayPal-Request-Id`, for an action an order may repeat.

        Raises:
            PayPalError: PayPal refused the request or did not answer.
        """
        call_id = uuid7()
        request_id = str(uuid4())
        async with self._sessions.begin() as session:
            session.add(
                PayPalCallRecord(
                    id=call_id,
                    order_id=order_id,
                    action=action,
                    request_id=request_id,
                    request_body=body,
                )
            )
        return await self._post(call_id, path, body, request_id)

    async def _post(
        self, call_id: UUID, path: str, body: dict[str, JsonValue], request_id: str
    ) -> PayPalResponse:
        try:
            response = await self._paypal.post(path, body, request_id=request_id)
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
