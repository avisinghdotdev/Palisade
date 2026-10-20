FROM ghcr.io/astral-sh/uv:0.13.2-python3.14-alpine AS deps
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
CMD ["/app/.venv/bin/uvicorn", "palisade_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "api/src"]

FROM source AS build
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --frozen --no-dev --no-editable --package palisade-api

FROM python:3.14.8-alpine AS serve
RUN adduser -D -H -u 10001 api
COPY --from=build /app/.venv /app/.venv
USER 10001
CMD ["/app/.venv/bin/uvicorn", "palisade_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
