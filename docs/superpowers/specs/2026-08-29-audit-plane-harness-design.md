# Audit Plane Harness — Design

**Status:** built and verified — all 6 done-when criteria confirmed live: a real widget created
through example-service's HTTP API flowed through Redpanda into the hash-chained log, and a row
tampered with directly in Postgres (bypassing every service) was caught by `/audit/verify` at
exactly that row. Not yet merged to `main`.
**Date:** 2026-08-29
**Wave:** 1 (Foundation) · fourth of four
**Branch:** `feat/wave1-audit-plane`

Related: [../../architecture.md](../../architecture.md) · [../../decisions.md](../../decisions.md)
(D-016) · [../../implementation-plan.md](../../implementation-plan.md)

## Context

D-016: "observability alone isn't a compliance record." Langfuse (Observability harness) captures
*what an LLM call looked like* for debugging/eval; Audit captures *what happened, by whom, when* as
a tamper-evident record — a different property (integrity under tampering) that a trace store
doesn't provide. This is a **greenfield consumer**: it doesn't wrap an upstream OSS project, it
just listens to the event backbone (`platform_core.events`, built in Phase 0) and hash-chains what
it hears.

**Approach:** append-only Postgres table, each row's hash covering the previous row's hash plus its
own canonical content — the same integrity idea as a git commit chain or a blockchain, applied to
compliance records rather than code or currency. Any historical edit breaks the chain from that
point forward, detectably.

## Goal & success criteria

Consume platform events into a tamper-evident audit log, and prove tampering is detectable.

**Done when:**
1. `audit-service` consumes `platform.example.v1` (the one real event-producing topic today) via
   `platform_core.events.Consumer`, appending one hash-chained row per event.
2. Each row's hash = `SHA-256(prev_hash + canonical_json(event_id, type, source, occurred_at,
   trace_id, data))`; the first row chains from a fixed genesis hash.
3. `GET /audit/records` — paginated, chronological read of the log.
4. `GET /audit/verify` — walks the whole chain, recomputing hashes; returns whether it's intact and,
   if not, the first broken row.
5. A test that corrupts a historical row's `data` directly in Postgres (bypassing the service
   entirely) and confirms `/audit/verify` catches it — the actual property being built, not just
   "rows got inserted."
6. Unit tests (infra-free: hash-chain math) + integration tests (skip-if-down: real Kafka →
   Postgres) green; `task lint` clean.

## Non-goals (YAGNI)

MinIO WORM storage (D-016 calls it "optional"; Postgres hash-chaining alone already gives
tamper-evidence — a second storage layer would be redundant for this reference); consuming topics
that don't exist yet (only `platform.example.v1` is real; the pattern extends to more topics by
adding to a list, not a redesign); multi-instance/HA consumption (a single `audit-service` instance
processes its consumer group serially — the natural way hash-chaining stays race-free without
locking — so this explicitly doesn't run more than one replica); redaction/retention policy; a UI.

## Components

- **`audit-service`** — greenfield FastAPI (façade-free, like `identity-service`). One background
  `Consumer` (lifespan-managed, existing pattern) appending to the chain; two read endpoints.
  Postgres `audit` DB (db-per-service).

## Hash chain

```
row.hash = SHA256(row.prev_hash + canonical_json({
    event_id, type, source, occurred_at (isoformat), trace_id, data
}))
```

`canonical_json`: `sort_keys=True, separators=(",", ":")` — deterministic regardless of dict
ordering, so re-verification recomputes the identical hash. Genesis: `prev_hash = "0" * 64` for the
first row. Single-writer (the Consumer's own sequential dispatch loop — no concurrent handlers)
means no `SELECT ... FOR UPDATE` locking is needed to read-then-write the previous row safely.

## API

- `GET /audit/records?limit=&cursor=` — chronological (oldest first), cursor-paginated.
- `GET /audit/verify` — `{"valid": bool, "checked": N, "broken_at": <row id> | null}`. Recomputes
  every row's hash from its stored content and compares against the stored hash *and* the next
  row's `prev_hash` — either mismatch means tampering.

## Storage

- **`audit` Postgres DB** — one `audit_log` table: `id` (bigserial, PK — also the chain order),
  `event_id`, `type`, `source`, `occurred_at`, `trace_id`, `data` (jsonb), `prev_hash` (char 64),
  `hash` (char 64), `consumed_at`. Alembic migration per the existing Phase 0 pattern.

## Build order (dependency-ordered)

1. **Hash-chain core** — pure functions: canonicalize an event, compute a row's hash, verify one
   link. *Verify (unit, infra-free):* known input/output vectors; tamper-one-byte breaks
   verification.
2. **`audit-service` scaffold + migration** — `audit_log` table, Alembic migration, DB model.
   *Verify:* migration applies cleanly (mirrors `identity-service`'s pattern).
3. **Consumer wiring** — subscribe to `platform.example.v1`, append a hash-chained row per event.
   *Verify (unit):* a stub event list produces the expected chain.
4. **Read API** — `/audit/records`, `/audit/verify`. *Verify (unit):* against a seeded in-memory
   chain.
5. **Compose integration + live tampering test** — run against real Postgres + Redpanda; publish a
   real widget-created event, confirm it's chained; directly corrupt a row's `data` in Postgres and
   confirm `/audit/verify` reports `valid: false` at that row. *Verify:* skip-if-down, matching the
   existing pattern; this step is the harness's actual point, not a formality.

## Testing strategy

- **Unit (infra-free):** hash-chain math (canonicalization, genesis, tamper detection) against
  fixed vectors — no Kafka/Postgres needed to prove the core property.
- **Integration (skip-if-down):** the full consume → chain → verify → tamper → re-verify loop
  against a live stack, per step 5.

## Risks / watch-items

- **Single-consumer-instance assumption** — documented, not enforced at the infra level (no compose
  guard against scaling replicas). Acceptable for a local reference platform; a production audit
  plane would need either a distributed-safe chaining scheme or a single designated writer role.
- **Clock skew in `occurred_at`** — it's the *producer's* timestamp (set in `EventEnvelope`), not
  when `audit-service` consumed it; `consumed_at` is separate and always monotonic locally, useful
  if the two ever need to be told apart during an investigation.
- **Only one real topic exists yet** — the harness is scoped to `platform.example.v1` today; adding
  a topic is a one-line change to the `Consumer`'s topic list, not a design change.
- **SQLAlchemy's `text()` misparses `:param::type` with no space** — a raw `UPDATE ... SET data =
  :data::jsonb` (used in the live tampering test, to forge a row's content the same way a DB-level
  attacker would) hit a Postgres syntax error; SQLAlchemy's bind-parameter scanner reads
  `:data::jsonb` as part of the parameter name, not a cast. `:data ::jsonb` (a space before the
  cast) fixes it — worth remembering for any future raw-SQL cast in this codebase.
