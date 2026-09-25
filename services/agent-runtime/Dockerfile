# Build agent-runtime from the uv workspace. Built once as a shared, generic image (D-087) —
# a specific deployment's role/goal/backstory are injected via environment variables at
# `docker run` time by deployment-pipeline-service, not baked into this image itself.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
COPY services/agent-runtime ./services/agent-runtime

RUN uv sync --package agent-runtime --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "agent_runtime.main:app", "--host", "0.0.0.0", "--port", "8000"]
