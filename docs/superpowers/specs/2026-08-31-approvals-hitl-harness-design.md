# Approvals / HITL Harness — Design

**Status:** built and verified live — all 6 done-when criteria confirmed against the running
stack, including a worker-restart-mid-flight test and the durable-timeout path.
**Date:** 2026-08-31
**Wave:** 3 (Runtime & Policy)
**Branch:** `feat/wave3-approvals-hitl`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-015, D-048, D-049) ·
[../../implementation-plan.md](../../implementation-plan.md) ·
[2026-09-01-temporal-harness-design.md](2026-09-01-temporal-harness-design.md)

## Context

D-015: "approve / edit / reject gates on risky actions and deploys. Mechanism: Temporal signals
(already in stack) as primary; HumanLayer optional." The architecture's data-plane sequence
diagram shows the Agent Runtime calling this harness mid-task: `RT->>HITL: approve risky action?
(Temporal signal)`.

Two things make this harness's build order unusual:

1. **No caller exists yet.** Agent Runtime is Wave 4. Every prior harness that needed a live
   caller either had one already built (Skill Registry called by mcp-skills-demo/agent-gateway)
   or built a minimal stand-in (mcp-skills-demo itself). Here we do the same: reuse
   **example-service** as the risk-taking caller (same simulated-principal pattern as D-030),
   granted a new scope.
2. **This is where Temporal's deferred scope gets paid off.** The Temporal merge (D-048/D-049)
   shipped infra-only, explicitly deferring "a demo workflow that calls a real service" and "a
   committed automated test." Approvals/HITL's own workflow *is* that real workflow — it isn't a
   toy, it's the actual durable-orchestration mechanism D-015 specifies. Building this harness
   correctly closes both gaps at once rather than needing a separate throwaway demo later.

## Goal & success criteria

