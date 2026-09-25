# Deployment Pipeline Harness — Design

**Status:** design drafted, not yet built. Depends on three smaller pieces landing on `main`
first — see "Dependency branches" below.
**Date:** 2026-09-25
**Wave:** 4 (Agent Lifecycle) — third and final Wave 4 harness; the last of all 16.
**Branch:** `feat/wave4-deployment-pipeline` (this design doc); dependency work happens on its
own branches first (see below) before this branch's own service code is written.

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-009, D-077, D-087..D-089) ·
[../../implementation-plan.md](../../implementation-plan.md) ·
[2026-09-08-agent-builder-harness-design.md](2026-09-08-agent-builder-harness-design.md) ·
[2026-09-18-evals-runner-harness-design.md](2026-09-18-evals-runner-harness-design.md)

## Context

This is the last of the 16 harnesses. D-009 already ruled out Kubernetes for the reference
(container packaging + Temporal, kagent left as the documented production upgrade path) — this
design follows through on that.

Two real gaps were found reading the actual code of the two services this harness depends on,
not assumed from their APIs alone:

- **Agent Builder's own `Build` row never persists `role`/`goal`/`backstory`** — only
  `name`/`description`/`task`. Its published Agent Card is thinner still: just
  `name`/`url`/`version`/`description`/`capabilities`. The implementation plan and
  `architecture.md`'s control-plane diagram both say Deployment Pipeline "reads card, REST" — but
  today's card carries nothing a caller could use to reconstruct what CrewAI agent to actually
  run. Fixed by extending the card with an `extensions` entry (A2A's own mechanism for
  platform-specific enrichment) carrying `role`/`goal`/`backstory`/`task` (D-088).
- **No real, independently-callable agent endpoint exists anywhere in this platform** — Evals
  Runner's own D-077 named this explicitly and deferred it, since nothing existed yet to call.
  Deployment Pipeline is what creates the first one, which reopens that specific deferred
  question rather than leaving it deferred indefinitely.

