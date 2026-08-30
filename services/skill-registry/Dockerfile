# Build skill-registry from the uv workspace.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
COPY services/skill-registry ./services/skill-registry

RUN uv sync --package skill-registry --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "skill_registry.main:app", "--host", "0.0.0.0", "--port", "8000"]
