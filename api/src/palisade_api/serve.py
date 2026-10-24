"""The API process: logging is configured before uvicorn writes its first line, then the server runs."""

import uvicorn

from palisade_api.logging import configure_logging
from palisade_api.settings import LogSettings

# The container's published port is the only way in, so the server listens on every interface.
HOST = "0.0.0.0"  # noqa: S104
PORT = 8000


def main() -> None:
    # pydantic-settings fills the fields from the environment; pyright only sees the synthesized constructor.
    settings = LogSettings()  # pyright: ignore[reportCallIssue]
    configure_logging(settings.log_level, settings.log_format)
    # Without a log config uvicorn adds no handlers of its own, so its lines reach the ones above.
    uvicorn.run("palisade_api.main:app", host=HOST, port=PORT, log_config=None)


if __name__ == "__main__":
    main()
