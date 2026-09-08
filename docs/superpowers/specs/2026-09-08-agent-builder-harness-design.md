# Agent Builder Harness — Design

**Status:** design drafted, not yet built.
**Date:** 2026-09-08
**Wave:** 4 (Agent Lifecycle) — first Wave 4 harness
**Branch:** `feat/wave4-agent-builder`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-054, D-070..D-074) ·
[../../implementation-plan.md](../../implementation-plan.md)

## Context

D-054 already re-confirmed CrewAI as the framework, with a mitigation plan for its disclosed
CVEs: don't use CrewAI's own Code Interpreter or RAG-URL tools; route code execution and URL
fetches through this platform's own Sandbox and Guardrails instead. This design follows through
on that plan.

`crewai` + `crewai-tools` were installed and their actual execution paths read before designing
anything — same discipline as every prior harness, specifically checking for the two classes of
bug already found twice in this platform (D-051/D-059/D-064's telemetry defaults, D-063/D-069's
token-refresh-vs-client-caching), rather than assuming CrewAI is exempt from either. Five real
findings, D-070..D-074 (see decisions.md) — none blocking, but each shaped this design:

- Telemetry is a clean env-var gate (`CREWAI_DISABLE_TELEMETRY=true`), checked in the actual
  source, not inferred.
- CrewAI's `LLM.call()` calls `litellm.completion()` fresh every time with `api_key` passed as a
  plain argument — no cached client to go stale like D-069's `OpenAILike`. Still verified live
  against a real TTL boundary during build (D-071's whole point is that resemblance to a working
  fix isn't proof).
- Tool access goes through `agent-gateway`'s REST API via a small custom `BaseTool`, not
  `crewai-tools`' `MCPServerAdapter` (which would bypass this platform's identity/scope model
  entirely — the exact seam D-043 built `agent-gateway` to close).
- `agent-builder-service` gets its own real OAuth2 client (D-062 pattern); `example-service`'s
  existing `agentcard:sign`/`registry:publish` grants are left untouched, having checked first
  (D-073) that Agent Registry's own tests depend on them — the same regression Knowledge/RAG hit
  by *not* checking first (D-069's `alice` sibling finding).
- CrewAI's *tracing* subsystem (separate from D-070's telemetry) fires a one-time local
  "first execution" flow that a plain `Crew(tracing=False)` does not fully suppress — traced into
  its source (D-074) and confirmed the one branch that could send anything over the network is
  hard-gated on an interactive TTY, which a FastAPI server never has. Mitigated by setting
  `CREWAI_TRACING_ENABLED=false` alongside `tracing=False`; the harmless local file write/console
  panel is accepted as-is.

Per architecture.md's control-plane diagram, Agent Builder reads the registries, publishes a
signed Agent Card, and (eventually) hands off to Deployment Pipeline — Deployment Pipeline itself
isn't built yet (next Wave 4 harness), so that hand-off is a documented non-goal here, not a gap.

## Goal & success criteria

A caller submits a build request (a name, description, task, and optionally which skills to
draw on); the harness constructs a real CrewAI agent pointed at `llm-gateway`, actually runs it
once as proof the built agent works (not just that it was configured), publishes a signed Agent
Card to Agent Registry, and selects baseline eval suites from Eval Registry for the agent's
declared capabilities.

Done when, live against the running stack:
1. A real bearer token (scope `builder:build`) submits a build request; the harness actually
   executes a CrewAI `crew.kickoff()` against `llm-gateway`, and the response includes real
   model output, not a stub.
2. The built agent, given a tool wired through `agent-gateway` (the already-live
   `mcp-skills-demo` tool), genuinely calls it during its run — observable in the kickoff's own
   tool-use trace, not asserted from configuration alone.
3. A resulting Agent Card is signed by identity-service and published to Agent Registry —
   fetchable back from the registry afterward, same "byte-for-byte, not just a 201" bar every
   registry harness already proved for its own publish path.
4. Eval Registry's `GET /suites?applies_to=...` is queried for the built agent's declared
   capabilities, and whatever suites (including zero) come back are included in the response —
   proving the query happens for real, not that suites necessarily exist yet to match it.
5. CrewAI's own Code Interpreter and RAG-URL tools are never enabled anywhere in this service —
   confirmed by inspecting the actual `Agent`/`Task` construction, not just documented as intent
   (D-054's CVE mitigation, made real).
6. `agent-builder-service`'s own token to `llm-gateway`/`agent-gateway` survives a real wait past
   the 300-second TTL — verified live, the same two-step way as D-063/D-069.

## Non-goals (YAGNI)

- Deployment Pipeline hand-off — doesn't exist yet; this harness's output (a published Agent
  Card) is exactly what Deployment Pipeline is meant to consume when it's built next.
- Drafted eval cases (D-012 mentions an LLM optionally drafting scenario cases for human review)
  — this harness selects existing baseline suites by capability tag; authoring new draft cases is
  a real, separate feature worth its own scoped follow-up, not bundled in here.
- Multi-agent crews (more than one `Agent` per `Crew`) — CrewAI's core value is orchestrating
  multiple agents, but a single-agent crew is enough to prove the mechanism end to end for v1;
  multi-agent composition is a natural, low-risk follow-up once this scaffold exists.
- CrewAI's built-in memory/RAG — this platform already has Memory (Mem0) and Knowledge/RAG
  harnesses; enabling CrewAI's own would duplicate them (same reasoning D-013 already applied to
  Mem0-vs-RAG, one level up).

