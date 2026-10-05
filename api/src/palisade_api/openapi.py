"""Write the API's OpenAPI document as JSON; the web client is generated from it."""

import json
import sys
from pathlib import Path

from palisade_api.main import app


def main(output: Path) -> None:
    output.write_text(
        json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
