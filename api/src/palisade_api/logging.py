"""Structured logging: one wide event per request, with stdlib records rendered the same way."""

import logging
import re
import sys
from time import perf_counter
from typing import Literal, TextIO, cast, final
from uuid import uuid4

import structlog
from fastapi.routing import APIRoute
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.tracebacks import ExceptionDictTransformer
from structlog.typing import EventDict, Processor

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "console"]

SCHEMA_VERSION = 1
FIELD_ORDER = (
    "timestamp",
    "level",
    "logger",
    "event",
    "schema_version",
    "request_id",
    "cf_ray",
    "method",
    "route",
    "path",
    "status_code",
    "duration_ms",
    "outcome",
)
# Ids arrive from the client and from Cloudflare and land on every log line of the request. An
# id outside this shape is replaced, never truncated: two attacker-chosen values must not
# collapse into one id.
ID_SHAPE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# The platform probes this every second, so only a failed answer gets a line.
PROBE_PATH = "/api/health"


def configure_logging(
    level: LogLevel, log_format: LogFormat, stream: TextIO | None = None
) -> None:
    """Send structlog and stdlib records through one handler on `stream`, stdout by default.

    Uvicorn's server records are rerouted to that handler. Its access log is dropped because the
    `api_request` event already covers every request. httpx2's per-call lines and two libraries'
    import-time notes are dropped too.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
    ]
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _order_fields,
            *_renderers(log_format),
        ],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    # httpx2 logs a call before its caller binds the debug id, so the line carries the previous
    # call's id; the PayPal client's own `paypal_call` event replaces it.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    # Import-time notes that the app loaded its tasks and Alembic its plugins.
    for name in ("procrastinate.blueprints", "alembic.runtime.plugins"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _order_fields(_logger: object, _method: str, event_dict: EventDict) -> EventDict:
    """Puts the fixed keys first so every line reads the same; extra fields follow."""
    return {
        **{key: event_dict[key] for key in FIELD_ORDER if key in event_dict},
        **event_dict,
    }


def _renderers(log_format: LogFormat) -> list[Processor]:
    if log_format == "json":
        return [
            # Frame locals would carry request bodies, tokens, and secrets into the logs.
            structlog.processors.ExceptionRenderer(
                ExceptionDictTransformer(show_locals=False)
            ),
            structlog.processors.JSONRenderer(),
        ]
    return [structlog.dev.ConsoleRenderer()]


@final
class RequestLogMiddleware:
    """Pure ASGI middleware that emits one `api_request` event per request, bar passing probes.

    It must be the outermost middleware: Starlette's `BaseHTTPMiddleware` (what
    `@app.middleware("http")` creates) breaks context-variable propagation for anything inside it.
    An unhandled error is logged here with its traceback and re-raised, so Starlette's error
    middleware still answers the 500 and the test client still sees the exception.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = structlog.stdlib.get_logger("palisade_api.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = _request_id(request.headers.get("x-request-id"))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        cf_ray = request.headers.get("cf-ray")
        if cf_ray is not None and ID_SHAPE.match(cf_ray):
            structlog.contextvars.bind_contextvars(cf_ray=cf_ray)
        start = perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                # ASGI messages are untyped mappings; the status is an int by spec.
                status_code = cast("int", message["status"])
                MutableHeaders(scope=message)["x-request-id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            self.logger.exception(
                "api_request", **_event_fields(request, status_code, start, "error")
            )
            raise
        if status_code < 400 and request.url.path == PROBE_PATH:
            return
        self.logger.info(
            "api_request",
            **_event_fields(request, status_code, start, _outcome(status_code)),
        )


def _request_id(candidate: str | None) -> str:
    if candidate is not None and ID_SHAPE.match(candidate):
        return candidate
    return f"req_{uuid4().hex}"


def _outcome(status_code: int) -> str:
    if status_code >= 500:
        return "error"
    if status_code >= 400:
        return "client_error"
    return "success"


def _event_fields(
    request: Request, status_code: int, start: float, outcome: str
) -> dict[str, object]:
    # FastAPI stores the matched route in the scope once routing has happened.
    route = cast("object", request.scope.get("route"))
    return {
        "schema_version": SCHEMA_VERSION,
        "method": request.method,
        "route": route.path if isinstance(route, APIRoute) else None,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": round((perf_counter() - start) * 1000, 2),
        "outcome": outcome,
    }
