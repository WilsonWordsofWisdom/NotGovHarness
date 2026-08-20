# Architecture

NotGovHarness is a locally-runnable reference implementation of a complete agentic platform:
16 "harnesses" across 4 dependency layers, all built on a shared Phase 0 substrate.

See [decisions.md](decisions.md) for the rationale behind every choice below.

## Platform map (harnesses + chosen anchors)

```mermaid
graph TB
    subgraph L4["④ Agent Lifecycle"]
        AB["Agent Builder<br/><i>CrewAI</i>"]
        DP["Deployment Pipeline<br/><i>Docker + Temporal</i>"]
        EV["Evals<br/><i>DeepEval / Promptfoo / Ragas</i>"]
    end
    subgraph L3["③ Runtime & Policy"]
        MG["Agent Gateway<br/><i>ContextForge (A2A+MCP+Registry)</i>"]
        GR["Guardrails<br/><i>LLM Guard / NeMo / Guardrails AI / Llama Guard</i>"]
        MEM["Memory<br/><i>Mem0 (pgvector/Qdrant)</i>"]
        RAG["Knowledge / RAG<br/><i>LlamaIndex + reranker</i>"]
        SBX["Sandbox<br/><i>E2B (isolated compute)</i>"]
        HITL["Approvals / HITL<br/><i>Temporal signals</i>"]
    end
    subgraph L2["② Catalog & Registries"]
        AR["Agent Registry<br/><i>A2A Agent Cards (signed)</i>"]
        SR["Skill Registry<br/><i>Agent Skills (SKILL.md)</i>"]
        ER["Eval Registry<br/><i>eval suites (versioned)</i>"]
    end
    subgraph L1["① Foundation & Cross-cutting"]
        ID["Agent Identity<br/><i>SPIFFE/SPIRE + OAuth2</i>"]
        GW["LLM Gateway<br/><i>LiteLLM</i>"]
        OBS["Observability<br/><i>Langfuse + OTel</i>"]
        AUD["Audit plane<br/><i>hash-chained event log</i>"]
    end

    AB --> AR & SR & MG & ER
    AB --> GW & MEM & GR & RAG & SBX & HITL
    DP --> AR
    DP -->|gate| EV
    EV --> GW & OBS & ER
    RAG --> GW
    AUD -.consumes events.- OBS
    L3 --> ID
    L2 --> ID
    L4 --> OBS

    L1 --> SUB
    L2 --> SUB
    L3 --> SUB
    L4 --> SUB

    subgraph SUB["Phase 0 — Platform Substrate (built now)"]
        direction LR
        EDGE["Edge<br/>Traefik"]
        KIT["Service Kit<br/>platform-core"]
        CON["Contracts<br/>OpenAPI + codegen"]
        PER["Persistence<br/>db-per-service"]
        EVT["Events<br/>Redpanda (Kafka)"]
        OT["Obs baseline<br/>OTel + Jaeger"]
    end
```

## Engine map

| Engine | Owning services | Started in compose |
|---|---|---|
| Postgres | LiteLLM, Builder, Deploy (Temporal), Agent Registry, Identity authz, Guardrails, Evals, ContextForge, Sandbox jobs, Approvals, Audit (hash-chained) | Phase 0 (shared container, db-per-service) |
| ClickHouse + Redis + MinIO | Observability (Langfuse) | when Observability is built |
| MinIO (object store) | Skill Registry bundles, Eval Registry datasets (shared object store) | when owning harness is built |
| Vector (pgvector / Qdrant) | Memory, Knowledge/RAG | when owning harness is built |
| Temporal datastore | Deployment Pipeline orchestration | when Deployment is built |
| SPIRE datastore | Agent Identity | when Identity is built |

Compose uses **profiles** so only the engines a given active service needs are started.

## Interconnect — connectors & surfaces

How the harnesses speak to one another. Full visual reference (control + data plane,
surfaces table, cross-cutting planes): the "Connectors & Surfaces" artifact.

**Connectors (protocols on the wire):** `REST/OpenAPI` (default, generated clients) ·
`OpenAI API` (→ LLM Gateway) · `MCP` (→ MCP Gateway → servers) · `A2A` (agent↔agent, signed
Agent Cards) · `SPIFFE Workload API` + `OAuth2` (Identity) · `OTLP` (→ Observability) ·
`Kafka` (async events) · `S3` (bundles/artifacts).

**Data plane — one request through a running agent:**

```mermaid
sequenceDiagram
    participant C as Caller
    participant ID as Identity
    participant RT as Agent Runtime
    participant GR as Guardrails
    participant MEM as Memory
    participant RAG as Knowledge/RAG
    participant GW as LLM Gateway
    participant MG as Agent Gateway
    participant SBX as Sandbox
    participant HITL as Approvals
    C->>ID: get scoped token (OAuth2)
    C->>RT: invoke task (A2A, mTLS/SVID)
    RT->>GR: check(input) (REST)
    RT->>MEM: retrieve memory (REST)
    RT->>RAG: retrieve grounding (REST, provenance)
    loop reason / act
        RT->>GW: chat/completion (OpenAI API)
        RT->>MG: call tool via Agent Gateway (MCP/A2A)
        RT->>SBX: run code (isolated, E2B)
        RT->>HITL: approve risky action? (Temporal signal)
    end
    RT->>GR: check(output) (REST)
    RT->>MEM: persist (REST)
    RT->>C: result (A2A)
    Note over C,MG: every hop: mTLS (SPIFFE) + scoped token (OAuth2); all spans -> OTLP -> Observability
```

**Control plane — build & deploy:**

```mermaid
flowchart LR
    REG["Registries<br/>Agent · Skill · MCP"] -->|read, REST| AB["Agent Builder<br/>CrewAI"]
    AB -->|publish signed Agent Card, REST| REG
    AB --> DP["Deployment Pipeline<br/>Temporal + Docker"]
    DP -->|read card, REST| REG
    DP -->|deploy events, Kafka| EVT[(Redpanda)]
    DP --> RT["Agent Runtime<br/>live A2A endpoint"]
```

## Phase 0 substrate

The substrate is the only thing built in the first cycle. Every harness plugs into it. Two
service shapes are supported by the shared kit:

- **Greenfield service** — a FastAPI service owning its data (e.g. Agent Registry, Skill Registry).
- **Façade / adapter service** — wraps an upstream OSS project (LiteLLM, ContextForge, Langfuse,
  Temporal) behind the platform's OpenAPI contract + identity + OTel + events.

Detailed substrate design: see the Phase 0 spec in
[superpowers/specs/](superpowers/specs/).