## Components

- **`services/agent-builder-service/`** — new greenfield service, Postgres `builder` DB
  (+ `builder_test`): a `builds` table logging every build request and its outcome (kickoff
  result, published card id, selected suites) — this harness's own audit trail, same shape as
  Sandbox's `executions`/Guardrails' `checks`.
- **`agent_gateway_tool.py`** — a small custom `crewai.tools.BaseTool` calling `agent-gateway`'s
  `POST /tools/{name}/call` (D-072), reusing the already-registered `mcp-skills-demo` tool.
- **`platform_core.llm_gateway_auth.TokenClient`** — reused as-is for `llm-gateway` calls;
  `agent-builder-service` also needs a token for `agent-gateway`, requested with a wider scope
  set (`llm_gateway:call agent_gateway:call`) from the same client.
- **`agent-builder-service`'s own OAuth2 client** (D-073) — `agentcard:sign`, `registry:publish`,
  `llm_gateway:call`, `agent_gateway:call`.
- **example-service** — gains scope `builder:build` (D-030 pattern, as the external caller of
  *this* harness).

## API (agent-builder-service)

- `POST /builds` (scope `builder:build`) — `{name, description, task, role, goal, backstory}` →
  constructs a single-agent CrewAI `Crew` (LLM via `llm-gateway`, one tool via `agent-gateway`),
  runs `kickoff()`, builds + signs + publishes an Agent Card, queries Eval Registry for matching
  baseline suites, persists the outcome. Returns `{build_id, agent_card, kickoff_result,
  eval_suites}`.
- `GET /builds` (any authenticated caller) — list past builds.
- `GET /builds/{id}` (any authenticated caller) — one build's full record.

## Storage

Postgres `builder`: `builds` table (id, requester, name, description, task, kickoff_result,
agent_card_id, eval_suite_names JSONB, created_at). No object storage needed for v1 — build
requests and their outputs are small enough for Postgres columns directly.

## Build order (dependency-ordered)

1. `services/agent-builder-service/` scaffold + `builds` table migration.
2. `agent_gateway_tool.py` — unit-tested against a stub `agent-gateway` (no live-stack
   dependency), same "test against the real request shape" posture as `test_contextforge_client.py`.
3. `crew_layer.py`: constructs the `Agent`/`Task`/`Crew`, wires `LLM` at `llm-gateway` via
   `TokenClient`, wires the one tool, runs `kickoff()` in a threadpool (CrewAI's core is
   synchronous, same pattern as every prior harness wrapping a sync SDK).
4. `main.py`: `POST /builds`, `GET /builds[/{id}]` — card construction, signing (call
   identity-service `/cards/sign`), publishing (call agent-registry `/agents`), eval-suite
   selection (call eval-registry `GET /suites?applies_to=...`).
5. identity-service migration: new `agent-builder-service` OAuth2 client; `builder:build` →
   example-service.
6. Live verification: all 6 done-when criteria against the running stack, including the
   TTL-boundary proof (criterion 6).
7. Committed `pytest`: `agent_gateway_tool.py` unit test (stub server); live-stack test for the
   full build → sign → publish → eval-select flow.

## Testing strategy

- `agent_gateway_tool.py` tested against a stub `agent-gateway` HTTP server (real request/
  response shape, no live dependency) — same shape as every prior stub-based auth/client test in
  this repo.
- Live-stack test: real identity-service token → real `agent-builder-service` → real CrewAI
  kickoff against real `llm-gateway` → real tool call through real `agent-gateway` → real signed,
  published Agent Card fetched back from `agent-registry` → real Eval Registry query.
- The TTL-boundary proof (done-when criterion 6) is a manual live-verification step during build,
  same as D-063/D-069 — not a committed automated test that waits out a real 5 minutes.

## Risks / watch-items

*(updated live with real findings during build)*

- CrewAI's `kickoff()` duration is bounded by the LLM's own response time (this platform's local
  Ollama model, already observed to be several seconds per call) — worth confirming a
  single-agent, single-tool-call kickoff completes in a reasonable window for a synchronous HTTP
  request, not something that needs to become async/polling for v1.
- The custom `BaseTool`'s synchronous `_run()` needs its own token for `agent-gateway` — since
  CrewAI's execution is fully synchronous (no `async`/`await` in the tool-call path), this can't
  reuse `platform_core.llm_gateway_auth.TokenClient` (an async class) directly inside `_run()`;
  needs a plain synchronous token fetch, decided during build.
- Whether any eval suites in the live registry actually have an `applies_to` tag matching this
  harness's built agents is untested — criterion 4 only requires the *query* to happen correctly,
  not that results are non-empty, since Eval Registry doesn't yet have a real production catalog.
