# Build deployment-pipeline-service from the uv workspace. One image, two roles (compose
# overrides `command` for the worker) — same shape as approvals-service's own Dockerfile.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
COPY services/deployment-pipeline-service ./services/deployment-pipeline-service

RUN uv sync --package deployment-pipeline-service --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "deployment_pipeline_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
