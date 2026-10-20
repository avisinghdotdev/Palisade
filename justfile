set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# List available recipes
default:
    @just --list

# Sync all dependencies and install the pre-commit git hook (git, just, Node, and Docker Desktop are manual installs)
init: _ensure-uv _ensure-pnpm
    uv sync
    uv run pre-commit install --install-hooks
    pnpm --dir web install

# Empty Docker Desktop: every container, image, volume (named too), custom network, build cache and extra builder
[windows]
docker-wipe:
    @$c = docker ps -aq; if ($c) { docker rm -f -v $c }; \
    docker buildx rm --all-inactive -f; \
    docker buildx prune -af; \
    docker system prune -af --volumes; \
    docker volume prune -af; \
    docker system df

[unix]
docker-wipe:
    @c=$(docker ps -aq); if [ -n "$c" ]; then docker rm -f -v $c; fi; \
    docker buildx rm --all-inactive -f; \
    docker buildx prune -af; \
    docker system prune -af --volumes; \
    docker volume prune -af; \
    docker system df

# Install uv via the official installer when it is not on PATH
[windows]
_ensure-uv:
    @if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { \
        irm https://astral.sh/uv/install.ps1 | iex; \
        [Console]::Error.WriteLine("uv installed - restart your shell if the next step fails") \
    }

# Install pnpm via its official installer when it is not on PATH
[windows]
_ensure-pnpm:
    @if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { \
        iwr https://get.pnpm.io/install.ps1 -UseBasicParsing | iex; \
        [Console]::Error.WriteLine("pnpm installed - restart your shell if the next step fails") \
    }
