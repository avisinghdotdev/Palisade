"""Authenticated requests to the PayPal sandbox REST API."""

import asyncio
import json
from dataclasses import dataclass
from time import perf_counter
from typing import final

import httpx2
import structlog
from pydantic import BaseModel, JsonValue, ValidationError

from palisade_api.paypal.auth import PayPalAuth, TokenUnavailable, log_call

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


@dataclass(frozen=True)
class Attachment:
    """A file sent beside the JSON part of a multipart request."""

    name: str
    content: bytes
    content_type: str


# The parts of a multipart request as PayPal's dispute calls take them: the JSON under `input`,
# and on provide-evidence the file under `evidence-file`; accepted by the sandbox on 2026-10-20
# and 2026-11-01. Send-message takes the `input` part alone (2026-11-08).
MultipartFiles = dict[str, tuple[str | None, bytes, str]]


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

    @property
    def outcome_unknown(self) -> bool:
        """Whether PayPal may still have done what was asked.

        A 5xx or no answer says nothing about the outcome. A 409 that lasts past the repeat means
        PayPal was still working on the first request: it may finish it.
        """
        return self.status_code >= 500 or self.status_code == 409


def no_answer(message: str) -> ErrorBody:
    """The error body of a call PayPal never answered; its callers treat it as a 5xx."""
    return ErrorBody(name="NO_ANSWER", message=message)


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
        return await self._post_once_repeated(
            path, json=body, files=None, headers=headers
        )

    async def post_multipart(
        self,
        path: str,
        body: dict[str, JsonValue],
        attachment: Attachment | None,
        *,
        request_id: str,
    ) -> PayPalResponse:
        """POST `body` as the JSON part, with `attachment` beside it if any, under `request_id`.

        Repeats once as `post` does. PayPal does not document the request id on its multipart
        calls, so a caller that must not file twice checks PayPal's own record first.

        Raises:
            PayPalError: PayPal refused the request, after the repeat where one applies.
        """
        files: MultipartFiles = {
            "input": (None, json.dumps(body).encode(), "application/json")
        }
        if attachment is not None:
            files["evidence-file"] = (
                attachment.name,
                attachment.content,
                attachment.content_type,
            )
        return await self._post_once_repeated(
            path, json=None, files=files, headers={"PayPal-Request-Id": request_id}
        )

    async def get(self, path: str) -> PayPalResponse:
        response = await self._send("GET", path, json=None, files=None, headers={})
        if response is None:
            raise PayPalError(504, None, no_answer(f"GET {path} got no answer"))
        return self._result(response)

    async def _post_once_repeated(
        self,
        path: str,
        *,
        json: dict[str, JsonValue] | None,
        files: MultipartFiles | None,
        headers: dict[str, str],
    ) -> PayPalResponse:
        response = await self._send(
            "POST", path, json=json, files=files, headers=headers
        )
        if response is None or response.status_code in RETRIED_STATUSES:
            await asyncio.sleep(RETRY_PAUSE_SECONDS)
            response = await self._send(
                "POST", path, json=json, files=files, headers=headers
            )
            if response is None:
                raise PayPalError(
                    504, None, no_answer(f"POST {path} got no answer twice")
                )
        return self._result(response)

    async def post_json(self, path: str, body: bytes) -> httpx2.Response:
        """POST a JSON document given as bytes, so the caller controls its exact wire form."""
        token = await self._auth.token()
        started = perf_counter()
        response = await self._http.post(
            path,
            content=body,
            headers={
                "Authorization": f"Bearer {token.value}",
                "Content-Type": "application/json",
            },
        )
        self._record("POST", path, response, started)
        return response

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, JsonValue] | None,
        files: MultipartFiles | None,
        headers: dict[str, str],
    ) -> httpx2.Response | None:
        """One attempt; None when PayPal could not be reached or did not answer in time.

        The token call is inside the attempt: a network failure or a 5xx there is the same outage.
        """
        try:
            token = await self._auth.token()
            started = perf_counter()
            response = await self._http.request(
                method,
                path,
                json=json,
                files=files,
                headers={"Authorization": f"Bearer {token.value}", **headers},
            )
        except (httpx2.TransportError, TokenUnavailable) as error:
            logger.warning(
                "paypal_unreachable",
                method=method,
                path=path,
                error=type(error).__name__,
            )
            return None
        self._record(method, path, response, started)
        return response

    def _result(self, response: httpx2.Response) -> PayPalResponse:
        debug_id = response.headers.get("paypal-debug-id")
        if response.is_error:
            raise PayPalError(response.status_code, debug_id, _error_body(response))
        body: dict[str, JsonValue] = response.json() if response.content else {}
        return PayPalResponse(response.status_code, debug_id, body)

    @staticmethod
    def _record(
        method: str, path: str, response: httpx2.Response, started: float
    ) -> None:
        log_call(method, path, response, started)
        # Bound as well, the last call's id lands on the request's wide event.
        debug_id = response.headers.get("paypal-debug-id")
        if debug_id is not None:
            structlog.contextvars.bind_contextvars(paypal_debug_id=debug_id)
