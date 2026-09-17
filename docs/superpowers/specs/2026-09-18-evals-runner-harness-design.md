# Evals Runner Harness — Design

**Status:** built and verified live — all 5 done-when criteria confirmed against the running
stack, including a real 400+s run after a deliberate wait past the 300-second token TTL.
**Date:** 2026-09-18
**Wave:** 4 (Agent Lifecycle) — second Wave 4 harness (Deployment Pipeline deliberately skipped
for now, at explicit user direction)
**Branch:** `feat/wave4-evals-runner`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-039..D-042, D-077..D-086) ·
[../../implementation-plan.md](../../implementation-plan.md) ·
[2026-08-30-eval-registry-harness-design.md](2026-08-30-eval-registry-harness-design.md)

## Context

Eval Registry (Wave 2) already stores versioned eval suites: a `cases` suite has a JSONL dataset
of `Golden`s (`input`, `expected_output`, `context`, `expected_tools`, `metadata`) plus a
`metrics` envelope (`{engine, metric_id, params}`, `engine` one of `deepeval`/`ragas`/
`promptfoo`/`custom` — D-039); a `redteam` suite has a `redteam_config` and no fixed dataset
(generation, not assertion — D-040). Nothing in this platform executes an eval yet — Eval
Registry only stores and serves suites, by design (see its own harness design doc). This harness
is that missing piece.

The implementation plan's original wording says this harness "drives agents via A2A." Checked
before designing anything, not assumed: `a2a-sdk` is listed as an Agent Registry dependency but
never actually imported or used anywhere in this codebase — Agent Registry stores a Card's `url`
field as an opaque string with no validation and no client. Agent Builder's own published cards
carry a placeholder URI (`agent-builder-service://agents/{name}`) that resolves to nothing real.
There is no independently-callable A2A agent endpoint anywhere in this platform today — that is
what Deployment Pipeline would actually produce, and it hasn't been built (skipped, at explicit
user direction, in favor of building this harness next).

`deepeval` and `ragas` were installed in a scratch venv and their actual telemetry/upload paths
and integration points read before designing anything, same discipline as every prior harness.
Four real findings, D-077..D-080 (see decisions.md) — none blocking, but each shaped this design:

- No real A2A-callable agent exists yet, so "drive an agent via A2A" isn't honestly buildable
  today. **Decided with the user** (D-077): v1's system-under-test is a direct `llm-gateway`
  chat completion — exactly the `input` → `actual_output` shape DeepEval/Ragas's own metrics
  already expect — with real A2A-agent-driving documented as a non-goal/follow-up for once
  Deployment Pipeline exists.
- DeepEval's telemetry (`DEEPEVAL_TELEMETRY_OPT_OUT=1`) and its separate Confident AI
  cloud-upload path (`is_confident()`, gated purely on an unset `CONFIDENT_API_KEY`) were both
  traced to source and confirmed cleanly gated (D-078) — the first library since llama-index to
  pass this check without a trap. The high-level `evaluate()` orchestration is skipped anyway in
  favor of bare metric objects, since `evaluate()` carries CLI-oriented local-file/console-report
  baggage this server context doesn't want.
- A fresh `pip install ragas` is broken out of the box: it declares `langchain-community` with no
  version bound, which resolves to today's LangChain-1.0-generation release and breaks ragas' own
  unconditional import of a class that release moved elsewhere. Verified live and fixed by
  pinning `langchain-community<0.4` (D-079). Ragas 0.4's metrics API is also mid-redesign — the
  old `ragas.metrics.<Metric>` imports are deprecated in favor of `ragas.metrics.collections` +
  `llm_factory(provider="openai", client=...)`, which is the integration point this design uses.
  `RAGAS_DO_NOT_TRACK=true` is a real, verified-clean telemetry opt-out (own endpoint, not
  PostHog).
- Promptfoo (a Node.js CLI, not a Python library) and `redteam` suites are deferred from v1
  (D-080) — same "one thing well, first" call Agent Builder made deferring multi-agent crews.
  `cases` suites alone, judged by DeepEval and Ragas, are enough to prove the mechanism end to
  end; `engine: "custom"` is rejected outright for v1, not silently ignored.

