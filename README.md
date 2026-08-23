# NotGovHarness

A locally-runnable **reference implementation of a complete agentic platform** — 16 "harnesses"
across four layers on a shared substrate. This repo currently contains **Phase 0**: the platform
substrate every harness plugs into, plus an `example-service` that proves it end to end.

See [`docs/architecture.md`](docs/architecture.md), [`docs/decisions.md`](docs/decisions.md), and
[`docs/implementation-plan.md`](docs/implementation-plan.md) for the full design and roadmap.

## Prerequisites

- **Docker** (Docker Desktop). An old Engine (20.10.x) works but needs the `seccomp:unconfined`
  workaround already baked into `docker-compose.yml`; a current Engine is recommended.
- **[uv](https://docs.astral.sh/uv/)** and **[go-task](https://taskfile.dev/)** on your `PATH`
  (both install to `~/.local/bin` — add it to `PATH` if needed).

```bash
uv sync --all-packages   # create the workspace venv (Python 3.12, pinned)
```

## Quickstart

```bash
task up                        # start the core stack (Traefik, Postgres, Redpanda, OTel, Jaeger,
                               # example-service, upstream-stub)
task migrate SVC=example-service   # apply the example-service schema (Alembic)

curl localhost/example/healthz                     # -> {"status":"ok"}   (via Traefik)
curl -X POST localhost/example/widgets \
     -H 'content-type: application/json' -d '{"name":"gizmo"}'
curl localhost/example/proxy -H 'x-service-identity: me'   # façade -> upstream-stub

task down                      # stop the stack
```

Local UIs: **Jaeger** http://localhost:16686 · **Traefik** http://localhost:8080

## Common tasks

| Command | Does |
|---|---|
| `task up` / `task down` | start / stop the `core` stack |
| `task test` | run unit + integration tests (integration hits the live stack; skips if down) |
| `task lint` / `task fmt` | ruff + pyright · auto-format |
| `task codegen` | export each service's OpenAPI + regenerate typed clients |
| `task new-service NAME=foo-service` | scaffold a new service from the copier template |
| `task migrate SVC=…` | run a service's Alembic migrations |

## Layout

```
packages/platform-core     # the service kit (config, logging, errors, otel, db, events, auth, facade, app)
packages/platform-testing  # shared pytest fixtures (endpoint-driven)
services/example-service   # reference service: DB + events + façade endpoint (both shapes)
services/upstream-stub     # trivial upstream for the façade demo
tools/codegen              # OpenAPI -> typed client generator
templates/service          # copier template for `task new-service`
infra/                     # Traefik, OTel Collector, Postgres init
clients/                   # generated typed clients (reproducible via `task codegen`)
```

## Phase 0 success criteria (all met)

1. **Runs behind the edge** — `healthz`/`readyz` answer through Traefik.
2. **One end-to-end trace** — a `POST /widgets` produces a single Jaeger trace spanning
   request → Postgres `INSERT` → `widget.created` → consumer.
3. **OpenAPI-first codegen** — `task codegen` regenerates a typed client.
4. **Real integration tests** — `task test` runs against ephemeral/live infra.
5. **`task new-service`** scaffolds a service that builds and passes its smoke test.
6. **Façade shape** — `/example/proxy` forwards to `upstream-stub` with identity + trace context
   crossing the hop (one trace over two services).
