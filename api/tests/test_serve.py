import logging
from collections.abc import Iterator

import pytest
import structlog
import uvicorn
from pydantic import TypeAdapter

from palisade_api import serve

EVENT = TypeAdapter(dict[str, object])


@pytest.fixture
def reset_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


@pytest.mark.usefixtures("reset_logging")
def test_uvicorns_first_line_is_already_structured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "json")
    started: dict[str, object] = {}

    def run(app: str, **options: object) -> None:
        # What uvicorn logs first, before the app's lifespan runs.
        logging.getLogger("uvicorn.error").info("Started server process [%d]", 7)
        started.update(options, app=app)

    monkeypatch.setattr(uvicorn, "run", run)

    serve.main()

    [line] = capsys.readouterr().out.splitlines()
    event = EVENT.validate_json(line)
    assert (event["logger"], event["event"]) == (
        "uvicorn.error",
        "Started server process [7]",
    )
    assert started == {
        "app": "palisade_api.main:app",
        "host": serve.HOST,
        "port": serve.PORT,
        "log_config": None,
    }