## Goal & success criteria

A caller submits a run against a published `cases` suite; the harness fetches the suite +
dataset from Eval Registry, calls `llm-gateway` once per golden to produce a real
`actual_output`, judges each case with the metrics the suite declares (dispatched by `engine`),
and persists a readable-back result.

Done when, live against the running stack:
1. A real bearer token (scope `evals:run`) submits a run against a real `cases` suite already
   published in Eval Registry; the runner fetches the real dataset, calls `llm-gateway` for each
   golden's `actual_output`, and computes genuine DeepEval **and** Ragas metric scores in the
   same run — not stubbed, not just one engine.
2. A case with a deliberately wrong `expected_output` actually fails its metric, and a correct
   case actually passes — proving real judgment, not a rubber stamp.
3. Results are persisted and fully readable back via `GET /runs/{id}`, including each case's
   input/actual_output/expected_output and every metric's score + reason.
4. Zero outbound telemetry calls — no PostHog (DeepEval), no explodinggradients.com (Ragas), no
   Confident AI upload — confirmed via the container's own logs, the same check every prior
   harness got.
5. This service's own token to `llm-gateway` survives a real wait past the 300-second TTL,
   verified live the same two-step way as D-063/D-069/D-071.

## Non-goals (YAGNI)

- A2A-driven agent evaluation — no real A2A-callable agent exists yet (see Context); this
  harness's `llm-gateway`-direct target is exactly what Deployment Pipeline's eventual output
  would slot into as a second target type, not a redesign.
- Promptfoo — a Node.js toolchain in an otherwise all-Python monorepo; `engine: "custom"` and
  DeepEval/Ragas already cover the metric shapes Eval Registry's schema expects. A real, separate
  scoped follow-up if ever needed, not bundled in here.
- `redteam` suites — generation rather than assertion against a fixed dataset, a meaningfully
  different code path from `cases`. Deferred, not dropped.
- `metric_id`/`engine: "custom"` — rejected outright (a clear 422), not silently ignored or
  treated as a no-op pass.
- Pulling traces from Observability (Langfuse) to grade an already-executed run post-hoc — the
  plan mentions this; v1 only grades fresh `llm-gateway` calls it makes itself. A real, separate
  follow-up once there's a concrete need to grade historical traces rather than fresh runs.

## Follow-up feature requests (logged, not built here — 2026-09-18)

Requested by the user after v1 was built and verified; each is a genuinely separate capability,
not an incremental tweak, and would want its own design pass before building, matching every
other harness's spec → plan → build discipline:

1. **Auto-suggest goldens from the system-under-test.** An LLM drafts candidate `cases` goldens
   (input/expected_output/context) by exercising the system-under-test, for a human to review and
   confirm before they're published as a real suite — this is exactly the "LLM optionally
   drafting scenario cases for human review" idea D-012 already named as a future capability, now
   made concrete. Open questions for its own design pass: where does drafting live (a new
   endpoint on this service, or a separate step before `eval-registry` publish?), what's the
   human-review/confirm UX, and how does a drafted-but-unconfirmed golden avoid being mistaken
   for a real one.
2. **Auto-generate synthetic data once goldens are confirmed.** Given a small set of
   human-confirmed goldens, generate additional synthetic variations (paraphrases, edge cases) to
   expand a suite's dataset — a data-augmentation step downstream of (1). Needs its own design
   pass on synthetic-data quality/diversity controls and how synthetic cases are distinguished
   from human-authored ones in a published suite.
3. **Independently configurable models across every phase** (golden generation, synthetic data
   generation, autorater) — the same knob this build just added for the autorater/judge model
   (and the pre-existing one for the system-under-test) should extend to whichever model drafts
   goldens and whichever model generates synthetic data, once (1) and (2) exist.

## Components

- **`services/evals-runner-service/`** — new greenfield service, Postgres `evals` DB
  (+ `evals_test`): a `runs` table logging every run and its full per-case outcome — this
  harness's own audit trail, same shape as Agent Builder's `builds`.
- **`eval_registry_client.py`** — fetches suite metadata (`GET /suites/{name}[/{version}]`) and
  dataset (`GET /suites/{name}/{version}/dataset`) from Eval Registry. No scope needed — Eval
  Registry's GET endpoints are open to any caller.
