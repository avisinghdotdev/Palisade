"""The API itself as the ASGI app the agent's tools call, in-process and without a socket."""

import asyncio
from collections.abc import Mapping
from typing import final

from starlette.types import ASGIApp, Receive, Scope, Send


@final
class LocalApp:
    """Calls the app the way a server would: with the lifespan state in the scope.

    Each request runs in its own task, so the request log middleware binds its own context
    without touching the context of the turn that made the call.
    """

    def __init__(self, app: ASGIApp, state: Mapping[str, object]) -> None:
        self._app = app
        self._state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def serve() -> None:
            await self._app({**scope, "state": dict(self._state)}, receive, send)

        await asyncio.create_task(serve())
