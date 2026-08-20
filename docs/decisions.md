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
| Evals | **DeepEval** + Promptfoo (red-team) + Ragas (RAG) | Apache-2.0 / OSS | Integrate libs | Postgres |
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