- **`llm_gateway_target.py`** — the system-under-test adapter: one `llm-gateway` chat completion
  per golden, producing `actual_output`. Reuses `platform_core.llm_gateway_auth.TokenClient` +
  `call_with_auth_retry`, same as every prior service-calls-service harness.
- **`metrics/deepeval_engine.py`** — a small `DeepEvalBaseLLM` adapter pointed at `llm-gateway`
  (fresh client per call, by construction, sidestepping D-069's whole class of bug rather than
  needing to reactively test for it), plus a `metric_id` → `BaseMetric` dispatch table
  (`GEval`, `AnswerRelevancyMetric`, `FaithfulnessMetric` for v1) calling `.a_measure()` per case.
- **`metrics/ragas_engine.py`** — `llm_factory(provider="openai", client=<AsyncOpenAI pointed at
  llm-gateway>)` plus a `metric_id` → `ragas.metrics.collections` dispatch table.
- **`evals-runner-service`'s own OAuth2 client** (D-062/D-073 pattern) — `llm_gateway:call` only;
  reading Eval Registry needs no scope.
- **example-service** — gains scope `evals:run` (D-030 pattern, as this harness's external
  caller).

## API (evals-runner-service)

- `POST /runs` (scope `evals:run`) — `{suite_name, suite_version?, model?, judge_model?}` →
  fetches the suite (rejects anything but `kind: "cases"` with a clear 422 for v1) and its
  dataset, runs every golden through `llm-gateway` as the system-under-test (`model`, default
  `qwen3.8`), dispatches each of the suite's configured metrics by `engine` using `judge_model`
  (default `qwen3.8`) as the autorater — independently configurable from `model`, so the two can
  be pointed at different models when self-preference bias matters, even though it's fine for
  them to be the same. Returns `{run_id, suite_name, suite_version, model, judge_model,
  case_results, pass_count, fail_count, created_at}`.
