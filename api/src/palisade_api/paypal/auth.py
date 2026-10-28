"""Client-credentials access tokens for the PayPal sandbox."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Literal, final

import httpx2
import structlog
from pydantic import BaseModel

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
OAUTH_PATH = "/v1/oauth2/token"
REFRESH_MARGIN = timedelta(minutes=5)

# Named for the package: `log_call` serves the client's calls as well as the token's.
logger = structlog.stdlib.get_logger("palisade_api.paypal")


def log_call(method: str, path: str, response: httpx2.Response, started: float) -> None:
    """One `paypal_call` event per answer, with the debug id PayPal support asks for.

    `started` is the `perf_counter()` reading taken just before the request was sent.
    """
    logger.info(
        "paypal_call",
        method=method,
        path=path,
        status_code=response.status_code,
        paypal_debug_id=response.headers.get("paypal-debug-id"),
        duration_ms=round((perf_counter() - started) * 1000, 2),
    )


class TokenUnavailable(Exception):
    """PayPal's token endpoint answered with a server error; another try later may work."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"PayPal's token endpoint answered {status_code}")


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"]
    expires_in: int
    scope: str
    app_id: str


@dataclass(frozen=True)
class AccessToken:
    value: str
    app_id: str
    scopes: tuple[str, ...]
    expires_at: datetime


@final
class PayPalAuth:
    """Fetches an access token and reuses it until shortly before it expires."""

    def __init__(
        self, http: httpx2.AsyncClient, client_id: str, client_secret: str
    ) -> None:
        self._http = http
        self._credentials = httpx2.BasicAuth(client_id, client_secret)
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()

    async def token(self) -> AccessToken:
        async with self._lock:
            if (
                self._token is None
                or self._token.expires_at - REFRESH_MARGIN <= datetime.now(UTC)
            ):
                self._token = await self._fetch()
            return self._token

    async def _fetch(self) -> AccessToken:
        started = perf_counter()
        response = await self._http.post(
            OAUTH_PATH,
            auth=self._credentials,
            data={"grant_type": "client_credentials"},
        )
        log_call("POST", OAUTH_PATH, response, started)
        if response.is_server_error:
            raise TokenUnavailable(response.status_code)
        # A 4xx means the credentials are wrong: a setup fault that must surface, not be retried.
        response.raise_for_status()
        body = TokenResponse.model_validate_json(response.content)
        return AccessToken(
            value=body.access_token,
            app_id=body.app_id,
            scopes=tuple(body.scope.split()),
            expires_at=datetime.now(UTC) + timedelta(seconds=body.expires_in),
        )
