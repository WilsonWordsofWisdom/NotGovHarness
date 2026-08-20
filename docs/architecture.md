# Architecture

NotGovHarness is a locally-runnable reference implementation of a complete agentic platform:
12 "harnesses" across 4 dependency layers, all built on a shared Phase 0 substrate.

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
        MG["MCP Gateway + Registry<br/><i>IBM ContextForge</i>"]
        GR["Guardrails<br/><i>LLM Guard / NeMo / Guardrails AI / Llama Guard</i>"]
        MEM["Memory<br/><i>Mem0 (pgvector/Qdrant)</i>"]
    end
    subgraph L2["② Catalog & Registries"]
        AR["Agent Registry<br/><i>A2A Agent Cards (signed)</i>"]
        SR["Skill Registry<br/><i>Agent Skills (SKILL.md)</i>"]
    end
    subgraph L1["① Foundation & Cross-cutting"]
        ID["Agent Identity<br/><i>SPIFFE/SPIRE + OAuth2</i>"]
        GW["LLM Gateway<br/><i>LiteLLM</i>"]
        OBS["Observability<br/><i>Langfuse + OTel</i>"]
    end

    AB --> AR & SR & MG
    AB --> GW & MEM & GR
    DP --> AR
    EV --> GW & OBS
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
| Postgres | LiteLLM, Builder, Deploy (Temporal), Agent Registry, Identity authz, Guardrails, Evals, ContextForge | Phase 0 (shared container, db-per-service) |
| ClickHouse + Redis + MinIO | Observability (Langfuse) | when Observability is built |
| MinIO (object store) | Skill Registry bundles (shared object store) | when Skill Registry is built |
| Vector (pgvector / Qdrant) | Memory | when Memory is built |
| Temporal datastore | Deployment Pipeline orchestration | when Deployment is built |
| SPIRE datastore | Agent Identity | when Identity is built |

Compose uses **profiles** so only the engines a given active service needs are started.

## Phase 0 substrate

The substrate is the only thing built in the first cycle. Every harness plugs into it. Two
service shapes are supported by the shared kit:

- **Greenfield service** — a FastAPI service owning its data (e.g. Agent Registry, Skill Registry).
- **Façade / adapter service** — wraps an upstream OSS project (LiteLLM, ContextForge, Langfuse,
  Temporal) behind the platform's OpenAPI contract + identity + OTel + events.

Detailed substrate design: see the Phase 0 spec in
[superpowers/specs/](superpowers/specs/).
