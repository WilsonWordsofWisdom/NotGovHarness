# Memory Harness — Design

**Status:** design drafted, not yet built.
**Date:** 2026-09-08
**Wave:** 3 (Runtime & Policy)
**Branch:** `feat/wave3-memory`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-059..D-062) ·
[../../implementation-plan.md](../../implementation-plan.md) ·
[2026-09-01-llm-gateway-harness-design.md](2026-09-01-llm-gateway-harness-design.md) (the LLM
surface this harness calls into)

## Context

Memory (Mem0) was blocked on LLM Gateway since Wave 1 — extraction of durable facts from
conversation history needs an LLM call. LLM Gateway is now built and verified (D-055..D-058), so
this harness is unblocked.

`mem0ai` was installed and read before designing anything (same discipline as D-050/D-051),
surfacing three real findings (D-059..D-061): Mem0's telemetry defaults to enabled but is a clean
env-var gate, not a Guardrails-style trap; pgvector is a first-class backend, so this harness
reuses the shared Postgres rather than adding Qdrant; FastEmbed (local ONNX, in-container) avoids
depending on the host's Ollama having an embedding-capable model, which — unlike its chat
model — was never confirmed to exist, and Ollama's own uptime already proved unreliable across
this session.

**A fourth, structural finding**: this is the first harness where a platform service needs to
call *another platform service* as an authenticated caller, rather than an external caller
hitting a harness (D-030's pattern) or a façade wrapping an external OSS tool's own auth (D-043's
pattern). `mem0-service` needs a real bearer token to call `llm-gateway`'s
`POST /chat/completions`. Checked identity-service's token TTL before assuming a one-time fetch
would work: `access_token_ttl_seconds = 300` — five minutes. A token fetched once at startup
would silently start failing after five minutes. This needs the same fetch-cache-refresh shape
`ContextForgeClient` already uses (D-046), applied to a genuinely new direction (service → service
via this platform's own identity, not service → external tool).

## Goal & success criteria

A caller adds a memory from a real conversation; Mem0 genuinely extracts a durable fact via a
real LLM call through `llm-gateway`, embeds it via FastEmbed, and stores it in pgvector; a later
search retrieves it by semantic relevance, not just exact text match.

Done when, live against the running stack:
1. A real bearer token (scope `memory:write`) adds a memory from a multi-turn conversation, and
   the response reflects a genuinely *extracted* fact (not the raw input echoed back) — proving
   the LLM extraction step actually ran through `llm-gateway`, not a passthrough.
2. `mem0-service`'s own token to `llm-gateway` is fetched and refreshed correctly — confirmed by
   forcing a wait past the 5-minute TTL and observing a subsequent call still succeed (the
   refresh path is exercised for real, not just present in the code).
3. A semantic search (different wording than the stored fact) still retrieves it — proving
   FastEmbed's embeddings and pgvector's similarity search are both real, not a keyword match in
   disguise.
4. The extension and Mem0's own tables are visible directly in Postgres — `\dx` shows `vector`
   installed on the `memory` database; Mem0's own table(s) exist and hold the row.
5. Zero outbound calls to PostHog or GitHub's notices config appear in the container's logs
   across a batch of operations — the same style of check as D-051's telemetry fix, applied here.
6. A caller without `memory:write` can still read (`GET`) but is rejected on `POST`/`DELETE`.

## Non-goals (YAGNI)

- Memory decay/temporal features, Mem0's "scale threshold" tooling, or anything gated behind the
  notices system this harness disables — those are paid-platform-adjacent features this
  self-hosted OSS build isn't using.
- Multi-tenant memory isolation beyond Mem0's own `user_id`/`agent_id` partitioning — no
  additional access-control layer on top for v1.
- A UI — bearer-token API only, matching every harness except Skill Registry and Approvals/HITL,
  which had a specific human-in-the-loop reason for one.

## Components

- **Shared Postgres image swap**: `postgres:16` → `pgvector/pgvector:pg16` (D-060) — confirmed
  real, pullable, drop-in. Every existing service is unaffected (the extension isn't enabled
  anywhere until a database explicitly runs `CREATE EXTENSION vector`).
- **`services/mem0-service/`** — new greenfield service, Postgres `memory` DB (+ `memory_test`),
  one Alembic migration whose only job is `CREATE EXTENSION IF NOT EXISTS vector` — no
  application-owned tables; Mem0's own pgvector backend manages its schema in the same database.
- **`LlmGatewayClient`** — a small fetch-cache-refresh token client for calling `llm-gateway`,
  same shape as `ContextForgeClient` (D-046): fetches a client-credentials token from
  identity-service, caches it, refreshes proactively before the 5-minute TTL expires (not just
  reactively on 401 — a 300-second TTL is short enough that reactive-only retry would fail
  requests unnecessarily often).
- **`mem0-service`'s own OAuth2 client** — registered in identity-service (new, not
  `example-service` reused) with scope `llm_gateway:call` — this is a platform service calling
  another platform service, not a simulated future external caller, so it gets its own identity
  rather than borrowing D-030's pattern.
- **example-service** — gains scope `memory:write` (D-030 pattern, back to the usual shape — it's
  still standing in for the not-yet-built Agent Runtime as *this* harness's external caller).

## API (mem0-service)

- `POST /memories` (scope `memory:write`) — `{user_id, messages: [{role, content}, ...]}` →
  `Memory.add(messages, user_id=...)` via FastEmbed + pgvector + `llm-gateway`, run in a
  threadpool (Mem0's core is synchronous, same pattern as `platform_core.objectstore`).
- `GET /memories?user_id=...&query=...` (any authenticated caller) — semantic search if `query`
  given, else all memories for that `user_id`.
- `DELETE /memories/{memory_id}` (scope `memory:write`).

## Storage

Postgres `memory`: pgvector extension enabled via migration; Mem0's own backend creates and owns
its table(s) — this service's Alembic migrations never touch Mem0's schema directly, mirroring
how ContextForge/Temporal manage their own schemas against a database this platform provisions
but doesn't own the tables of.

## Build order (dependency-ordered)

1. Swap the shared Postgres image to `pgvector/pgvector:pg16`; verify every existing service
   still starts cleanly against it (a base-image change, however drop-in it claims to be, gets
   verified against the whole stack, not just Memory).
2. `services/mem0-service/` scaffold + the extension-only migration.
3. identity-service migrations: new `mem0-service` OAuth2 client (`llm_gateway:call`);
   `memory:write` → example-service.
4. `LlmGatewayClient` (fetch-cache-refresh) — unit-tested against a stub identity-service the
   same way `ContextForgeClient` was (D-046's stub-server pattern).
5. `mem0_layer.py`: Mem0 `Memory` instance wired to pgvector + FastEmbed + the `openai` provider
   pointed at `llm-gateway` via `LlmGatewayClient`-supplied tokens, `MEM0_TELEMETRY=false` set
   before Mem0 is ever imported.
6. `main.py`: the three endpoints, hybrid `auth_mode` + `require_scope`.
7. Live verification: all 6 done-when criteria against the running stack, including the
   TTL-refresh proof (criterion 2) and the semantic-search proof (criterion 3).
8. Committed `pytest`: `LlmGatewayClient` tested against a stub (no live dependency); live-stack
   test for the full add→search→delete flow.

## Testing strategy

- `LlmGatewayClient` tested against a stub identity-service server (real HTTP, fake responses) —
  same shape as `test_contextforge_client.py` — proves the refresh logic without needing a live
  300-second wait in the normal test run.
- Live-stack test: real identity-service tokens (both `memory:write` and the internal
  `mem0-service` client), real `mem0-service`, real `llm-gateway`, real Ollama, real pgvector —
  add a fact, search for it with different wording, confirm retrieval.
- The TTL-refresh *live* proof (done-when criterion 2) is a manual verification step during
  build, not a committed automated test — waiting out a real 5-minute TTL isn't a normal pytest
  run's job; the stub-based unit test is what's committed and runs every time.

## Risks / watch-items

*(updated live with real findings during build)*

- The Postgres image swap is the riskiest single step here — it changes shared infrastructure
  every other harness depends on. Verified against the full stack before considered done, not
  just Memory's own tests.
- `LlmGatewayClient`'s refresh timing (proactive vs reactive) needs to actually be exercised, not
  just coded — a 300-second TTL is short enough that this will get real exercise quickly during
  any live testing session, unlike a longer-lived token that might hide a bug for a while.
- FastEmbed's model download (`BAAI/bge-small-en-v1.5`) happens on first use — pre-warmed at
  container startup via a lifespan hook, same lesson as Sandbox's image pull and Guardrails' spaCy
  model, not left to the first live request.
