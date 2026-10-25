"""A stand-in for PayPal's REST API behind an httpx mock transport."""

from collections.abc import Callable
from typing import final

import httpx2

Answer = Callable[[httpx2.Request], httpx2.Response]


def json_answer(status_code: int, body: object) -> Answer:
    return lambda _request: httpx2.Response(
        status_code, json=body, headers={"paypal-debug-id": f"debug-{status_code}"}
    )


def timeout(_request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("PayPal did not answer")


def unreachable(_request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("PayPal could not be reached")


@final
class PayPalStub:
    """Answers the token call itself; every other call is matched by `METHOD path`.

    Answers for a path are consumed in order, the last one repeating, so a test can script a
    timeout followed by a success. Each request PayPal would have seen is kept in `requests`.
    """

    def __init__(self) -> None:
        self.answers: dict[str, list[Answer]] = {}
        self.requests: list[httpx2.Request] = []

    def on(self, method: str, path: str, *answers: Answer) -> None:
        self.answers[f"{method} {path}"] = list(answers)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx2.Response(
                200,
                json={
                    "access_token": "sandbox-access-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "",
                    "app_id": "APP-1",
                },
            )
        self.requests.append(request)
        answers = self.answers[f"{request.method} {request.url.path}"]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        return answer(request)

    def sent(self, method: str, path: str) -> list[httpx2.Request]:
        return [
            request
            for request in self.requests
            if request.method == method and request.url.path == path
        ]

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.MockTransport(self.handle),
            base_url="https://api-m.sandbox.paypal.com",
        )
