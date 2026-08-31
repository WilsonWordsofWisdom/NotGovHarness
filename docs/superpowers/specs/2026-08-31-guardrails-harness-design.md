# Guardrails Harness — Design

**Status:** built and verified live — all 6 done-when criteria confirmed against the running
stack, including two real bugs found and fixed only once running for real (NeMo's sync/async
event-loop conflict, and a second-attempt telemetry fix after the first one silently didn't
survive `Guard`'s own reload behavior).
**Date:** 2026-08-31
**Wave:** 3 (Runtime & Policy)
**Branch:** `feat/wave3-guardrails`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-051, D-052, D-053) ·
[../../implementation-plan.md](../../implementation-plan.md)

## Context

The locked stack names a layered pipeline: **LLM Guard → NeMo Guardrails → Guardrails AI → Llama
Guard**. Before designing anything, all three non-LLM layers were actually installed and run
(same discipline as D-050's E2B research), which surfaced three real findings recorded in
decisions.md:

- **D-051**: Guardrails AI's telemetry is opt-out, not opt-in — a bare `Guard().validate()` call
  phones home to a Guardrails-AI-owned AWS endpoint by default, with no configuration at all.
  Fixed by writing `~/.guardrailsrc` with `enable_metrics=false` before first use, verified live.
- **D-052**: Guardrails AI's own Hub CLI is deprecated by the tool itself in favor of installing
  validators as plain public-PyPI packages (`guardrails-ai-<name>`) — this repo uses that path,
  pinned in `pyproject.toml` like every other dependency, not the retiring CLI/registry.
- **D-053**: NeMo Guardrails' pattern-based (Colang) rails run correctly with zero LLM
  configured; only its topical/self-check rail types need an LLM, same as Llama Guard.

Llama Guard needs the (paused) LLM Gateway and is out of scope here, same as every other
LLM-dependent piece blocked on that. NeMo's LLM-backed rails are deferred for the same reason.

The architecture's data-plane sequence diagram calls this harness twice per task —
`RT->>GR: check(input)` before reasoning, `RT->>GR: check(output)` before returning to the
caller — so the design treats "stage" (`input`/`output`) as a first-class input, not an
afterthought.

## Goal & success criteria

A caller submits text at a given stage (input or output) and gets back an allow/block decision
plus the specific findings that drove it, across three independently-real libraries — not a
single tool wearing three labels.

Done when, live against the running stack:
1. A real bearer token (scope `guardrails:check`) submits a clean input and gets `allowed: true`.
2. A submission containing a known prompt-injection phrase (e.g. "ignore previous instructions")
   is blocked, with a finding attributing it to a specific layer (LLM Guard's `BanSubstrings` or
   NeMo's keyword flow).
3. A submission containing an unsafe pattern per the Guardrails AI layer (e.g. failing
   `RegexMatch`) is blocked, with a finding attributing it to `guardrails-ai-regex-match`.
4. All three layers ran on a given check (observable in the response's per-layer findings), not
   just whichever one happened to block first — the point is defense-in-depth visibility, not a
   short-circuit.
5. Every check (blocked or not) is queryable afterward from Postgres — stage, text, per-layer
   findings, final decision.
6. No outbound network call happens during a check beyond this platform's own containers —
   verified by confirming Guardrails AI's telemetry fix (D-051) actually holds under the compose
   deployment, not just the scratch venv it was found in.

## Non-goals (YAGNI)

- Llama Guard / any LLM-backed rail — blocked on the paused LLM Gateway, same as Memory and
  Knowledge/RAG.
- ML-based scanners (LLM Guard's prompt-injection classifier, toxicity, PII detection via
  `guardrails-ai-detect-pii`) — each needs a model download (hundreds of MB) not worth pre-baking
  for v1's done-when criteria. Documented as a straightforward follow-up (swap in the model-based
  scanner class, pre-warm it at Docker build time), not a gap silently left unaddressed.
- A policy/config UI for editing the banned-phrase lists or regexes — fixed at deploy time via
  the service's own config for v1, same posture as every other harness's fixed dev config.
- Publishing a Kafka event per check for Audit plane consumption — Postgres's own `checks` table
  is the log for v1; wiring a second integration point isn't needed to prove this harness's core
  function.
- No real Agent Runtime caller — example-service stands in (D-030 pattern), same as every prior
  harness.

## Components

- **`services/guardrails-service/`** — new greenfield service, Postgres `guardrails` DB
  (+ `guardrails_test`).
- **Three independent checker modules**, each wrapping one real library:
  - `llm_guard_layer.py` — `BanSubstrings` (a small default prompt-injection phrase list),
    `Regex` (secret-shaped-string detection, useful on the *output* stage), `TokenLimit`.
  - `nemo_layer.py` — a Colang config with one keyword-blocklist input flow, loaded via
    `RailsConfig.from_path` + `LLMRails` with `models: []`.
  - `guardrails_ai_layer.py` — `guardrails_ai.regex_match.RegexMatch`, loaded via `Guard().use(...)`.
    Disables telemetry (D-051) at module import time, before any `Guard` is constructed.
- **example-service** — gains scope `guardrails:check` (D-030 pattern).

## API (guardrails-service)

- `POST /check` (scope `guardrails:check`) — `{stage: "input" | "output", text: str}` → runs all
  three layers (always all three — criterion 4), returns `{decision: "allow" | "block",
  findings: [{layer, rule, severity, detail}]}`, persists the row.
- `GET /checks` (any authenticated caller) — list, filter by `decision`/`stage`.
- `GET /checks/{id}` (any authenticated caller) — one check's full record.

## Storage

Postgres `guardrails`: `checks` table (id, requester, stage, text, decision, findings JSONB,
created_at). No object storage — text and findings are small enough for Postgres columns
directly, same shape as Sandbox's `executions` table.

## Build order (dependency-ordered)

1. `services/guardrails-service/` scaffold + `checks` Alembic migration.
2. `llm_guard_layer.py`, `nemo_layer.py`, `guardrails_ai_layer.py` — each unit-tested directly
   against the real library (no live-stack dependency), same "test against the real thing, not a
   mock" discipline as Sandbox's executor tests. This is also where the D-051 telemetry-disabled
   fix and D-053's zero-LLM NeMo config get their own committed proof, not just the scratch-venv
   verification already done.
3. `main.py`: `POST /check` (runs all three layers, aggregates findings), `GET /checks[/{id}]`,
   hybrid `auth_mode` + `require_scope`.
4. identity-service migration: `guardrails:check` → example-service.
5. `docker-compose.yml`: `guardrails-service` under a new `guardrails` profile. Dockerfile
   pre-warms whatever the chosen layers need at build time (NeMo/LLM-Guard/Guardrails-AI import
   successfully with no runtime network dependency for the rule-based scanners in scope here).
6. Live verification: all 6 done-when criteria against the running stack, including confirming
   no outbound call happens (same check, but against the compose deployment, not the scratch venv).
7. Committed `pytest`: layer-level tests (real libraries, no live-stack dependency) + a live-stack
   test for the HTTP/auth/persistence layer, same shape as every other harness's.

## Testing strategy

- Layer-level tests exercise the real libraries directly, not mocks — a mock would assert this
  design's intent (D-050/D-051/D-052/D-053's whole point was that intent and reality diverged
  here more than once already).
- Live-stack test: real identity-service token → real guardrails-service → confirms the
  HTTP/auth layer, all-three-layers-always-run behavior, and Postgres persistence.

## Risks / watch-items

*(updated live with real findings during build)*

- **Real bug found live, twice**: the first D-051 fix (mutating `settings.rc.enable_metrics` in
  memory) looked correct in isolation but didn't survive `Guard.__init__()`'s own
  `self.configure()` call, which reloads `settings.rc` fresh from disk on every construction —
  confirmed live that `enable_metrics` silently reset to `True` right after being set `False`.
  Fixed by writing the actual `~/.guardrailsrc` file to disk instead (module-level, at import
  time, before any `Guard` is constructed) — this survives the reload because the reload reads
  the same file. See D-051's updated entry. Written to `Path.home()`, confirmed live to land at
  `/root/.guardrailsrc` under this image's runtime user.
- **Real bug found live**: `LLMRails.generate()` (sync) raises `RuntimeError` when called from
  inside an already-running event loop — invisible in a standalone script, fatal from a FastAPI
  request handler. Fixed with `generate_async` + making `nemo_layer.check()` async; a dedicated
  test (`test_nemo_check_works_inside_a_running_event_loop`) now guards this specifically, since
  a scratch script can't catch it.
- LLM Guard's import alone pulls ~750MB of dependencies (torch/transformers/spacy) even though
  v1 only uses its rule-based scanners — a real image-size cost worth being explicit about in the
  compose file's own comments, same as Sandbox's Docker-socket disclosure. Also required an
  explicit `transformers<5` pin in this service's own `pyproject.toml` — llm-guard 0.3.15's code
  still imports `transformers.TFPreTrainedModel` (removed in transformers 5.x), and nothing else
  in this workspace constrained the version, so `uv sync` alone resolved an incompatible 5.3.0.
- NeMo Guardrails prints a "No main LLM specified" warning on every startup with `models: []` —
  expected and harmless per D-053.