A caller can request approval for a risky action; a human reviewer can approve, reject, or edit
it; the caller can observe the outcome. The pending/decided state survives a service restart
(Temporal's whole point — the workflow, not the HTTP request, is durable).

Done when, live against the running stack:
1. A real bearer token (example-service's client-credentials, scope `approvals:request`) POSTs an
   approval request → a real Temporal workflow starts (visible running in Temporal UI) → a
   Postgres row is persisted `pending`.
2. A reviewer bearer token (scope `approvals:decide`) POSTs a decision → a real Temporal signal
   is delivered to that running workflow → the workflow completes → the Postgres row updates to
   `approved`/`rejected`/`edited`.
3. The caller polls and observes the terminal state and (for `edited`) the edited payload.
4. An approval nobody ever decides on times out on its own (workflow-driven timer, not a cron) and
   lands in Postgres as `expired` — proving the durable-timer value Temporal actually adds over a
   plain "pending row + human checks a dashboard."
5. Restarting the approvals worker mid-flight (a pending, undecided approval) does not lose it —
   the workflow resumes from Temporal's own history on worker restart, and a decision sent after
   the restart still lands. This is the concrete "why Temporal and not just a Postgres status
   column" proof.
6. A committed `pytest` integration test exercises 1-3 against the live stack (closes D-049's
   test gap) — plus a workflow-level test using `temporalio.testing` for the timeout path (4),
   which doesn't require waiting out a real 24h timer.

## Non-goals (YAGNI)

- No HumanLayer integration — D-015 lists it as optional; Temporal signals alone satisfy the
  mechanism.
- No real Agent Runtime integration — doesn't exist until Wave 4. example-service stands in.
- No approval *policy* engine (e.g., "risk_level >= high always requires two reviewers") — single
  reviewer decision only. Policy composition is Guardrails' job, not this harness's.
- No notification delivery (email/Slack ping to a reviewer) — a reviewer polls `GET /approvals`.

## Components

- **`services/approvals-service/`** — new greenfield service (copier template), Postgres
  `approvals` DB (+ `approvals_test`), Temporal client + a dedicated **worker process** (new
  long-running container — the first non-API-server container we've added since upstream-stub).
- **`ApprovalWorkflow`** (`temporal_workflow.py`) — `@workflow.defn`, blocks via
  `workflow.wait_condition` on a `@workflow.signal def decide(...)`, races against a
  `workflow.timeout` (default 24h, overridable per-request) that auto-resolves to `expired`.
  Returns the final decision; the API layer's Postgres row is the durable *read* projection, the
  workflow is the durable *source of truth*.
- **example-service** — gains scope `approvals:request` (new identity-service migration,
  simulated-principal pattern, same as every prior "future caller" grant).
- A new simulated **reviewer** OAuth2 client (identity-service), scope `approvals:decide` — kept
  separate from `approvals:request` because requesting and deciding are different trust levels; a
  service that can create risky-action requests should not be able to approve its own requests.
- **Reviewer UI** (`static/index.html`, mounted at `/ui`, same shape as Skill Registry's D-037
  UI) — lists pending approvals (action_type, requester, risk_level, payload), and
  approve/reject/edit controls per row. Takes a bearer token pasted in by the human reviewer
  (never a client secret, same D-037 rule), calls `POST /approvals/{id}/decide` directly. No new
  backend surface — it's a thin client over the same API the pytest test also exercises.

## API (approvals-service)

- `POST /approvals` (scope `approvals:request`) — `{action_type, action_payload, risk_level,
  requester, timeout_hours?}` → starts `ApprovalWorkflow`, persists `pending` row, returns
  `{id, workflow_id, status: "pending"}`.
- `GET /approvals` (either scope) — list, filter by `status`/`requester`.
- `GET /approvals/{id}` (either scope) — current state (Postgres projection — cheap, no Temporal
  round-trip).
- `POST /approvals/{id}/decide` (scope `approvals:decide`) — `{decision: approve|reject|edit,
  edited_payload?, decided_by}` → signals the live workflow, updates Postgres once the workflow
  ack's the signal.

Deliberately **no blocking "wait for result" endpoint.** A caller polls `GET /approvals/{id}`
instead of the HTTP connection blocking on `handle.result()` — an HTTP request left open for
potentially 24h is fragile (proxy timeouts, connection resets) in a way polling isn't, and the
sequence diagram's `RT->>HITL: approve?` is a logical hop, not a literal single blocking HTTP
call in any real implementation.

## Storage

- Postgres `approvals`: `approvals` table (id, workflow_id, requester, action_type,
  action_payload JSONB, risk_level, status, decision_payload JSONB, decided_by, decided_at,
  created_at). Postgres is the queryable projection; Temporal's own history is authoritative for
  in-flight state (point 5 above).
- Temporal: uses the existing `temporal`/`temporal_visibility` databases from the Wave 3 merge —
  no new Temporal-side storage.

## Build order (dependency-ordered)

1. `services/approvals-service/` scaffold (copier `task new-service`) + `approvals` Alembic
   migration.
2. `temporal_workflow.py`: `ApprovalWorkflow` (signal + timeout), unit-tested with
   `temporalio.testing.WorkflowEnvironment` (time-skipping, so the 24h timeout test runs in
   seconds).
3. Worker container in `docker-compose.yml` (new `worker` process under the existing `temporal`
   profile, or a new `approvals` profile — TBD during build based on what's cleanest).
4. `main.py`: the four endpoints, hybrid `auth_mode` + `require_scope`.
5. identity-service migrations: `approvals:request` → example-service; new reviewer client +
   `approvals:decide`.
6. `static/index.html` reviewer UI at `/ui` (`StaticFiles` mount, same pattern as Skill
   Registry) — pending-list view + decide actions, bearer-token entry.
7. Live verification against the full stack: request → visible in Temporal UI → decide (via API
   and via the UI) → signal delivered → Postgres updates; worker-restart-mid-flight test;
   timeout-path test.
8. Committed `pytest` integration test (closes D-049).

## Testing strategy

- Workflow logic: `temporalio.testing` time-skipping environment — no real waiting for the
  timeout path.
- Live-stack integration test (new): real identity-service tokens (both scopes) → real
  approvals-service → real Temporal → decision round-trip. This becomes the Temporal harness's
  first committed automated test, retroactively closing that gap.
- Restart-resilience: manual live verification (documented in this spec's findings), not
  practical to automate reliably in the standard pytest run.

## Risks / watch-items

*(updated live with real findings during build)*

- Worker container is new territory — first long-running non-HTTP-server process in the compose
  stack. Restart/health-check semantics need real verification, not assumption.
- `workflow.timeout` racing against `wait_condition` needs to actually cancel the losing side
  cleanly (no orphaned timers) — verify via the Temporal UI's workflow history, not just the
  returned result.
- Reviewer UI decision: user chose to build the minimal reviewer UI now rather than defer it —
  it's now in scope for this branch (see Build order step 6).
- **Real bug found live**: `POST /approvals/{id}/decide`'s return type is
  `dict[str, Any] | JSONResponse` (200 with the decided row, or 202 with the still-pending row if
  the workflow hasn't finished persisting within the wait window) — FastAPI's route decorator
  tries to build a Pydantic response field from that union and fails at import time
  (`FastAPIError: Invalid args for response field!`), crashing the container on startup. Fixed
  with `response_model=None` on that route — returning a raw `Response` object from a handler
  needs this whenever the handler's other branch returns a plain value, not just when it always
  returns a `Response`.
