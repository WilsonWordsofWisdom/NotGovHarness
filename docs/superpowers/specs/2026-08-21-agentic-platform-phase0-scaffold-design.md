# Phase 0 — Platform Scaffold Design

**Status:** approved design, pre-implementation
**Date:** 2026-08-21
**Scope:** the shared substrate every harness plugs into. **No harness logic is built in
Phase 0** beyond a reference `example-service` that proves the scaffold.

Related: [../../architecture.md](../../architecture.md) · [../../decisions.md](../../decisions.md)

---

## 1. Goal & success criteria

Deliver a runnable platform substrate such that `task up` brings up Traefik, Redpanda,
an OTel Collector, Jaeger, and Postgres, plus a traced, event-emitting `example-service`
reachable through the edge with a generated typed client — and `task new-service`
scaffolds a new service pre-wired to all of the above.

**Done when all are true:**

1. `task up` starts the stack; `example-service` answers `GET /healthz` and `/readyz` through
   Traefik.
2. `example-service` performs a DB read/write (Postgres), **emits and consumes** a Kafka event,
   and the full request → DB → event flow appears as one trace in Jaeger.
3. `task codegen` regenerates `example-service`'s OpenAPI + a typed client under `clients/`.
4. `task test` runs unit + integration tests against **ephemeral** Postgres + Redpanda
   (testcontainers) and passes.
5. `task new-service NAME=foo` scaffolds `services/foo/` from the copier template, wired to
   `platform-core`, and it builds and passes its generated smoke test.
6. Both service **shapes** are demonstrated: `example-service` (greenfield) and a façade
   endpoint wrapping a trivial upstream.

## 2. Non-goals (YAGNI)

No real harness logic; no production auth (stub only); no Kubernetes/cloud; no CI pipeline;
no ClickHouse/Qdrant/MinIO/Temporal/SPIRE containers yet (documented in the engine map,
instantiated when their owning harness is built).

## 3. Monorepo layout

```
NotGovHarness/
├─ pyproject.toml            # uv workspace root (tool config: ruff, pyright, pytest)
├─ uv.lock
├─ Taskfile.yml             # up, down, codegen, test, new-service, migrate, lint
├─ docker-compose.yml        # services + infra, gated by profiles
├─ .env.example
├─ packages/
│  ├─ platform-core/         # the service kit (§4)
│  └─ platform-testing/      # pytest fixtures: ephemeral DB/broker, http client, event probe
├─ services/
│  └─ example-service/       # reference service exercising the whole kit (§9)
├─ clients/                  # generated typed clients, one package per service (§6)
├─ infra/
│  ├─ otel-collector.yaml
│  ├─ traefik/               # dynamic + static config
│  └─ db/                    # per-engine init scripts
├─ tools/codegen/            # OpenAPI export + client generation
├─ templates/service/        # copier template (greenfield + façade variants)
└─ docs/
```

Workspace members: `packages/*`, `services/*`, `clients/*`. Python 3.12+.

## 4. `platform-core` — the service kit

A single importable package that supplies every cross-cutting behavior. Each concern is a
small, independently testable module.

- **`app.py` — app factory.** `create_app(settings, *, lifespan_hooks=...) -> FastAPI`.
  Mounts `/healthz` (liveness) and `/readyz` (readiness: checks DB + broker), OpenAPI docs,
  and installs the error handler + middleware below.
- **`config.py` — settings.** `PlatformSettings(BaseSettings)` base (service name, env, log
  level, DB URL, Kafka brokers, OTLP endpoint, auth mode). Services subclass to add keys.
  12-factor / env-driven; `.env` for local.
- **`logging.py` — structured logs.** `structlog` JSON renderer; a middleware binds
  `request_id` + `trace_id` into the context so every log line correlates to its trace.
- **`errors.py` — error envelope.** Exception handlers render a stable JSON body
  `{ "error": { "code", "message", "detail", "trace_id" } }` for HTTP + validation errors.
- **`otel.py` — tracing.** Configures the OTel SDK and auto-instruments FastAPI, `httpx`,
  SQLAlchemy, and the Kafka client; exports OTLP to the collector. One call from the factory.
- **`db.py` — persistence (engine-agnostic).** A thin `Database` interface + a SQLAlchemy/async
  Postgres implementation. Exposes a request-scoped session dependency and a `migrations` hook
  (Alembic for Postgres). The interface is what lets future services swap engines
  (ClickHouse/Qdrant) without touching consumers. See §7.
- **`events.py` — event backbone.** A typed `EventEnvelope` (§8), a `Producer.publish(event)`,
  and a `consumer(group, topics, handler)` helper with retry + DLQ semantics. Hides Kafka
  specifics behind typed events.
- **`auth.py` — pluggable auth dependency.** A FastAPI dependency `require_identity()` that, in
  `dev` mode, accepts an `X-Service-Identity` header / dev token and yields a `CallerIdentity`.
  This is the **seam** the Agent Identity harness (SPIFFE/SPIRE + OAuth2) replaces later — the
  interface (`CallerIdentity`, `require_identity`) stays stable; only the implementation swaps.
- **`facade.py` — upstream adapter helpers.** Utilities for the façade service shape (§5):
  an `httpx` client factory that propagates trace context + identity to an upstream OSS service,
  and helpers to re-expose upstream responses under the platform error envelope.

