# Implementation Plan

Comprehensive, executable plan derived from the approved design. **Part A** is the Phase 0
scaffold in build-ready detail; **Part B** is the dependency-ordered roadmap for all 16 harnesses.

Related: [superpowers/specs/2026-08-21-agentic-platform-phase0-scaffold-design.md](superpowers/specs/2026-08-21-agentic-platform-phase0-scaffold-design.md)
· [decisions.md](decisions.md) · [architecture.md](architecture.md)

## Context

An approved Phase 0 scaffold spec, a decisions log (D-001…D-017), and an architecture covering
16 harnesses across 4 layers on a shared substrate. This plan defines the exact
libraries/dependencies, the backend infra + databases, and the build order derived from
dependencies. Phase 0 is the concrete gate before any harness is built.

## Status (updated 2026-08-27)

**Phase 0 is COMPLETE and merged to `main`** (PRs #2 + #3). All six success criteria verified.

**Wave 1 progress:** the **Observability** harness (Langfuse v4) is built and verified — see
[superpowers/specs/2026-08-27-observability-harness-design.md](superpowers/specs/2026-08-27-observability-harness-design.md)
and decisions D-021..D-023. Built ahead of Identity (the plan's stated build-first harness) by
explicit choice; see that spec's Context section for why this ordering is safe. Remaining Wave 1
harnesses: Identity (design spec written, D-018..D-020, not yet built), LLM Gateway, Audit.

| Area | State |
|---|---|
| Workspace foundation (uv, Taskfile, ruff/pyright/pytest, `.env.example`) | ✅ merged |
| `platform-core`: context, config, logging, errors, auth-stub, otel, app factory | ✅ merged |
| `platform-core`: **db** (async SQLAlchemy), **events** (aiokafka + retry/DLQ + trace headers) | ✅ merged |
| `platform-core`: **facade** (UpstreamClient, identity+trace propagation, error mapping) | ✅ merged |
| `platform-testing` endpoint-driven fixtures (db, kafka, event_probe) | ✅ merged |
| Infra `core` compose (Traefik, Postgres, Redpanda dual-listener, OTel Collector, Jaeger) | ✅ merged |
| **example-service** (greenfield + façade, Alembic, DB + events, Traefik, OTEL) | ✅ merged |
| **upstream-stub** (façade demo target) | ✅ merged |
| codegen (`task codegen`) + typed client · copier template (`task new-service`) · README | ✅ merged |

**Success criteria (all met):** 1 edge health/ready via Traefik · 2 one end-to-end Jaeger trace
(request→DB→event→consume) · 3 OpenAPI codegen → importable client · 4 integration tests
(**25 pass**) · 5 `task new-service` scaffolds a building service · 6 façade cross-hop trace +
identity. `task lint` clean (ruff + pyright, 0 errors).

## Environment prerequisites & learnings (from the build)

- **Python 3.12 pinned** (`.python-version`). The host Python is 3.14, which lacks wheels for
  some deps; `uv` fetches a managed 3.12. Sync the whole workspace with **`uv sync --all-packages`**
  (plain `uv sync` installs only the root + dev group, not the members).
- **Docker Engine is old (20.10.x)** and its seccomp profile blocks syscalls Postgres 16 and
  Redpanda/seastar need → both get `security_opt: [seccomp:unconfined]` in compose as a local-dev
  workaround. **Update:** Docker Desktop has since been upgraded (4.88.1, Engine 29.7.2) — the
  `seccomp:unconfined` overrides are left in place (never verified as removable) but ClickHouse,
  MinIO, Redis, and Langfuse all came up clean on the new engine with no seccomp workaround needed.
- **Raising Docker Desktop's memory allocation resets its VM** — every running container is lost
  (not just stopped), including volumes' container state; the `core` stack had to be brought back
  up from scratch after bumping memory from ~8 GiB to 31 GiB for the Observability harness.
- **Langfuse v4 defaults to `events_only` mode** — the legacy `GET /api/public/traces` /
  `GET /api/public/observations` (v1) endpoints 404 under it. Use the **Observations API v2**
  (`GET /api/public/v2/observations`) instead; see the Observability harness spec's risks section.
- **OTel + Kafka:** the community `opentelemetry-instrumentation-aiokafka` was **not** adopted;
  `events.py` instead injects/extracts W3C `traceparent` via Kafka **headers** (the plan's
  documented fallback), so a consumer span links into the producing trace. FastAPI, httpx, and
  SQLAlchemy use their standard instrumentors.
- **Redpanda topic auto-create is not relied upon** — producers/tests create topics explicitly
  (via `AIOKafkaAdminClient`).
- **Redpanda needs two listeners** — `external://localhost:9092` for host tools and
  `internal://redpanda:29092` for other containers (a Kafka client follows the *advertised*
  address, so containers must be given a name they can resolve).
- **App containers also need `seccomp:unconfined`** on old Docker — OTel resource detection spawns
  threads and the old profile blocks thread creation (`can't start new thread`). This is now three
  services on the workaround; **it strengthens the case to upgrade Docker before Wave 1.**
- **pyright scope** must include `services/` and `tools/` (it originally covered only `packages/`);
  generated `clients/` are excluded from ruff + pyright.
- **`gh` CLI is not installed** — PRs are opened via the GitHub REST API + the stored git
  credential. `brew reinstall gh` would restore the normal flow.

---

## PART A — Phase 0 scaffold (executable detail)

### A1. Dependencies (as actually pinned)

**Root dev:** `uv`, `go-task`, `ruff`, `pyright`, `pytest`, `pytest-asyncio`, `pytest-cov`,
`copier`, `openapi-python-client`.

**`platform-core`:** `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `structlog`,
`httpx`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `aiokafka`, `opentelemetry-sdk`,
`opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-fastapi` / `-httpx` / `-sqlalchemy`,
`orjson`. *(No `-aiokafka` instrumentation — manual header propagation instead.)*

**`platform-testing`:** `testcontainers`, `asgi-lifespan`, `httpx`, `pytest`, `pytest-asyncio`
(+ workspace `platform-core`). *Note:* testcontainers is declared but not yet wired — see A3.4.

### A2. Backend infra & databases (docker-compose, `core` profile) — ✅ built

Traefik (`v3.1`), Postgres (`16`, db-per-service; `example_service` DB created on init), Redpanda
(`v24.2.7`), OTel Collector (`0.109.0`) → Jaeger (`1.60`). Postgres + Redpanda carry
`seccomp:unconfined` (see learnings). Compose profiles: `core` now; `identity` / `clickhouse` /
`objectstore` / `vector` / `temporal` reserved for Part B.

### A3. Build order (all steps ✅ complete — merged via PRs #2/#3; retained for reference)

1. ✅ **Workspace foundation.** *Verified:* `uv sync --all-packages`; `task lint`.
2. ✅ **Infra compose (`core`).** *Verified:* all five containers healthy; Jaeger/Traefik 200.
3. 🔶 **`platform-core`** — `context, config, logging, errors, otel, db, events, auth` ✅;
   **`facade`** ◻ remaining. *Verified so far:* 18 tests, pyright clean.
4. ◻ **`platform-testing`** — fixtures. **Revised:** provide **endpoint-driven** fixtures
   (`platform_database`, `platform_kafka_brokers`, `event_probe`) reading env with localhost
   defaults, plus a reachability skip — matching how `platform-core` already tests against the
   live stack. **Testcontainers (ephemeral) is deferred until Docker is upgraded** (old engine +
   seccomp makes testcontainer Postgres/Redpanda unreliable). *Verify:* a smoke test that
   consumes an event via the probe.
5. ◻ **`example-service` (greenfield)** — the linchpin. Sub-steps, in order:
   a. Alembic scaffolding under `services/example-service/migrations/` + implement `task migrate
      SVC=…`; one table + migration.
   b. `create_app` with a REST resource, `db` session dependency, `readiness_checks=[db.check]`,
      lifespan hooks for `db` + event `Producer`/`Consumer`.
   c. Emit `platform.example.v1` on write; a background consumer handles it.
   d. Dockerfile + compose service (with `OTEL_EXPORTER_OTLP_ENDPOINT` → collector) + Traefik
      labels (`/example`).
   *Verify:* criteria 1–2 — healthz/readyz via Traefik; DB r/w; event round-trip; **one Jaeger
   trace spanning request → DB span → produced event → consumer span**.
6. ◻ **`tools/codegen` + `clients/`** — export `openapi.json`, generate the typed client; add
   `services/*` and `clients/*` to the uv workspace globs. *Verify:* criterion 3.
7. ◻ **Façade endpoint + `upstream-stub`** — needs the `facade` module (step 3). Add an
   instrumented `upstream-stub` service; a façade route proxying it; assert `traceparent` +
   identity propagate across the hop. *Verify:* criterion 6.
8. ◻ **`copier` template + `task new-service`** — parametrized from example-service
   (`SHAPE=greenfield|facade`). *Verify:* criterion 5.
9. ◻ **Full E2E + run docs** — `README` with `task up`/`down`, the seccomp/Docker note, and the
   success-criteria checklist. *Verify:* all 6 criteria green.

**Immediate next order & why:** `facade` (3) → `platform-testing` (4) → `example-service` (5).
`facade` finishes the kit and unblocks step 7. `platform-testing` gives `example-service` clean
fixtures. `example-service` is prioritised as the linchpin: it is the first real consumer of
`db` + `events` + `otel` together and the only thing that proves criteria 1–2 (the end-to-end
trace) — every later harness is copied from it, so a flaw here is cheapest to fix now. codegen (6),
façade endpoint (7), copier (8) then fall out quickly because their dependencies are satisfied.

### A4. Phase 0 verification (end-to-end)

`task up`; then the six criteria: healthz/readyz through Traefik → DB read/write → Kafka
emit+consume → **one Jaeger trace** request→DB→event → `task codegen` regenerates a client →
`task test` passes (integration tests run against the live stack, **skip** if it is down) →
`task new-service` scaffolds a building service → façade endpoint shows trace+identity crossing to
`upstream-stub`.

---

## Wave 1 readiness (go / no-go)

**Verdict: GO — no structural changes to the roadmap.** Phase 0 proved the patterns Wave 1 relies
on: the façade shape (LLM Gateway = LiteLLM, Observability = Langfuse both wrap upstreams behind
our contract), db-per-service, the event backbone (Audit consumes it), and the auth seam that
Identity replaces. Ordering stands: **Identity first**, then LLM Gateway, Observability, Audit.

Two concrete prerequisites before starting:

1. **Upgrade Docker Engine (blocking-ish).** Phase 0 already needed `seccomp:unconfined` on three
   containers, and thread creation was blocked on the old engine. Wave 1 adds SPIRE, ClickHouse,
   Redis, and MinIO (Langfuse alone is several containers) — the workaround will not carry all of
   them. Upgrading is the single highest-leverage step before Wave 1.
2. **Provide an LLM provider key (or a mock) for the LLM Gateway.** LiteLLM needs at least one
   real provider (`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`, via env/virtual keys) to be useful; decide
   whether to wire a real key or a mock/local model for the reference. Not needed for Identity.

Nothing else in Wave 1 changes. Each harness still gets its own spec → plan → build cycle.

## PART B — Full 16-harness build roadmap (dependency-ordered)

> **Prerequisite:** upgrade Docker Engine before starting Wave 1 (see learnings). Wave 1 pulls in
> SPIRE, ClickHouse, Redis, and MinIO — the seccomp workaround does not scale to all of them.

**Ordering principle:** stand up cross-cutting foundations first (identity, model access,
telemetry, audit), then the catalogs, then the runtime dependencies an agent needs, then the
lifecycle orchestration that composes everything, with Evals last (it gates deploys). Each
harness gets its **own spec → plan → build cycle**; below is the sequence and the
infra/DB/library delta each introduces.

### Wave 1 — Foundation (unlocks everything; replaces the Phase 0 stubs)
- **Agent Identity** (SPIFFE/SPIRE + OAuth2). *Infra:* SPIRE server + agent (`identity` profile);
  Postgres `identity`. *Libs:* `spiffe`/`py-spiffe`, `authlib`, `cryptography`. *Effect:* replaces
  the `platform-core.auth` dev stub with real SVIDs + scoped tokens; the trust root that signs
  Agent Cards. **Build first — it is the seam every other service authenticates through.**
- **LLM Gateway** (LiteLLM, façade). *Infra:* LiteLLM container; Postgres `litellm`. *Libs:*
  `litellm[proxy]`. *Effect:* the single OpenAI-format model surface many later harnesses call.
- **Observability** (Langfuse, façade). *Infra:* Langfuse + ClickHouse + Redis + MinIO. *Libs:*
  `langfuse`. *Effect:* Collector also exports to Langfuse; builds on the existing trace baseline.
- **Audit plane** (greenfield consumer). *Infra:* Postgres `audit` (hash-chained). *Libs:*
  `platform-core.events` + `cryptography`. *Effect:* tamper-evident compliance log off Kafka.

### Wave 2 — Catalog & Registries (need Identity for signing)
- **Agent Registry** (A2A cards). Postgres `agent_registry`; `a2a-sdk`, `cryptography`.
- **Skill Registry** (`SKILL.md`). Postgres `skill_registry` + MinIO; `pyyaml`, `minio`.
- **Eval Registry**. Postgres `eval_registry` + MinIO; pydantic, `minio`.

### Wave 3 — Runtime & Policy (an agent's dependencies)
- **Temporal** (shared infra for Approvals + Deployment — stand up once here). Temporal server +
  its Postgres; `temporalio`.
- **Agent Gateway** (ContextForge, façade). ContextForge container; Postgres. Single agent-level
  control point (A2A + MCP + registry federation).
- **Guardrails** (greenfield). Postgres `guardrails`; `llm-guard`, `nemoguardrails`,
  `guardrails-ai`; Llama Guard via LLM Gateway.
- **Memory** (Mem0). Qdrant (`vector` profile) or pgvector; `mem0ai`, `qdrant-client`.
- **Knowledge/RAG** (LlamaIndex). Qdrant/pgvector + MinIO; `llama-index`, a reranker.
- **Approvals/HITL**. Postgres `approvals`; `temporalio` signals; optional `humanlayer`.

### Wave 4 — Agent Lifecycle (composes everything)
- **Agent Builder** (CrewAI). Postgres `builder`; `crewai`. Reads registries + LLM Gateway;
  publishes signed Agent Cards; requests baseline eval config.
- **Deployment Pipeline** (Docker + Temporal). Reuse Temporal; Postgres `deploy`; Docker socket;
  `temporalio`, `docker`. Packages agent → image → run; emits deploy events; runs Evals as a gate.
- **Evals runner** (DeepEval/Promptfoo/Ragas). Postgres `evals`; `deepeval`, `ragas`, `promptfoo`
  (Node). Drives agents via A2A, pulls traces from Observability, judges via LLM Gateway.

### Infra/DB accumulation (compose profiles enabled per wave)
`core` (Phase 0) → `identity` + `clickhouse` + `objectstore` (Wave 1) → (Wave 2 adds MinIO
buckets) → `vector` + `temporal` (Wave 3) → (Wave 4 adds Docker socket). Every new service = one
new logical Postgres DB (db-per-service) unless it brings a specialized engine.

---

## Risks / watch-items
- **Docker Engine age** — 20.10.x forced the seccomp workaround; upgrade before Wave 1 infra.
- **Testcontainers on old Docker** — deferred; live-stack integration tests with skip-if-down are
  the interim strategy (A3.4).
- **Kafka↔OTel context** — handled via manual `traceparent` headers in `events.py` (resolved).
- **Façade trace/identity propagation** — the subtle Phase 0 correctness point; tested against
  `upstream-stub` (step 7).
- **Engine-agnostic `db` interface** — kept minimal (session + check + dispose) so ClickHouse/
  Qdrant fit later without leaking Postgres assumptions.
- **Node dependency for Promptfoo** — the Evals wave introduces a Node toolchain alongside Python.
- **Temporal is a shared dependency** (Approvals + Deployment) — stand it up once, in Wave 3.
- **Scope discipline** — Phase 0 builds no harness logic beyond `example-service`.
