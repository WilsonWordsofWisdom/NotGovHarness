# Sandbox Harness — Design

**Status:** design drafted, not yet built.
**Date:** 2026-08-31
**Wave:** 3 (Runtime & Policy)
**Branch:** `feat/wave3-sandbox`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-014, D-050) ·
[../../implementation-plan.md](../../implementation-plan.md)

## Context

D-014 named E2B as the framework for isolated agent-code-execution "on the strength of it being
OSS, self-hostable." That framing didn't survive contact with E2B's own self-hosting docs
(`e2b-dev/infra`, checked before writing anything below): self-hosting E2B for real means
Firecracker microVMs orchestrated by Nomad + Consul, provisioned via Terraform + Packer, on GCP
(2,500GB SSD quota + 24 CPUs minimum, ~$1,250/mo at list price) or AWS with nested-virtualization
instances — no documented lighter-weight local mode exists. This breaks D-002 ("runs on one
machine via `docker-compose`... not production cloud") outright, and this repo's Docker Desktop
host (a linuxkit VM) has no exposed KVM for Firecracker regardless of budget.

D-050 records the resulting decision: build `sandbox-service` against the *operation shape* E2B's
API exposes (submit code, get back stdout/stderr/exit code, network-isolated by default) but
execute locally via plain Docker containers instead of microVMs — same shape of tradeoff as
D-009's kagent decision for Deployment Pipeline (keep the unit's interface compatible with the
real thing; don't drag its production orchestration platform into a single-machine reference).

Three properties this design leans on were verified empirically against this repo's actual
Docker Desktop, not assumed from Docker's documentation:
- `docker run --network none` genuinely blocks egress (a script's own attempt to reach
  `http://example.com` fails with a DNS resolution error, not an app-level convention).
- `--memory`/`--memory-swap` limits are enforced by the kernel's cgroup OOM killer (exit 137)
  *before* Python's own allocator ever raises `MemoryError` in-process — the limit is real, not
  cooperative.
- Killing a container from outside (`docker kill`) reliably stops a genuine infinite loop; there
  is no in-process cooperation required for the timeout to work.

## Goal & success criteria

An authenticated caller submits code; it runs in an ephemeral, network-isolated, resource-capped
container; the caller gets back stdout/stderr/exit code; a runaway or memory-hungry submission is
killed rather than affecting the host or other executions.

Done when, live against the running stack:
1. A real bearer token (scope `sandbox:execute`) submits a Python snippet and gets back its real
   stdout.
2. A submission that tries to reach the network fails from inside the sandbox (not merely
   documented as blocked — observed failing in the captured stderr).
3. A submission that allocates unbounded memory is killed by the container's own memory limit,
   observed as a distinct terminal status (`oom_killed`), and the host stays unaffected.
4. A submission that loops forever is killed at its configured timeout, observed as a distinct
   terminal status (`timed_out`), not left running.
5. Every execution — however it ends — is fully cleaned up (no leftover stopped/running
   containers), verified via `docker ps -a` after a batch of runs.
6. A record of every execution (code, status, stdout/stderr, exit code, timing) is queryable
   afterward from Postgres, independent of whether the container that ran it still exists.

## Non-goals (YAGNI)

- Multi-language support — Python only for v1 (matches the likely CrewAI/agent code-interpreter
  use case named in D-014's own rationale). Adding a language is "add another base image," not a
  design change.
- File upload/download, persistent/long-lived sandboxes you call multiple times, or streaming
  output — E2B's real API supports these; this reference does one-shot "run this, get the
  result" only.
- Multi-tenant scheduling/queueing across many concurrent executions — single-node Docker Engine,
  no fairness/quota system beyond per-execution resource limits.
- Hardening `sandbox-service` itself against a malicious *caller* (as opposed to malicious
  *submitted code*) — every other harness's `require_scope` gate is the only defense; this
  service is not trying to be safe against an untrusted API caller, only against the code an
  authorized caller submits.

## Components

- **`services/sandbox-service/`** — new greenfield service (copier template), Postgres `sandbox`
  DB (+ `sandbox_test`). Talks to the **host's Docker Engine** via the `docker` Python SDK,
  mounting `/var/run/docker.sock` — the same dependency already named for the (unbuilt)
  Deployment Pipeline harness in `architecture.md`'s engine table.
- **Executor** (`executor.py`) — given `{language, code, timeout_seconds}`: creates a container
  from a fixed per-language base image (`python:3.12-slim` for v1) with the code mounted in
  read-only, `network_disabled=True`, memory/CPU limits, running as a non-root user; waits up to
  `timeout_seconds` for it to finish; classifies the terminal state (`completed` / `oom_killed` /
  `timed_out`); captures stdout/stderr/exit code; always removes the container in a `finally`.
- **example-service** — gains scope `sandbox:execute` (same D-030 simulated-principal pattern —
  no real Agent Runtime exists yet to be the caller).

## API (sandbox-service)

- `POST /executions` (scope `sandbox:execute`) — `{language, code, timeout_seconds?}`. **Blocking
  — unlike Approvals/HITL's deliberately poll-based API**, because an execution is bounded in
  seconds (a hard timeout, not "however long a human takes"), so a synchronous HTTP response is
  the right shape here, not a fragility risk. Returns `{id, status, stdout, stderr, exit_code,
  duration_ms}`.
- `GET /executions` (any authenticated caller) — list, filter by `status`/`requester`.
- `GET /executions/{id}` (any authenticated caller) — a past execution's full record.

## Storage

Postgres `sandbox`: `executions` table (id, requester, language, code, status, stdout, stderr,
exit_code, timeout_seconds, duration_ms, created_at). No object storage — code and output are
small enough for Postgres columns directly (no bundle/dataset shape like Skill or Eval Registry).

## Build order (dependency-ordered)

1. `services/sandbox-service/` scaffold + `executions` Alembic migration.
2. `executor.py`: the Docker-backed run function, unit-tested directly (no HTTP layer) against
   the real Docker Engine — this is where done-when criteria 2-5 get proven first, in isolation.
3. `main.py`: `POST /executions` (synchronous call into the executor), `GET /executions[/{id}]`,
   hybrid `auth_mode` + `require_scope`.
4. identity-service migration: `sandbox:execute` → example-service.
5. `docker-compose.yml`: `sandbox-service` under a new `sandbox` profile, with the Docker socket
   mounted — first service in this stack with host Docker Engine access, a real privilege
   worth calling out explicitly in its own compose comment, not folded in quietly.
6. Live verification: all 6 done-when criteria against the running stack, plus a batch-cleanup
   check (`docker ps -a` before/after N executions of mixed outcomes).
7. Committed `pytest`: executor-level tests (network block, memory kill, timeout kill, cleanup)
   run directly against Docker, no live-stack dependency needed for those; a live-stack test for
   the HTTP layer + scope gating, same shape as every other harness's.

## Testing strategy

- Executor-level tests exercise the real Docker Engine directly (not mocked) — a mock would
  assert this design's *intent*, not that `--network none`/`--memory`/`docker kill` actually do
  what's claimed on this host. These don't need the live stack (no Postgres/identity-service
  dependency), only Docker itself, so they run in the normal `pytest` pass, not gated behind a
  skip-if-down fixture.
- Live-stack test: real identity-service token → real sandbox-service → confirms the HTTP/auth
  layer and Postgres persistence around the (already-proven) executor.

## Risks / watch-items

*(updated live with real findings during build)*

- **Docker socket access is real host privilege**, disclosed in D-050 and repeated here: this
  reference's isolation boundary is *executed code* vs. host, not `sandbox-service` vs. host. A
  compromised or buggy `sandbox-service` has the same reach as anything else with
  `/var/run/docker.sock` — this is a known, accepted limitation of the local-Docker-instead-of-
  Firecracker tradeoff, not an oversight.
- Container cleanup on a mid-execution `sandbox-service` crash/restart needs verification — an
  orphaned container from a request that never reached its `finally` block would sit running
  until something reaps it. Worth a live restart-during-execution test, similar in spirit to
  Approvals/HITL's worker-restart proof.
- Base image pull time (`python:3.12-slim`) on first use — pre-pulling in the Dockerfile/compose
  build, not on first request, avoids a slow first execution.
