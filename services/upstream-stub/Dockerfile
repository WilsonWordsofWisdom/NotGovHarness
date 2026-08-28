# Build upstream-stub from the uv workspace.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
COPY services/upstream-stub ./services/upstream-stub

RUN uv sync --package upstream-stub --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# Not a plain `uvicorn ...` CLI invocation — run.py picks mTLS vs plain HTTP at startup depending
# on whether SPIRE is available (the `identity` profile). See run.py's module docstring.
CMD ["python", "-m", "upstream_stub.run"]
