"""Pre-commit entry: lint every changed Bicep file and compile every changed parameter file."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Parameter files read these at compile time; any value proves they resolve.
PLACEHOLDER_ENVIRONMENT = {"IMAGE_TAG": "0", "REVISION_SUFFIX": "check"}


def main(paths: list[str]) -> int:
    az = shutil.which("az")
    if az is None:
        raise FileNotFoundError("az CLI not found on PATH")
    environment = {**os.environ, **PLACEHOLDER_ENVIRONMENT}
    for path in map(Path, paths):
        command = (
            ["build-params", "--file", str(path), "--stdout"]
            if path.suffix == ".bicepparam"
            else ["lint", "--file", str(path)]
        )
        result = subprocess.run(
            [az, "bicep", *command],
            check=False,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0 or "Warning" in result.stderr:
            sys.stderr.write(result.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
