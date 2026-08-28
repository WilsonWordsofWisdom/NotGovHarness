# Decisions Log

Append-only log of key business & technical decisions and *why*. Newest first.

---

## 2026-08-21 — Deliverable, fidelity, and stack

**D-001 — Deliverable is working reference services.**
The platform is a set of *working* reference services (real APIs + backing stores), not
scaffolds, test rigs, or docs-only. **Why:** the goal is a functioning demonstration of a
complete agentic platform, not a paper design.

**D-002 — Fidelity: local reference platform.**
Runs on one machine via `docker-compose` + a monorepo. Real services and APIs, lightweight
backing. Not production cloud, not single-node-prod. **Why:** maximizes coherence and
runnability of the *whole* platform over the depth of any one service.

**D-003 — Language/stack: Python + FastAPI.**
`uv` workspace monorepo. **Why:** strongest ecosystem for LLM/agent/eval tooling; most chosen
OSS integrations (LiteLLM, ContextForge, Langfuse SDK, DeepEval, Mem0) are Python-first.

**D-004 — Persistence: db-per-service ownership, per-service best-fit engine.**
Each service owns its data behind its API; the engine is chosen per service, not globally.
**Why:** these subsystems have genuinely different data shapes; db-per-service keeps the
engine swappable without breaking consumers.

**D-005 — Inter-service comms: REST now + event backbone (Kafka via Redpanda).**
Synchronous REST between services, plus a real async event backbone from day one.
**Why:** Observability and Deployment events are event-shaped; baking the backbone in avoids
a later rework.

**D-006 — Contracts: OpenAPI-first codegen.**
Each service is the source of truth for its own OpenAPI contract; typed clients are generated
from it. Services consume each other only through generated clients. **Why:** enforces the API
boundary that db-per-service ownership implies.

**D-007 — Observability baseline from day one: OpenTelemetry + Jaeger.**
The service template auto-instruments with OTel (OTLP); compose ships an OTel Collector +
Jaeger. **Why:** the Observability harness (Langfuse) builds on this baseline instead of
inventing a parallel path; OTel GenAI semconv is the 2026 vendor-neutral standard.

---

## 2026-08-21 — Locked harness stack (integrate-first)

Guiding principle: **integrate mature OSS where it exists; build only to open standards where
it doesn't.** Most harnesses become "run it + wrap it behind our contract + wire identity,
events, tracing" (a *façade service*), which lowers total effort.

| Harness | Anchor | License | Posture | Storage |
|---|---|---|---|---|
| LLM Gateway | **LiteLLM** | MIT | Integrate | Postgres |
| Agent Builder | **CrewAI** (runtime) | MIT | Integrate | Postgres |
| Deployment Pipeline | **Docker packaging + Temporal**; kagent = documented k8s prod path | MIT/Apache-2.0 | Build + integrate | Postgres (Temporal) |
| Agent Registry | **A2A Agent Cards** (signed), Linux Foundation | Open | Build to standard | Postgres |
| Agent Identity | **SPIFFE/SPIRE + OAuth2 client-credentials** | Apache-2.0 | Integrate SPIRE, build authz | Postgres (+ SPIRE datastore) |
| Skill Registry | **Agent Skills standard (`SKILL.md`, agentskills.io)** | Open | Build to standard | Postgres + MinIO |
| MCP Gateway **+** Registry (merged) | **IBM ContextForge** (Python/FastAPI) | Apache-2.0 | Integrate | Postgres |
| Observability | **Langfuse** + OTel GenAI semconv + OpenInference/OpenLLMetry | MIT | Integrate | ClickHouse + Redis + MinIO |
| Guardrails | Layered: **LLM Guard → NeMo Guardrails → Guardrails AI → Llama Guard** | Apache-2.0 / OSS | Integrate libs | Postgres |
| Evals (runner) | **DeepEval** + Promptfoo (red-team) + Ragas (RAG) | Apache-2.0 / OSS | Integrate libs | Postgres |
| Eval Registry | Build (suite catalog) — see D-011 | Open | Build | Postgres + MinIO |
| Memory | **Mem0** (default); Zep/Graphiti = temporal-KG alt | Apache-2.0 | Integrate | Vector (pgvector/Qdrant) |

