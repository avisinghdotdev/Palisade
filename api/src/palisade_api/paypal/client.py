"""Authenticated requests to the PayPal sandbox REST API."""

import asyncio
from dataclasses import dataclass
from typing import final

import httpx2
import structlog
from pydantic import BaseModel, JsonValue, ValidationError

from palisade_api.paypal.auth import PayPalAuth

# PayPal asks for one repeat of a call that timed out or answered 5xx, with the same request id,
# and for a later retry of a 409; the repeat follows after this pause.
RETRY_PAUSE_SECONDS = 1.0
RETRIED_STATUSES = frozenset({409, 429, 500, 502, 503, 504})

logger = structlog.stdlib.get_logger(__name__)


class ErrorDetail(BaseModel):
    issue: str
    description: str = ""
    field: str = ""


class ErrorBody(BaseModel):
    """The shape of a PayPal error response; `details[].issue` carries the business reason."""

    name: str
    message: str = ""
    debug_id: str = ""
    details: list[ErrorDetail] = []


@dataclass(frozen=True)
class PayPalResponse:
    status_code: int
    debug_id: str | None
    body: dict[str, JsonValue]


class PayPalError(Exception):
    """An answer PayPal refused, with the issue codes its body names."""

    def __init__(
        self, status_code: int, debug_id: str | None, error: ErrorBody
    ) -> None:
        issues = ", ".join(detail.issue for detail in error.details) or error.name
        super().__init__(
            f"PayPal answered {status_code} {issues} (debug id {debug_id})"
        )
        self.status_code: int = status_code
        self.debug_id: str | None = debug_id
        self.error: ErrorBody = error

    @property
    def issues(self) -> list[str]:
        return [detail.issue for detail in self.error.details] or [self.error.name]


def _error_body(response: httpx2.Response) -> ErrorBody:
    try:
        return ErrorBody.model_validate_json(response.content)
    except ValidationError:
        return ErrorBody(name=f"HTTP_{response.status_code}", message=response.text)


@final
class PayPalClient:
    def __init__(self, http: httpx2.AsyncClient, auth: PayPalAuth) -> None:
        self._http = http
        self._auth = auth

    async def post(
        self, path: str, body: dict[str, JsonValue], *, request_id: str
    ) -> PayPalResponse:
        """POST a JSON body under `request_id`, repeating once when PayPal's rules allow it.

        Raises:
            PayPalError: PayPal refused the request, after the repeat where one applies.
        """
        headers = {"PayPal-Request-Id": request_id, "Prefer": "return=representation"}
        response = await self._send("POST", path, json=body, headers=headers)
        if response is None or response.status_code in RETRIED_STATUSES:
            await asyncio.sleep(RETRY_PAUSE_SECONDS)
            response = await self._send("POST", path, json=body, headers=headers)
            if response is None:
                raise PayPalError(
                    504,
                    None,
                    ErrorBody(name="TIMEOUT", message=f"POST {path} timed out twice"),
                )
        return self._result(response)

    async def get(self, path: str) -> PayPalResponse:
        response = await self._send("GET", path, json=None, headers={})
        if response is None:
            raise PayPalError(
                504, None, ErrorBody(name="TIMEOUT", message=f"GET {path} timed out")
            )
        return self._result(response)

    async def post_json(self, path: str, body: bytes) -> httpx2.Response:
        """POST a JSON document given as bytes, so the caller controls its exact wire form."""
        token = await self._auth.token()
        response = await self._http.post(
            path,
            content=body,
            headers={
                "Authorization": f"Bearer {token.value}",
                "Content-Type": "application/json",
            },
        )
        self._bind_debug_id(response)
        return response

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, JsonValue] | None,
        headers: dict[str, str],
    ) -> httpx2.Response | None:
        """One attempt; None when the request timed out."""
        token = await self._auth.token()
        try:
            response = await self._http.request(
                method,
                path,
                json=json,
                headers={"Authorization": f"Bearer {token.value}", **headers},
            )
        except httpx2.TimeoutException:
            logger.warning("paypal_timeout", method=method, path=path)
            return None
        self._bind_debug_id(response)
        return response

    def _result(self, response: httpx2.Response) -> PayPalResponse:
        debug_id = response.headers.get("paypal-debug-id")
        if response.is_error:
            raise PayPalError(response.status_code, debug_id, _error_body(response))
        body: dict[str, JsonValue] = response.json() if response.content else {}
        return PayPalResponse(response.status_code, debug_id, body)

    @staticmethod
    def _bind_debug_id(response: httpx2.Response) -> None:
        # PayPal support asks for this id; bound here, it lands on the request's wide event.
        debug_id = response.headers.get("paypal-debug-id")
        if debug_id is not None:
            structlog.contextvars.bind_contextvars(paypal_debug_id=debug_id)
