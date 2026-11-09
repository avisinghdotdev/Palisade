FROM ghcr.io/astral-sh/uv:0.13.7-python3.14-alpine AS deps
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY api/pyproject.toml api/
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --frozen --no-dev --no-install-workspace --package palisade-api

FROM deps AS source
COPY api api

FROM source AS dev-server
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --package palisade-api
# The same start as the served image; Compose restarts the container when the source changes.
CMD ["/app/.venv/bin/python", "-m", "palisade_api.serve"]

FROM source AS build
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --frozen --no-dev --no-editable --package palisade-api

# alpine3.23 matches the uv image above, so both stages share one libc and OpenSSL.
FROM python:3.14.10-alpine3.23 AS serve
RUN adduser -D -H -u 10001 api
COPY --from=build /app/.venv /app/.venv
USER 10001
CMD ["/app/.venv/bin/python", "-m", "palisade_api.serve"]
