# Build example-service from the uv workspace.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Workspace metadata + members needed to resolve example-service.
COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
COPY services/example-service ./services/example-service

RUN uv sync --package example-service --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "example_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