**D-008 — MCP Gateway and Registry are merged onto ContextForge.**
One Apache-2.0, Python-native service provides both MCP federation/proxy and the registry.
**Why:** ContextForge covers both; fewest moving parts; best fit for the Python monorepo. The
official `modelcontextprotocol/registry` remains a documented split-out option.

**D-009 — Deployment Pipeline avoids Kubernetes for the reference.**
Container packaging + Temporal for durable orchestration (both run in compose). kagent is the
documented Kubernetes production upgrade; the packaging unit stays a container so the door to
kagent stays open. **Why:** kagent would drag k8s into the whole platform, against D-002.

**D-010 — Identity auth seam targets SPIFFE/SPIRE + OAuth2.**
The scaffold ships a *pluggable auth dependency stub* shaped to be replaced by SPIFFE SVIDs +
OAuth2 client-credentials. **Why:** A2A Agent Cards are signed and agents are non-human
identities calling other agents/tools; SPIFFE is the 2026 de-facto workload-identity standard.

**Engine-map consequence:** "Postgres everywhere" does not hold. Langfuse brings
ClickHouse + Redis + MinIO; Skill Registry needs object storage; Memory needs a vector store;
Temporal and SPIRE bring their own stores. This validates D-004 (per-service best-fit engines).

---

## 2026-08-21 — Evals design

**D-011 — Eval Registry is a separate (13th) harness in the Catalog layer.**
Eval **suites** (`{ dataset, metrics, thresholds }`) are first-class, versioned, reusable
artifacts cataloged by a dedicated **Eval Registry**, parallel to the Agent/Skill/MCP
registries. The **Evals runner** (DeepEval/Ragas/Promptfoo) stays in the Lifecycle layer and
*consumes* the registry. **Why:** suites like "tool-use safety" or "RAG faithfulness" are
authored once and referenced by many agents; a shared catalog prevents per-agent duplication.
Storage: Postgres (suite metadata/versions) + MinIO (datasets/golden sets). Platform is now
**12 harnesses** (original 12 − merged MCP gateway/registry = 11, + Eval Registry = 12; an
earlier note miscounted this as 13).

**D-012 — Evals are produced by capability-baseline + drafted, human-reviewed cases.**
At build, the Builder maps the Agent Card's declared capabilities (`AgentSkill`s, tools, RAG
usage) to a **baseline metric pack** pulled from the Eval Registry (uses tools → Tool
Correctness; RAG → Ragas faithfulness; always → a safety/red-team pack). An LLM additionally
**drafts** scenario cases from the use-case description; a human **reviews** them before they
gate. Not blind full auto-generation. **Why:** an unreviewed auto-generated suite can't be
trusted as a deploy gate ("who evaluates the evaluator").

**Runner behavior:** drives the target agent through the normal data-plane path (A2A invoke),
captures output **and the distributed trace from Observability** (to score *how* it worked, not
just the answer), scores via the engines (LLM-judge metrics call the LLM Gateway), persists
results to Postgres. Runs offline, as a **deploy gate** (Temporal step in the Deployment
Pipeline), and **online** (sampling live production traces from Observability).

**New connector edges:** Builder → Eval Registry (compose baseline config) · Builder → Evals
(optional draft) · Deployment Pipeline → Evals (gate before promote) · Evals → Eval Registry
(fetch suites) · Evals → Observability (pull traces) · Evals → LLM Gateway (judge metrics).

---

## 2026-08-21 — Industry gap-analysis refinements (→ 16 harnesses)

Compared our architecture against the 2026 reference-architecture consensus (six horizontal
layers + two vertical rails: Observability&Eval, Governance&Security; core capabilities Agent
Identity / Agent Gateway / Agent Registry; the "7 patterns"). We are ahead on identity, evals,
and platform discipline; the gaps below were real must-haves. Adopting all four additions +
elevating ContextForge takes us from 12 to **16 harnesses**.

**D-013 — Add a Knowledge / RAG harness (grounding layer).** Memory (Mem0) is not RAG. Add a
governed, provenance-aware **agentic retrieval** service — hybrid search (BM25 + dense) +
cross-encoder reranker + re-ask, permission-aware ingestion. Framework: **LlamaIndex** (or
Haystack). Layer ③ Runtime. Storage: pgvector/Qdrant (vectors) + MinIO (source docs). **Why:**
the literature's named grounding layer; closes the loop with Evals' groundedness metrics.

