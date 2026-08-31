# Temporal (Shared Infra) — Design

**Status:** step 1 (compose integration) built and verified live — schema setup, server, Web UI,
and a real `temporalio` Python SDK workflow (worker + client + activity) all confirmed against
the actual running stack. Steps 2-3 not yet built.
**Date:** 2026-09-01
**Wave:** 3 (Runtime & Policy) · shared infra, not itself one of the 16 harnesses
**Branch:** `feat/wave3-temporal`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-009, D-015) ·
[../../implementation-plan.md](../../implementation-plan.md)

## Context

Implementation plan: "Temporal (shared infra for Approvals + Deployment — stand up once here).
Temporal server + its Postgres; `temporalio`." Unlike every other Wave 3 item, Temporal isn't one
of the 16 harnesses — it's infrastructure two future harnesses build on: Approvals/HITL (Wave 3,
D-015: "approve/edit/reject gates... Mechanism: Temporal signals") and Deployment Pipeline (Wave
4, D-009: "Container packaging + Temporal for durable orchestration"). Same relationship Postgres/
Redpanda/MinIO already have to the services that use them — stood up once, proven with a real
workflow, then consumed by name later.

**Researched against current docs** (the old `temporalio/docker-compose` GitHub repo is archived
as of Jan 2026; examples moved to `temporalio/samples-server`): two viable local deployment
modes exist — the Temporal CLI's embedded dev server (`temporal server start-dev`, in-memory or
SQLite-file, Web UI baked in, single container) or the full multi-container server backed by a
real database (`docker-compose-postgres.yml`'s pattern: `temporalio/auto-setup` + a separate
`temporalio/ui` container).

**Decision: full Postgres-backed server, not `start-dev`.** Matches this platform's own stated
posture (D-001: "working reference services... not scaffolds"; D-004: per-service best-fit
engine, but Postgres is genuinely Temporal's own best fit here) and the db-per-service pattern
every other harness already follows — a new `temporal` database on the shared Postgres container,
not a separate ephemeral/in-memory store that would be the only piece of this platform that
doesn't survive a restart. The added complexity is small: one more container (`temporalio/ui`)
beyond what `start-dev` would need, in exchange for consistency with everything else already
built.

## Goal & success criteria

Stand up Temporal, and prove the `temporalio` Python SDK genuinely works against it — not just
that the container starts.

**Done when:**
1. Temporal server runs in compose (`temporalio/auto-setup`, single-binary-per-container but all
   roles in one process — matches the reference `docker-compose-postgres.yml` pattern), backed
   by a new `temporal` Postgres database (db-per-service, shared container).
2. Temporal Web UI (`temporalio/ui`) runs alongside it, reachable on its own port.
3. A demo workflow — one activity that calls Skill Registry's `GET /skills` (same "prove it
   against something real" discipline as `mcp-skills-demo` wrapping Skill Registry for Agent
   Gateway, not an isolated toy) — actually executes: a worker polls a task queue, a client
   starts the workflow, the workflow completes and returns real Skill Registry data.
4. The workflow's execution is visible in the Temporal Web UI (proving the persistence layer,
   not just the RPC round trip).
5. Unit/integration tests (skip-if-down: real Temporal server) green; `task lint` clean.

## Non-goals (YAGNI)

**Approvals/HITL itself** — that's a separate, not-yet-built harness that will actually use
Temporal signals for approve/edit/reject gates; this only proves the infra it depends on works.
**Deployment Pipeline** — Wave 4, same relationship. **A persistent worker service** — unlike
`mcp-skills-demo` (which had to keep running so ContextForge could call it anytime), nothing
calls this demo workflow on an ongoing basis; the worker runs for the duration of a test/demo
script, not as a compose service of its own. **Elasticsearch / advanced visibility** — the
reference compose configs offer a Postgres+Elasticsearch variant for richer search; standard
visibility (list/filter by basic attributes) is all this reference platform needs.
**Multi-role/scaled deployment** — the reference `docker-compose-multirole.yaml` splits frontend/
history/matching/worker into separate containers for production-scale tuning; the single
auto-setup container (all roles, one process) is the right fit here, same reasoning as every
other "don't scale what doesn't need scaling yet" call already made in this platform.