## 5. Two service shapes

The kit supports both, because most harnesses integrate existing OSS:

1. **Greenfield service** — owns its data and logic (e.g. Agent Registry, Skill Registry).
2. **Façade / adapter service** — wraps an upstream OSS project (LiteLLM, ContextForge,
   Langfuse, Temporal) behind the platform's OpenAPI contract, adding identity, OTel, events,
   and the standard error envelope. Uses `platform-core.facade`.

`example-service` demonstrates both: a native resource **and** a façade endpoint proxying a
trivial upstream (an in-compose stub) to prove trace + identity propagation across the hop.

## 6. Contracts — OpenAPI-first codegen

Each service is the source of truth for its own contract. `tools/codegen`:

1. imports each service's FastAPI app and dumps `services/<svc>/openapi.json`;
2. generates a typed client into `clients/<svc>_client/` (via `openapi-python-client`).

Services import other services **only** through `clients/*` — never by importing another
service's internals. `task codegen` runs both steps; CI-of-the-future will diff the committed
`openapi.json` to catch contract drift.

## 7. Persistence — db-per-service, engine-agnostic

- **Ownership invariant:** a service touches only its own database.
- **Phase 0 engine:** Postgres (one container, one logical DB per service; init scripts in
  `infra/db/`). Migrations via Alembic, owned per service.
- **Extensibility:** the `platform-core.db.Database` interface is engine-agnostic so later
  services bind their best-fit engine. The **engine map** (Postgres / ClickHouse / pgvector·
  Qdrant / Temporal / SPIRE / MinIO) is documented in `architecture.md`; compose **profiles**
  gate each engine so only what's needed starts. Phase 0 ships only the Postgres profile plus
  commented-out placeholder profiles for the others.

## 8. Event backbone — Redpanda (Kafka API)

- **Broker:** Redpanda in compose (Kafka-compatible, single binary).
- **Envelope:** `EventEnvelope { event_id, type, source, occurred_at, trace_id, data }`.
  `data` is a per-event-type Pydantic model.
- **Topics:** namespaced `platform.<domain>.v<major>` (e.g. `platform.example.v1`).
- **Delivery:** consumer-group helper with bounded retries then dead-letter to
  `<topic>.dlq`. Producers attach the current `trace_id` so events join the originating trace.

## 9. `example-service` (reference) + `copier` template

**`example-service`** exercises every capability and is the copy-paste reference:
- a REST resource with a Postgres table + Alembic migration;
- emits `platform.example.v1` on write and consumes it in a background consumer;
- a **façade endpoint** proxying a trivial in-compose upstream;
- OTel traced end-to-end; OpenAPI published; typed client generated;
- ships the reference test suite (unit + integration + event round-trip).

**`templates/service/`** — a `copier` template driving `task new-service NAME=…
SHAPE=greenfield|facade`, scaffolding a service pre-wired to `platform-core` with health,
config, a sample route, a migration, tests, a `Dockerfile`, and a compose fragment.

## 10. Edge & compose topology

- **Edge:** Traefik as a single entrypoint; services routed by path
  `http://localhost/<service>/…` via labels. One URL surface, closer to real deployments.
- **Compose brings up (Phase 0 profile):** Traefik, Redpanda, OTel Collector, Jaeger,
  Postgres, `example-service`, and the trivial upstream stub.
- **Profiles:** `core` (always), plus `clickhouse`, `objectstore`, `vector`, `temporal`,
  `identity` placeholders for later harnesses.

## 11. Dev workflow (Taskfile)

| Command | Does |
|---|---|
| `task up` / `task down` | start / stop the stack |
| `task codegen` | export OpenAPI + regenerate `clients/*` |
| `task test` | run unit + integration tests (testcontainers) |
| `task new-service NAME=… SHAPE=…` | scaffold a service from the template |
| `task migrate SVC=…` | run a service's Alembic migrations |
| `task lint` | ruff + pyright across the workspace |

## 12. Testing strategy

- **`platform-testing`** provides fixtures spinning **ephemeral** Postgres + Redpanda via
  testcontainers, an HTTP test client, and an **event probe** (asserts an expected event was
  published/consumed).
- **`example-service`** ships the reference suite: unit (handlers/validation), integration
  (DB + event round-trip against real ephemeral infra), and a façade test (trace + identity
  propagate to the upstream). This is the pattern later harnesses copy.
- Gherkin PRD-journey tests live under `docs/tests/` as harnesses are built (per TESTING SOP);
  Phase 0 has no PRD journey of its own beyond "the scaffold runs."

## 13. Risks / watch-items

- **Scope creep into harness logic** — guard the Phase 0 boundary; `example-service` stays a
  demo, not a real harness.
- **OTel auto-instrumentation coverage** — verify the Kafka client is actually instrumented so
  events join traces; if the chosen client lacks an OTel plugin, propagate context manually in
  `events.py`.
- **Façade trace propagation** — ensure `httpx` + upstream carry the traceparent header; this is
  the subtle part and is explicitly tested.
- **Engine-agnostic `db` interface** — keep it minimal (session + migrate) so it doesn't leak
  Postgres assumptions that would block ClickHouse/Qdrant later.