**D-014 — Add a Sandbox / execution harness.** Isolated compute for agent-generated code/tool
execution. Framework: **E2B** (OSS, self-hostable, network-isolated by default, CrewAI
integration). Layer ③ Runtime. Storage: Postgres (job metadata); execution in ephemeral
microVMs. **Why:** a core reference layer; contains runaway/unsafe agent code.

**D-015 — Add a Human-in-the-Loop / Approvals harness.** approve / edit / reject gates on risky
actions and deploys. Mechanism: **Temporal signals** (already in stack) as primary; HumanLayer
optional. Layer ③ Runtime. Storage: Postgres (pending approvals). **Why:** named must-have
pattern; ties cleanly to our Temporal-based Deployment Pipeline.

**D-016 — Add an Audit plane.** A tamper-evident, compliance-grade audit trail consuming the
Kafka event stream (hash-chained, append-only). Layer ① Foundation, cross-cutting like
Observability. Storage: Postgres (hash-chained) + optional MinIO WORM. **Why:** one of the 7
patterns; observability alone isn't a compliance record.

**D-017 — Elevate ContextForge to the Agent Gateway role.** ContextForge already federates
A2A + MCP + REST/gRPC, so it becomes the single **agent-level control point** (agent↔agent and
agent↔tool traffic, policy, cost, audit), not just an MCP gateway. Harness renamed **Agent
Gateway** (still includes the MCP registry). **CrewAI stays** the orchestration framework;
LangGraph noted as a future option for stateful/HITL-heavy agents but not adopted now.

**New connector edges:** Agent Runtime → Knowledge/RAG (REST, retrieve) · Agent Runtime →
Sandbox (execute code) · Agent Runtime → Approvals (request/await decision; Temporal signal) ·
Knowledge/RAG → LLM Gateway (embeddings) + object store · all services → Audit (Kafka) ·
Agent Runtime → Agent Gateway → MCP servers / other agents (A2A).

**Final layer map (16):** ④ Lifecycle: Builder, Deployment, Evals. ③ Runtime: Agent Gateway,
Guardrails, Memory, Knowledge/RAG, Sandbox, Approvals. ② Catalog: Agent/Skill/Eval Registries.
① Foundation: Identity, LLM Gateway, Observability, Audit.

---

## 2026-08-23 — Agent Identity harness design (Wave 1)

Full design: [superpowers/specs/2026-08-23-agent-identity-harness-design.md](superpowers/specs/2026-08-23-agent-identity-harness-design.md).