Two genuine design forks were walked through with the user before any code (matching every prior
harness's own discipline):

1. **What does "deploying an agent" concretely produce?** A brand-new per-agent Docker image
   built on every deploy, or one shared, generic runtime image parameterized per deployment. The
   user chose the shared image (D-087) — a per-agent build adds real `docker build` time/storage
   cost for config that's a handful of strings, over-engineered for what this reference actually
   needs.
2. **Should the eval-gate call the newly-deployed agent for real, or keep grading `llm-gateway`
   directly (Evals Runner's existing v1 shape)?** The user chose to extend Evals Runner now with
   a real agent-runtime target (D-089), closing D-077's deferred non-goal for real rather than
   leaving Deployment Pipeline's gate meaningless (grading the underlying model, not the actually
   -deployed agent's config).

## Goal & success criteria

A caller submits a deploy request for an already-published Agent Card; the harness durably (via
Temporal) starts a real container running that agent's actual configuration, gates it with a real
Evals Runner run against the live deployed agent (not a stand-in), and only leaves it running if
the gate passes — rolling back (stopping the container) on a failing gate, never leaving a bad
deployment live.

Done when, live against the running stack:
1. A real bearer token (scope `deploy:create`) submits a deployment for a real, already-published
   Agent Card (carrying the new `extensions`); `DeploymentWorkflow` runs a real Docker container
   from the shared `agent-runtime` image with that agent's actual role/goal/backstory/task
   injected — not a stub, not hardcoded.
2. The deployed container's own `POST /invoke` is real and callable — a genuine CrewAI kickoff
   against `llm-gateway` runs when called, observable in its own real output.
3. The eval gate genuinely calls the deployed container through Evals Runner's new
   agent-runtime target (not `llm-gateway` directly) — proven with both a passing run (the
   deployment stays up) and a deliberately-failing run (the deployment is torn down), both live.
4. A `platform.deployment.v1` event is emitted to Kafka on both outcomes.
5. A failing gate leaves zero orphaned running containers — confirmed by checking Docker directly
   after a deliberately-failing deployment, the same "leaves no leftover state" bar Sandbox
   already proved for its own containers.
6. The workflow survives a worker restart mid-deployment — the concrete proof this needed
   Temporal's durability, the same proof Approvals/HITL already established for its own workflow.
7. Every token in this chain (`deployment-pipeline-service`'s own, `agent-runtime`'s shared one)
   survives a real wait past the 300-second TTL, verified live the same way as every prior
   harness.

## Non-goals (YAGNI)

- Real A2A protocol compliance on the deployed runtime's endpoint — a plain REST `POST /invoke`
  for v1, same "schema and signing are real, a full protocol server isn't" posture Agent Registry
  already has for A2A Agent Cards.
- Tool-wiring on deployed agents — Agent Builder's own build step already proves tool-calling
  works; re-proving it in the deployed runtime isn't needed for v1. A plain CrewAI agent (no
  `agent-gateway` tool) is what gets deployed.
- Per-agent Docker image builds — the user's own fork decision (D-087); one shared image,
  parameterized per deployment via environment variables.
- Rolling updates, multiple concurrent versions of the same agent, or auto-scaling/load
  balancing across replicas — v1 is deploy-one/stop-explicitly, not a fleet manager.
- Kubernetes/kagent — D-009 already settled this permanently for this reference platform.

## Components

- **`services/agent-runtime/`** (new, shared, built once) — a small CrewAI-based service, not a
  standing compose service (build-only compose entry, image tag `notgovharness-agent-runtime`).
  `POST /invoke` runs a single CrewAI kickoff using `role`/`goal`/`backstory` baked in via
  environment variables at container start (from the card's `extensions`) and a `task`/input from
  the request body. Its own **shared** OAuth2 client (`agent-runtime`, scope `llm_gateway:call`)
  — every deployed instance uses the same identity; no per-instance provisioning (D-087).
- **`services/deployment-pipeline-service/`** (new) — Postgres `deploy` DB (+ `deploy_test`): a
  `deployments` table (id, requester, agent_name, agent_version, container_id, host_port, status,
  eval_run_id, created_at) as this harness's own audit trail. Reuses Approvals/HITL's exact
  worker/workflow/activity split and Sandbox's exact Docker SDK pattern (sync `docker` client in
  a threadpool).
- **`DeploymentWorkflow`** (Temporal, durable): fetch card from Agent Registry → run a new
  `agent-runtime` container (env vars from the card's `extensions`, port-published to the host —
  same `host.docker.internal` pattern LLM Gateway already uses for the host's Ollama) → poll its
  `/healthz` → run the eval gate via Evals Runner's new agent-runtime target → on failure, stop
  and remove the container (rollback-safe) → emit `platform.deployment.v1` either way → persist
  final status via an activity.
- **Card extension fix (`agent-builder-service`)** — adds an `extensions` entry to the published
  card: `{"uri": "urn:notgovharness:crewai-agent-config", "description": ..., "params": {"role",
  "goal", "backstory", "task"}}` (D-088). Small, additive, backward-compatible.
- **Agent-runtime target (`evals-runner-service`)** — a new target type on `POST /runs` alongside
  the existing direct-`llm-gateway` one: `AgentRuntimeTarget`, calling a deployed container's
  `POST /invoke` per golden instead of a raw chat completion (D-089).
- **`deployment-pipeline-service`'s own OAuth2 client** — scope `evals:run` (to call the gate);
  reading Agent Registry needs no scope (its GET endpoints are open, same as Eval Registry).
- **`evals-runner-service`** gains scope `agent_runtime:invoke` (to call a deployed container).
- **`example-service`** gains scope `deploy:create` (D-030 pattern, external caller of this
  harness).

## API

**`agent-runtime`** (per deployed instance): `POST /invoke` (scope `agent_runtime:invoke`) —
`{input}` → runs the baked-in CrewAI agent, returns `{output}`. `GET /healthz` (open, no scope —
same as every other harness) for the workflow's own readiness poll.

**`deployment-pipeline-service`**:
- `POST /deployments` (scope `deploy:create`) — `{agent_name, agent_version?}` → starts
  `DeploymentWorkflow`, returns the initial (pending) record.
- `GET /deployments` / `GET /deployments/{id}` (any authenticated caller) — read status,
  including `container_id`, `host_port`, `eval_run_id` once known.
- `POST /deployments/{id}/stop` (scope `deploy:create`) — stops and removes a running
  deployment's container, marks it `stopped`.

**`evals-runner-service`** (extension): `POST /runs` gains an optional target selector —
`{..., target: {"type": "agent_runtime", "url": "http://host.docker.internal:<port>"}}`
(default/omitted stays the existing direct-`llm-gateway` behavior, unchanged for every existing
caller).

## Storage

Postgres `deploy`: `deployments` table (id, requester, agent_name, agent_version, container_id,
host_port, status ∈ {`deploying`, `running`, `eval_failed`, `failed`, `stopped`}, eval_run_id,
created_at). One table, matching every prior harness's own minimal audit-trail shape.

## Build order (dependency-ordered)

Three pieces resolve real dependencies and are built + merged to `main` **before**
`deployment-pipeline-service` itself is written — each is independently valuable, independently
testable, and independently mergeable, so each gets its own feature branch rather than being
bundled into one large branch (see "Dependency branches" below):

1. **`agent-runtime`** — scaffold, `/invoke`, own OAuth2 client, Dockerfile + build-only compose
   entry. Verified standalone: manually `docker run` an instance with test env vars, curl
   `/invoke` directly, confirm a real CrewAI kickoff runs. No dependency on the other two pieces.
2. **Card extensions fix (`agent-builder-service`)** — small, additive. Verified: a real
   `POST /builds` now publishes a card whose `extensions` field round-trips through Agent
   Registry correctly.
3. **Agent-runtime target (`evals-runner-service`)** — depends on (1) existing to verify against
   (a manually-started `agent-runtime` container). Verified: a real `POST /runs` with
   `target: {"type": "agent_runtime", ...}` judges a manually-deployed instance correctly,
   existing direct-`llm-gateway` behavior unaffected (regression-tested).

Then, on `feat/wave4-deployment-pipeline` (this branch, rebased onto `main` once the above are
merged):

4. `services/deployment-pipeline-service/` scaffold + `deployments` table migration.
5. `temporal_workflow.py` + `activities.py` + `worker.py`.
6. `main.py`: `POST /deployments`, `GET /deployments[/{id}]`, `POST /deployments/{id}/stop`.
7. identity-service migration: `deployment-pipeline-service`'s own client; `deploy:create` →
   `example-service`.
8. docker-compose wiring (API + worker containers, `agent-runtime` as a build-only entry).
9. Live verification of all 7 done-when criteria, including the failing-gate rollback proof and
   the worker-restart-mid-deployment durability proof.
10. Committed `pytest`: stub tests for the Docker orchestration and Agent Registry client; a
    live-stack test for the full deploy → gate-pass → running flow and a separate one for
    deploy → gate-fail → rolled-back flow.

## Dependency branches (fork from `main`, merge before `feat/wave4-deployment-pipeline` builds)

In order:

1. **`feat/agent-runtime`** — the new shared runtime image/service (build order step 1).
2. **`feat/agent-builder-card-extensions`** — the card-schema fix (build order step 2). Small,
   independent of (1); could be built in parallel, but sequenced after here since it lands the
   schema `deployment-pipeline-service` will read.
3. **`feat/evals-runner-agent-target`** — the new target type (build order step 3). Depends on
   (1) being merged (needs a real `agent-runtime` image to verify against live).

Once all three are on `main`, `feat/wave4-deployment-pipeline` rebases onto it and build order
steps 4-10 proceed on that branch as normal.

## Testing strategy

- `agent-runtime`: a smoke test (`/healthz`) plus a live test that a real `/invoke` call produces
  genuine CrewAI output against real `llm-gateway`.
- `agent-builder-service`: existing live-stack test extended to assert the published card's
  `extensions` field round-trips correctly through Agent Registry.
- `evals-runner-service`: a new live test exercising the `agent_runtime` target against a
  manually-started `agent-runtime` container, plus the existing suite re-run unchanged to confirm
  no regression to the default `llm-gateway` target.
- `deployment-pipeline-service`: stub tests for the Docker orchestration activity (against a
  fake/mocked Docker client, same posture as any other stub-based client test in this repo) and
  the Agent Registry client; two live-stack tests (pass-gate keeps it running; fail-gate rolls it
  back) plus the worker-restart durability proof as a manual live-verification step, same as
  Approvals/HITL's own.

## Risks / watch-items

*(to be updated live with real findings during build, per this repo's established discipline)*

- Docker's dynamic host-port assignment (`ports={"8000/tcp": None}`) needs to be read back
  correctly from `container.reload()` before the workflow can construct the
  `host.docker.internal:<port>` URL other containers will use — unverified until built.
- The shared `agent-runtime` OAuth2 client being reused across every deployed instance means all
  deployed agents share one identity/trust level — acceptable for v1 (matches D-087's "same kind
  of thing" reasoning) but worth flagging if a future need for per-agent-instance auditability
  or revocation surfaces.
- Whether Evals Runner's new `agent_runtime` target needs its own timeout tuning (D-082's lesson
  — `llm-gateway`'s own upstream timeout already needed raising for judge-shaped calls; a
  deployed agent's own kickoff could be similarly slow) is untested until built.
- Temporal activity retry/timeout tuning for the Docker-orchestration activities (start/stop a
  container) hasn't been decided yet — needs real timing data from build, not guessed upfront.