- `GET /runs` (any authenticated caller) — list past runs.
- `GET /runs/{id}` (any authenticated caller) — one run's full per-case detail.
- `GET /ui` — a self-hosted browse/submit web UI (same shape as Skill Registry's and
  Approvals/HITL's `/ui`: a static page, takes a bearer token, never a client secret). Lets a
  caller submit a run with the suite name/version and both models set as plain text fields, and
  browse past runs with full per-case, per-metric detail (score, reason, pass/fail).

## Storage

Postgres `evals`: `runs` table (id, requester, suite_name, suite_version, model, judge_model,
case_results JSON — each entry `{input, expected_output, actual_output, metrics: [{metric_id,
engine, score, reason, passed}]}`, pass_count, fail_count, created_at). One table, matching Agent
Builder's own `builds` — no separate per-case table for v1 unless a real need to query cases via
SQL surfaces later.

## Build order (dependency-ordered)

1. `services/evals-runner-service/` scaffold + `runs` table migration.
2. `eval_registry_client.py` — unit-tested against a stub Eval Registry (no live-stack
   dependency), same "test against the real request shape" posture as prior stub-based clients.
3. `llm_gateway_target.py` — the system-under-test call, reusing `TokenClient`.
4. `metrics/deepeval_engine.py` + `metrics/ragas_engine.py` — each independently testable against
   fixed input/actual_output pairs once `llm-gateway` is reachable (a case that should obviously
   pass/fail, per criterion 2).
5. `main.py`: `POST /runs`, `GET /runs[/{id}]`.
6. identity-service migration: new `evals-runner-service` OAuth2 client; `evals:run` →
   example-service.
7. **Publish one real `cases` suite to Eval Registry first** — nothing is published there yet;
   this harness needs a real suite to run against before anything past step 4 can be verified
   live.
8. Live verification: all 5 done-when criteria against the running stack, including the
   TTL-boundary proof (criterion 5).
9. Committed `pytest`: stub tests for `eval_registry_client.py` and each metric engine; one
   live-stack test running a real published suite end to end (asserting both an expected pass and
   an expected fail, per criterion 2).

## Testing strategy

- `eval_registry_client.py` tested against a stub Eval Registry HTTP server (real request/
  response shape, no live dependency) — same shape as every stub-based client test in this repo.
- Metric engines tested with fixed, deliberately-chosen input/actual_output/expected_output
  triples against a real `llm-gateway` (needs the live stack, since both engines' judge calls are
  real LLM calls) — one pair engineered to obviously pass, one to obviously fail.
- Live-stack test: real identity-service token → real `evals-runner-service` → a real suite
  fetched from real Eval Registry → real `llm-gateway` completions → real DeepEval + Ragas
  metric scores → persisted and read back via `GET /runs/{id}`.
- The TTL-boundary proof (done-when criterion 5) is a manual live-verification step during build,
  same as D-063/D-069/D-071 — not a committed automated test that waits out a real 5 minutes.

## Risks / watch-items

*(updated live with real findings during build)*

- Ragas's `langchain-community<0.4` pin coexists cleanly with the rest of the shared uv
  workspace — confirmed live, no workspace-root `override-dependencies` entry was needed the way
  D-075/D-076 needed one for crewai. A *third* crewai-vs-new-harness conflict did surface
  (deepeval's `posthog` pin vs. crewai's own unused `chromadb` dependency's `posthog` cap) and
  needed exactly that treatment (D-081).
- DeepEval's and Ragas's judge calls both go through `llm-gateway`, and each metric can trigger
  several sequential judge calls internally (GEval's evaluation-steps generation, Ragas's claim
  decomposition + NLI verification) — confirmed live a two-case, two-metric run takes several
  minutes against the local Ollama model, not something to assume fast. `llm-gateway`'s own
  upstream timeout (30s default) was too tight for this and had to be raised to 120s (D-082) —
  the first harness to hit that ceiling. `POST /runs` stays synchronous for v1 regardless (no
  suite bigger than a handful of cases has been tried); async/polling is a real follow-up once
  suite size grows.
- No suite was published in Eval Registry before this build started — publishing one first (step
  7) was genuinely on the critical path, confirmed by needing to do it before any live
  verification could proceed.
- Ragas's `FactualCorrectness` needs a substantive (sentence-length) reference, not a bare word —
  a real, live-caught finding (D-083), not anticipated in the original design. Eval suite authors
  need to know this when writing `expected_output` for a `factual_correctness`-graded case.
- DeepEval's and Ragas's metric *reasons* are genuinely informative in practice (verified live —
  GEval's reasons read as coherent, correct explanations); Ragas's `FactualCorrectness` never
  populates `reason` at all (confirmed from its own source, D-083) — that's normal for this
  metric, not a bug to chase.
- The autorater/system-under-test model split is currently the same model (`qwen3.8`) for both —
  a known self-preference-bias risk in LLM-as-judge setups — resolved (D-085): `judge_model` is
  now an independent field on `POST /runs` (defaulting to `model`'s same default,
  `settings.llm_gateway_model`, so same-model-for-both needs no change), threaded through both
  engines and persisted on the run, with a `GET /ui` page to set it without hand-writing JSON.
- The two autoraters occasionally disagree with each other on the same borderline case — caught
  live when the committed live-stack test failed once, non-repeatably, with `pass_count`/
  `fail_count` still correct but one engine's verdict flipped on a closely-worded comparison
  (D-084). Inherent to LLM-as-judge evaluation on a small local model, not a bug; the test now
  asserts at least one real judge call reaches the correct verdict per case, not unanimous
  agreement every run.
- Neither engine's judge calls had D-063's reactive-retry backstop — only `LlmGatewayTarget`
  (the system-under-test leg) did. A run whose judging phase alone took 400s+ (confirmed real
  across several runs, not hypothetical) crossed the 300s TTL *during* judging and got a real
  401 from inside a judge call, caught by re-running the full test suite after D-085's change
  (D-086). Fixed at the one call site both engines share (`main.py`'s `_run_metric`): retry the
  whole metric once with a force-refreshed token on an auth-shaped failure, rather than inside
  either engine individually — ragas's `.ascore()` is a black box that can issue several internal
  LLM calls this code never sees one at a time, so there's no single per-call hook to wrap there
  the way `LlmGatewayTarget`/DeepEval's own `a_generate` allow.