**D-018 — Identity = standards base + AIP-shaped delegation claims.** Refines D-010 after research
(CNCF 2026, IETF WIMSE, RFC 8693, AIP draft-00, the MCP/A2A governance-gap findings). Adopt the
stable spine — **SPIFFE/SPIRE** (X.509 SVIDs) + **OAuth2 client-credentials** + **JWKS** +
**SVID-authenticated token endpoint** — and add **RFC 8693 token exchange** for delegation
(subject+actor → `act`-claim JWT), because plain client-credentials cannot express an agent acting
on-behalf-of a principal. Token claims are **AIP/IBCT-shaped**: `mode` (delegated vs autonomous),
`prov` (provenance for Audit), and `depth` with a **max-delegation-depth** enforced at verify (the
check AIP's adversarial tests uniquely caught). **Why:** genuinely agent-grade delegation on stable,
well-tooled standards, without betting the reference on a draft-00 wire format.

**D-019 — Cutting-edge mechanisms are documented seams, not built.** Biscuit (offline attenuation /
multi-hop), the full AIP wire format, OAuth Transaction Tokens, **OPA** (policy — inline scopes now,
OPA-ready seam), cross-org federation, and token refresh/revocation are explicitly out of scope.
JWT-SVIDs noted as the path for ephemeral agent workloads (we use X.509-SVIDs for long-lived
services). **Why:** bound the harness; keep upgrade paths open.

**D-020 — Build split around the Docker prerequisite.** `identity-service` (OAuth2 + token exchange
+ claim model) and the `platform-core` `oauth2`/`svid` changes build and unit-test on the current
stack; the **SPIRE server/agent + mTLS** integration is gated on the Docker Engine upgrade. **Why:**
SPIRE agents/attestors hit the old-Docker seccomp/thread wall (see Phase 0 learnings).

---

## 2026-08-27 — Observability harness design (Wave 1, built ahead of Identity)

Full design: [superpowers/specs/2026-08-27-observability-harness-design.md](superpowers/specs/2026-08-27-observability-harness-design.md).

**D-021 — Observability is built before Identity, out of the plan's stated order.** The Wave 1
order in `implementation-plan.md` is Identity → LLM Gateway → Observability → Audit, with Identity
called out as build-first. Built out of order by explicit choice. **Why it's viable:** Observability
has no hard dependency on Identity — it fans the existing Phase 0 OTel baseline (D-007) out to a
second exporter (Langfuse), it doesn't touch `platform-core`'s auth layer. **Consequence:** traces
won't carry `spiffe_id`/`act`-chain attributes until Identity lands; accepted as expected, not a
gap to route around.

**D-022 — Observability adds zero application code; it's an infra-only harness.** Langfuse is
wired as a second OTLP exporter on the existing `otel-collector` pipeline
(`otlphttp/langfuse` alongside `otlp/jaeger`), not via the `langfuse` Python SDK inside services.
**Why:** services already export OTLP to one collector endpoint (`platform_core.otel`); duplicating
that with manual SDK instrumentation would violate D-007's "builds on this baseline instead of
duplicating" and add a dependency every future service would need to remember.

**D-023 — ClickHouse and MinIO are their own reusable compose profiles, not bundled into
`observability`.** `clickhouse` and `objectstore` profiles stand alone; `langfuse-web`/`worker`/
`redis` are the `observability` profile proper, composed on top. **Why:** the compose file's own
header comment already anticipated this split, and later waves plausibly reuse both (Wave 2
registries want MinIO buckets; ClickHouse is a plausible fit for other analytics-shaped stores) —
bundling them into one Langfuse-specific profile would mean re-deriving the split later.

---

## 2026-08-28 — Agent Identity harness built (Wave 1, steps 1-5 complete)

Full design: [superpowers/specs/2026-08-23-agent-identity-harness-design.md](superpowers/specs/2026-08-23-agent-identity-harness-design.md).
All 6 done-when criteria verified live, end to end, on `feat/wave1-agent-identity` (not yet
merged). Real bugs found building SPIRE + mTLS are in that spec's risk section, not repeated here
— these three are the actual design decisions.

**D-024 — Server-side peer-SPIFFE-ID verification is a TLS-layer-only, non-goal for app code.**
upstream-stub requires and validates a client cert against the SPIRE trust bundle
(`ssl_cert_reqs=CERT_REQUIRED`) — real mutual authentication, only trust-domain-attested workloads
can connect — but doesn't extract or assert the caller's *specific* SPIFFE ID inside app code.
**Why:** uvicorn doesn't expose a client's peer certificate to ASGI apps through its public
interface; every path to it (`request.scope["transport"].get_extra_info("ssl_object")`) reaches
past that into undocumented internals a minor uvicorn upgrade could silently break. The client
side doesn't have this problem — httpx's `network_stream`/`ssl_object` extension is documented and
was verified working (including a `getpeercert(True)` positional-arg quirk) — so
`UpstreamClient` verifies the *upstream's* identity fully; only the reverse direction is
transport-layer-only.

**D-025 — A third `auth_mode`, `"hybrid"`: verify a Bearer token when present, fall back to
`dev`'s header behavior when absent.** upstream-stub needs to keep working under `core` alone (no
identity-service) while also accepting real delegated tokens once one exists — a hard
`auth_mode="oauth2"` switch would have broken Phase 0's already-verified plain-HTTP baseline.
**Why:** `require_scope()` on top of `hybrid`'s fallback path would silently 403 every
unauthenticated call (dev-mode `CallerIdentity` never carries scopes) — scope is only checked when
`identity.mode == "delegated"`, which is what actually distinguishes a verified token from no
token, not the presence of scopes.

**D-026 — Identity is built on its own branch, not directly on `main`.** Two commits (token core,
token exchange) had already landed on `main` before this was decided; they were moved to
`feat/wave1-agent-identity` via two `git revert` commits on `main` plus a fresh branch carrying the
originals — not a `reset` + force-push. **Why:** the two commits were already pushed to
`origin/main`; rewriting shared history there needs an explicit, deliberate choice, not a default.
