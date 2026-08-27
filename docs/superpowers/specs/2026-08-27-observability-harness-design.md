# Observability Harness — Design

**Status:** approved design, pre-implementation
**Date:** 2026-08-27
**Wave:** 1 (Foundation) · built out of the plan's stated order (Identity, then LLM Gateway,
Observability, Audit) — see note in Context.
**Branch:** `feat/wave1-observability`

Related: [../../architecture.md](../../architecture.md) · [../../decisions.md](../../decisions.md)
(D-007) · [../../implementation-plan.md](../../implementation-plan.md)

## Context

Phase 0 already established the tracing baseline: every service exports OTLP to a shared
`otel-collector`, which fans out to Jaeger (D-007, "Observability baseline from day one"). The
Observability harness does not replace that baseline — it adds a second export target,
**Langfuse**, an LLM-specific trace store that later harnesses depend on: Evals (Wave 4) pulls
traces from it to judge agent behavior, and Audit's hash-chained log (Wave 1) is a *different*
compliance record, not a substitute for LLM-trace observability.

**Ordering note:** the plan's stated Wave 1 order is Identity → LLM Gateway → Observability →
Audit, with Identity called out as build-first ("the seam every other service authenticates
through"). This harness is being built ahead of Identity by explicit choice. That's viable because
Observability has no hard dependency on Identity — it consumes the existing OTel baseline, not
`platform-core`'s auth layer — but it does mean traces won't carry `spiffe_id`/`act`-chain
attributes until Identity lands later; that's expected, not a gap to work around here.

**Approach:** self-host Langfuse v3 (ClickHouse-backed; the version the docs already named,
`decisions.md` line 60) as a **façade-free infra addition** — no new application code. The
integration point is entirely at the `otel-collector` config: add a second OTLP/HTTP exporter
alongside the existing `otlp/jaeger` one. Services keep exporting to one collector endpoint exactly
as they do today (`platform_core.otel`); they gain nothing to configure and nothing to import.

## Goal & success criteria

Stand up Langfuse as a second, LLM-aware home for the traces the platform already produces, with
zero manual UI steps to reach a working state from `task up`.

**Done when:**
1. `docker compose --profile observability up` (pulling in `clickhouse` + `objectstore` profiles)
   brings up ClickHouse, MinIO, Redis, `langfuse-web`, `langfuse-worker`, all healthy.
2. Langfuse auto-provisions a default org/project and a fixed dev API key pair via
   `LANGFUSE_INIT_*` env vars on first boot — no manual sign-up/UI step required for local dev.
3. `otel-collector` fans out the existing traces pipeline to Langfuse (`otlphttp/langfuse`
   exporter, HTTP Basic Auth built from the seeded key pair) **in addition to** the existing Jaeger
   exporter — verified by diffing `infra/otel-collector.yaml`, not by adding app code.
4. A demo call through `example-service` → `upstream-stub` (the same `/proxy` hop Phase 0 uses)
   produces a trace visible in **both** Jaeger and Langfuse — checked against Langfuse's public API
   (`GET /api/public/traces`), not just eyeballed in the UI, so it's scriptable/testable.
5. New Postgres `langfuse` DB provisioned per the existing db-per-service pattern
   (`infra/db/init/01-create-databases.sql`) for Langfuse's own metadata — separate from
   ClickHouse, which holds the trace/analytics data.
6. An integration test (skip-if-down, matching the existing pattern) hits Langfuse's public API and
   asserts at least one ingested trace exists after exercising the demo hop; `task lint` clean.

## Non-goals (YAGNI)

Langfuse's prompt management/versioning features; Langfuse's own Evaluations/scoring UI (that's
Wave 4's Evals runner — this harness only supplies raw traces for it to pull later); SSO/OIDC login
for the Langfuse UI (fixed dev admin credentials via `LANGFUSE_INIT_USER_*`, same insecure-dev
posture as Traefik's dashboard); manual `langfuse` Python SDK instrumentation in service code
(OTel-only, per D-007 — services don't gain a new dependency); ClickHouse cluster mode / production
sizing; alerting; ClickHouse or ingestion data retention policy.

## Components

- **`clickhouse`** — analytics store for Langfuse's trace/observation data. New `clickhouse`
  compose profile (reusable — the plan already names it separately from `observability`, since
  later harnesses may share it).
- **`minio`** — S3-compatible blob storage (Langfuse media/exports). New `objectstore` profile,
  same reasoning — Wave 2 registries also want MinIO buckets later.
- **`redis`** (Valkey-compatible) — cache + background-job queue for `langfuse-worker`.
- **`langfuse-web`** — UI + public API, port 3000 behind Traefik (`/observability` prefix, dev-only
  unauthenticated dashboard access, mirroring the Traefik-dashboard posture already in the repo).
- **`langfuse-worker`** — async ingestion pipeline (OTLP → ClickHouse), internal-only.

All five live under a new **`observability`** compose profile pattern: `clickhouse` +
`objectstore` are their own profiles (composed together), `langfuse-web`/`worker`/`redis` are the
`observability` profile proper.

## `otel-collector` integration (the actual integration point)

Langfuse ingests via **OTLP/HTTP** (protobuf), not gRPC, at `/api/public/otel/v1/traces`, and
authenticates with HTTP Basic Auth from a public/secret API-key pair — so the collector config
change has an ordering dependency: the seeded `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` /
`_SECRET_KEY` values must exist before the collector's exporter config can reference them (both
come from the same `.env`, so this is a same-file consistency requirement, not a runtime race).

```yaml
exporters:
  otlp/jaeger: { ... }               # unchanged
  otlphttp/langfuse:
    endpoint: http://langfuse-web:3000/api/public/otel
    encoding: proto
    headers:
      Authorization: "Basic ${LANGFUSE_BASIC_AUTH_B64}"  # base64(public_key:secret_key)
service:
  pipelines:
    traces:
      exporters: [otlp/jaeger, otlphttp/langfuse, debug]
```

## Storage

- **`langfuse` Postgres DB** — Langfuse's own metadata (projects, users, API keys); provisioned in
  `infra/db/init/01-create-databases.sql` per the existing db-per-service pattern.
- **ClickHouse** — trace/observation analytics data; its own engine, not shared Postgres (matches
  the "Postgres everywhere does not hold" note in `decisions.md`).
- **MinIO** — blob storage for media/exports; its own volume.

## Secrets (dev-only, generated not committed)

`NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY` (Langfuse-web), ClickHouse password, MinIO
access/secret key, Redis password, and the seeded `LANGFUSE_INIT_*` org/project/API-key values —
all `.env`-driven with `CHANGEME`-style placeholders, consistent with the existing dev-only
posture (Traefik's insecure dashboard, `platform`/`platform` Postgres creds).

## Build order (dependency-ordered)

1. **`clickhouse` + `objectstore` profiles** — bring up ClickHouse and MinIO standalone, verify
   healthy. *Verify:* container healthchecks green, no app wiring yet.
2. **`observability` profile** — `redis`, `langfuse-web`, `langfuse-worker`, `langfuse` Postgres DB,
   `LANGFUSE_INIT_*` seeding. *Verify:* Langfuse UI reachable, seeded API key pair confirmed via
   `GET /api/public/projects`.
3. **`otel-collector` fan-out** — add the `otlphttp/langfuse` exporter. *Verify:* re-run the Phase 0
   demo hop (`/example/proxy`), confirm the same trace appears in both Jaeger and Langfuse.
4. **Skip-if-down integration test** — script/test that exercises the demo hop and asserts via
   Langfuse's public API that the trace landed. *Verify:* green locally; skips cleanly when the
   `observability` profile isn't up.

## Testing strategy

- **No unit tests needed** — this harness adds no application code (no `platform-core` change), so
  there's nothing to unit-test in isolation.
- **Integration (skip-if-down):** the demo hop plus a Langfuse public-API assertion, matching the
  existing skip-if-down pattern for live-stack checks.

## Risks / watch-items

- **Resource footprint** — Langfuse v3's recommended minimums (ClickHouse 8 GiB, web/worker 4 GiB
  each, Postgres 4 GiB, Redis 1.5 GiB, MinIO 4 GiB) sit on top of the existing Phase 0 stack;
  confirmed workable after raising the local Docker Desktop memory allocation to 31 GiB.
- **Self-hosted OTLP endpoint bug** — an upstream issue
  (langfuse/langfuse#9900) reports the `/api/public/otel/v1/traces` endpoint hanging indefinitely
  on a recent release. Pin to a specific, tested Langfuse image tag (not `:latest`); verify the
  OTLP path works during step 3 before relying on it, and have the Langfuse SDK's own OTLP-adjacent
  ingestion documented as a fallback if the collector path is broken on the pinned version.
- **ClickHouse/Postgres timezone** — both must run in UTC; non-UTC silently returns empty query
  results rather than erroring, so this is easy to miss.
- **`LANGFUSE_INIT_*` ordering** — org must exist before project, project before API key; Docker
  Compose env var quoting matters (no double-quotes) per Langfuse's own docs.
- **No identity context yet** — traces won't carry `spiffe_id`/`act` until the Identity harness
  lands; Langfuse dashboards will show anonymous/dev-mode callers until then.
