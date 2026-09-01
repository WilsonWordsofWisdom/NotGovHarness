# LLM Gateway Harness — Design

**Status:** design drafted, not yet built.
**Date:** 2026-09-01
**Wave:** 1 (Foundation) — resumed after being paused since 2026-08-21
**Branch:** `feat/wave1-llm-gateway`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-055..D-058) ·
[../../implementation-plan.md](../../implementation-plan.md) ·
[2026-08-31-agent-gateway-harness-design.md](2026-08-31-agent-gateway-harness-design.md) (the
façade-auth precedent this design reuses)

## Context

LiteLLM was chosen as the LLM Gateway anchor from the very start (D-003-adjacent planning) —
the single OpenAI-format model surface every LLM-dependent harness calls: Guardrails' Llama Guard
layer, Memory, Knowledge/RAG, Agent Builder, and the Evals runner's judge metrics. It's been
paused since 2026-08-21 waiting on GovTech Platform AI credentials.

Resumed now with a different shape than originally planned, after two real findings (D-055,
D-056):

1. GovTech Platform AI's API isn't publicly documented — checked directly, not assumed. Rather
   than stay blocked, this harness is built and verified against a local model first; GovTech
   becomes a second `model_list` entry once real credentials exist, with zero downstream changes
   (every caller talks to LiteLLM's OpenAI-compatible endpoint, never a backend directly).
2. The user already has a real, capable model (`qwen3.8:latest`, actually Qwen's `qwen35` family
   at 27.3B params) pulled via a natively-installed Ollama, already running on the host. LiteLLM
   points at it via `host.docker.internal:11434` rather than running a second, containerized
   Ollama that would re-download the model and lose native Metal GPU acceleration.

## Goal & success criteria

A caller gets a real chat completion through the full chain — bearer token → `llm-gateway` façade
→ LiteLLM → host Ollama → real model output — with spend/usage tracked, and the same mechanism
ready to take GovTech Platform AI as a second backend without any downstream harness changing.

Done when, live against the running stack:
1. A real bearer token (scope `llm_gateway:call`) gets a real chat completion back, not a stub.
2. The response is genuinely OpenAI-compatible (`choices[0].message.content` shape) — proving the
   façade → LiteLLM → Ollama chain round-trips correctly, not just that each hop individually
   responds to something.
3. LiteLLM's own Postgres records the request (spend/usage tracking) — confirmed by querying it
   directly, not just trusting the feature exists.
4. A caller without `llm_gateway:call` is rejected (403) — the façade's own gate, independent of
   whatever LiteLLM's virtual key would otherwise allow.
5. LiteLLM's master key and virtual key are never exposed to a caller of the façade — only the
   façade itself holds them, same shape as ContextForge's admin credential in Agent Gateway
   (D-043).

## Non-goals (YAGNI)

- GovTech Platform AI itself — documented as the next step once credentials exist, not built
  here. This design's whole point is not blocking on it.
- Streaming responses — a synchronous, complete-response call is enough to prove the mechanism;
  streaming can be added to the façade later without changing the backend wiring.
- Multi-model routing/fallback logic beyond what LiteLLM already provides out of the box — no
  custom routing rules for v1.
- A containerized fallback Ollama — documented as an option (D-056) for full compose
  reproducibility, not built now since it would only duplicate a model that already exists.

## Components

- **`ollama`** — not a new container; the host's already-running, natively-installed Ollama
  instance, reached via `host.docker.internal:11434`. No compose service, no volume, no pulled
  image — this is the one harness in this repo whose "infra" is explicitly outside `docker
  compose up`, disclosed as a real tradeoff (D-056), not hidden.
- **`litellm`** — `ghcr.io/berriai/litellm-database:v1.99.0` (D-057), Postgres `litellm` DB
  (db-per-service), `config.yaml` mounted with `qwen3.8` aliased in its `model_list` against the
  host Ollama backend. `LITELLM_MASTER_KEY` from local `.env` only, `${VAR}` substitution in
  compose — never a literal value in any committed file (standing instruction, not new here).
- **`llm-gateway`** — new façade service (D-058, same shape as `agent-gateway`): validates this
  platform's bearer tokens, forwards to LiteLLM using a backend-only virtual key the façade holds
  (minted once via LiteLLM's `/key/generate` against the master key, then held as a secret the
  same way ContextForge's admin password is held).
- **example-service** — gains scope `llm_gateway:call` (D-030 pattern).

## API (llm-gateway façade)

- `POST /chat/completions` (scope `llm_gateway:call`) — proxies to LiteLLM's own
  `/chat/completions`, OpenAI-compatible request/response shape passed through largely as-is.
  Deliberately named to match the OpenAI API surface other tooling already expects, not a
  platform-specific shape.

## Build order (dependency-ordered)

1. Confirm `host.docker.internal:11434` reachability from a container (already verified live,
   see D-056) and the target model's real latency profile (already verified: ~3.5s cold load,
   ~390ms warm eval).
2. `litellm` container + Postgres `litellm` DB + `config.yaml` — verify LiteLLM itself serves a
   real completion against the host Ollama backend, independent of any façade.
3. Mint a virtual key for the façade via LiteLLM's `/key/generate` (master-key-authenticated,
   one-time, held as a secret).
4. `services/llm-gateway/` façade service — `POST /chat/completions`, hybrid `auth_mode` +
   `require_scope`.
5. identity-service migration: `llm_gateway:call` → example-service.
6. Live verification: all 5 done-when criteria against the running stack.
7. Committed `pytest`: live-stack test exercising the full chain (real token → façade → LiteLLM →
   Ollama → real completion), scope-rejection test.

## Testing strategy

- No meaningful layer-level/unit split here the way Guardrails or Sandbox had — this harness is
  thin proxying, not independent logic worth isolating from the live chain. The live-stack test
  *is* the real test.
- Live-stack test: real identity-service token → real llm-gateway → real LiteLLM → real Ollama →
  real model output, plus the scope-rejection case.

## Risks / watch-items

*(updated live with real findings during build)*

- Host-level Ollama dependency (D-056) means this harness won't come up on a fresh clone/machine
  without the user separately installing and running Ollama with the right model pulled — worth
  a clear README/compose-comment call-out, not just buried in this spec.
- LiteLLM's `/key/generate` flow for minting the façade's backend virtual key is a one-time manual
  (or bootstrap-script) step, not automatic on container start — needs to be either documented as
  a manual step or scripted, decided during build.