## Components

- **`temporal`** (integrated, not built) — `temporalio/auto-setup`, Postgres-backed
  (`temporal` DB, shared container). Never called directly by other services yet; only the demo
  workflow/worker (and later, Approvals/HITL) talk to it.
- **`temporal-ui`** (integrated) — `temporalio/ui`, for visually confirming workflow execution.
- **Demo workflow + worker** — lives in `packages/platform-testing` or a small dedicated location
  (not a persistent compose service, per Non-goals) — a workflow with one activity calling Skill
  Registry's `GET /skills`.

## Build order (dependency-ordered)

1. **Compose integration** — `temporal` Postgres DB + auto-setup container + `temporal-ui`.
   *Verify:* container healthy, UI reachable, matches the exact env-var shape the reference
   compose config uses (verified empirically against the real image before trusting it, same
   discipline as ContextForge's D-045/D-047 findings — Temporal's own env-var/schema-setup
   surface is exactly the kind of thing worth confirming live, not assumed from docs).
2. **Demo workflow + activity + worker** — the Skill-Registry-calling workflow described above.
   *Verify (unit, infra-free):* the activity function itself, called directly (no Temporal
   runtime needed to prove the HTTP call logic is right).
3. **Live workflow execution test** — skip-if-down: connect a real client to the running
   Temporal server, run a real worker, execute the workflow, confirm the result is real Skill
   Registry data, confirm the execution shows up via Temporal's own API (list/describe
   workflow) — not just trusting the client call succeeded.

## Testing strategy

- **Unit (infra-free):** the activity's HTTP-calling logic in isolation.
- **Integration (skip-if-down):** the full worker-executes-a-real-workflow loop against a real
  Temporal server, per step 3.

## Risks / watch-items

- **The dynamic-config file must exist at `DYNAMIC_CONFIG_FILE_PATH` or the server refuses to
  start at all** (found live: `unable to validate dynamic config: ... no such file or
  directory`, before the server even gets to opening a database connection). The image ships no
  default — `infra/temporal/dynamic_config.yaml` is mounted in, empty but present, which is a
  complete valid config for this reference deployment (no overrides needed yet).
- **Temporal splits persistence into two separate databases, `temporal` and
  `temporal_visibility`**, each with its own schema directory in the image
  (`/etc/temporal/schema/postgresql/v12/{temporal,visibility}`) — confirmed by actually applying
  both schemas and watching the server boot, not assumed from the plan's single-line "Temporal
  server + its Postgres." Not a db-per-service choice this platform made; Temporal's own
  architecture.
- **`temporal-sql-tool`'s env vars are different from the server's own env vars** (found live: an
  initial attempt using the server's `DB`/`DB_PORT`/`POSTGRES_SEEDS`/etc. against the raw CLI
  tool tried to connect to MySQL's default port 3306 instead of Postgres — those vars are
  consumed by the *server's* entrypoint script, not the bare `temporal-sql-tool` binary, which
  wants its own `--plugin`/`--endpoint`/`--user`/`--password`/`--database` flags instead).
- **A workflow's defining module must have no side-effecting top-level code** (found live:
  `RuntimeError: Failed validating workflow ...` from `asyncio.run() cannot be called from a
  running event loop`, because the Python SDK's sandbox *re-imports* the module a workflow class
  is defined in, to validate determinism — a single-file script with `asyncio.run(main())` at
  module scope breaks under that reimport). Workflow/activity definitions belong in their own
  module, separate from whatever client/worker driver code calls `asyncio.run()` — not a style
  preference, a real requirement given how the sandbox works.
