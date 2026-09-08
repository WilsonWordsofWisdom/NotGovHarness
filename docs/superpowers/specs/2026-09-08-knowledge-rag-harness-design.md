# Knowledge/RAG Harness — Design

**Status:** design drafted, not yet built.
**Date:** 2026-09-08
**Wave:** 3 (Runtime & Policy) — completes the wave
**Branch:** `feat/wave3-knowledge-rag`

Related: [../../architecture.md](../../architecture.md) ·
[../../decisions.md](../../decisions.md) (D-064..D-068) ·
[../../implementation-plan.md](../../implementation-plan.md) ·
[2026-09-08-memory-harness-design.md](2026-09-08-memory-harness-design.md) (pgvector-on-shared-
Postgres and `llm-gateway`-calling patterns this harness reuses directly)

## Context

D-013: "Memory (Mem0) is not RAG. Add a governed, provenance-aware agentic retrieval service —
hybrid search (BM25 + dense) + cross-encoder reranker + re-ask, permission-aware ingestion."
Framework: LlamaIndex. Blocked on LLM Gateway since Wave 1; unblocked now.

`llama-index-core` and its retrieval/rerank/vector-store integrations were installed and the
actual retrieval pipeline proven live (against a real, throwaway pgvector database and the real
running `llm-gateway`) before designing anything, same discipline as every prior harness. Four
real findings (D-064..D-067):

- No default telemetry in `llama-index-core` — checked, not assumed, same class of check that
  failed for the last two libraries (Guardrails AI, Mem0), passed this time.
- "Hybrid search" is `PGVectorStore`'s own `hybrid_search=True` (dense + Postgres full-text),
  not a separate BM25 index — verified live that a query sharing zero words with the correct
  document still ranked it first.
- The reranker is FastEmbed's `TextCrossEncoder`, not `FlagEmbedding` — the "obvious" LlamaIndex
  integration pulls in the full torch/transformers/sentence-transformers stack (multiple GB) and
  risks re-triggering D-052's `transformers<5` conflict. FastEmbed (already a dependency) ships a
  small (~80MB) cross-encoder that does the same job, verified live to rank correctly.
- `TokenClient` (Memory's fetch-cache-refresh-plus-reactive-retry auth client, D-062/D-063) moves
  to `platform_core` — Knowledge/RAG needs the exact same shape of call into `llm-gateway`, and
  D-062 explicitly named this as the moment to stop re-deriving it per-harness.

## Goal & success criteria

A caller ingests a document; a later query retrieves genuinely relevant grounding via hybrid
search + reranking, generates an answer through `llm-gateway`, and returns which source
document(s) backed that answer — with a minimal, real permission model (D-068), not full RBAC.

Done when, live against the running stack:
1. A real bearer token (scope `knowledge:write`) ingests a document; it's chunked, embedded, and
   stored in pgvector, with the original raw text stored in MinIO and its own metadata row in
   Postgres (title, `ingested_by`, `visibility`).
2. A query using different wording than the document's own text still retrieves it — proving
   real hybrid search, not exact-match.
3. The reranker's ordering is observably different from raw hybrid-search ordering on at least
   one query where they'd disagree — proving the rerank step is genuinely doing something, not a
   no-op pass-through.
4. A generated answer is grounded in the retrieved document (traceable to its content, not a
   hallucinated tangent) and the response includes the source document's id — real provenance,
   not just a generated string.
5. A document ingested as `visibility: private` by one caller does not appear in another
   caller's query results; the same caller's own private documents do.
6. A caller without `knowledge:write` can still query but is rejected on ingest.

## Non-goals (YAGNI)

- "Re-ask" (D-013 names this) — a self-correcting retry loop that reformulates the query when the
  first retrieval/answer looks weak. Real complexity (needs a confidence signal and a retry
  budget) without a clear v1 need; documented as a follow-up, not silently dropped.
- Full RBAC / per-document ACLs beyond the binary `shared`/`private` + `ingested_by` model
  (D-068) — a reference-scale permission model, not a production access-control system.
- Non-text documents (PDFs, images) — plain text/markdown ingestion only for v1. LlamaIndex has
  readers for richer formats; adding one is a follow-up, not a redesign.
- Streaming generation — a synchronous, complete response is enough to prove the mechanism.

## Components

- **`services/knowledge-service/`** — new greenfield service, Postgres `knowledge` DB
  (+ `knowledge_test`): a `documents` table this service *does* own (id, title, `ingested_by`,
  `visibility`, `minio_object_key`, `created_at`) — unlike Mem0, LlamaIndex's `PGVectorStore`
  only owns the chunk/embedding table, not document-level metadata, so this service has its own
  small schema too.
- **`platform_core.llm_gateway_auth.TokenClient`** (D-067) — moved from `mem0_service`, used
  by both `mem0-service` and `knowledge-service` now.
- **`fastembed_reranker.py`** — a small custom `BaseNodePostprocessor` wrapping FastEmbed's
  `TextCrossEncoder` (D-066) — the glue LlamaIndex doesn't ship.
- **pgvector on the shared Postgres** — already available (D-060); `knowledge-service` gets its
  own database, same as every other db-per-service harness.
- **MinIO** — reuses `platform_core.objectstore` (D-034) for the original raw document text,
  same pattern as Skill/Eval Registry.
- **`llm-gateway`** — generation via `OpenAILike`, `api_key` refreshed the same way Mem0's is
  (`TokenClient` + reactive retry on an auth-shaped failure).
- **`knowledge-service`'s own OAuth2 client** — same shape as `mem0-service` (D-062): a real
  platform service calling another, not a borrowed simulated principal.
- **example-service** — gains scope `knowledge:write` (D-030 pattern).

## API (knowledge-service)

- `POST /documents` (scope `knowledge:write`) — `{title, text, visibility: "shared" | "private"}`
  → chunks + embeds + stores in pgvector, stores raw text in MinIO, records metadata in Postgres.
- `POST /query` (any authenticated caller) — `{question, top_k?}` → hybrid retrieve (filtered to
  `shared` documents plus the caller's own `private` ones) → rerank → generate via `llm-gateway`
  → `{answer, sources: [{document_id, title, chunk_text}]}`.
- `GET /documents` (any authenticated caller) — list documents visible to the caller (same
  visibility filter as query).

## Storage

- Postgres `knowledge`: `documents` table (this service's own, Alembic-managed) +
  `PGVectorStore`'s own chunk/embedding table (LlamaIndex-managed, same "we provision the
  database, a library owns its own table in it" shape as Mem0).
- MinIO bucket `knowledge`: original raw document text, keyed by document id.

## Build order (dependency-ordered)

1. Promote `TokenClient` to `platform_core.llm_gateway_auth`; update `mem0-service` to import it;
   confirm `mem0-service`'s existing tests still pass unchanged (a refactor, not a behavior
   change).
2. `services/knowledge-service/` scaffold + `documents` table migration + pgvector-extension
   migration (same shape as Memory's, but this service also owns an application table).
3. `fastembed_reranker.py` — unit-tested directly against FastEmbed's real cross-encoder (no
   live-stack dependency), same "test against the real thing" posture as every prior harness's
   layer-level tests.
4. `main.py`: `POST /documents`, `POST /query`, `GET /documents` — hybrid retrieve + rerank +
   generate + MinIO + Postgres, hybrid `auth_mode` + `require_scope`.
5. identity-service migrations: new `knowledge-service` OAuth2 client (`llm_gateway:call`);
   `knowledge:write` → example-service.
6. Live verification: all 6 done-when criteria against the running stack.
7. Committed `pytest`: reranker unit test (real FastEmbed, no live-stack dependency); live-stack
   test for ingest → query → provenance → visibility filtering.

## Testing strategy

- `fastembed_reranker.py` tested directly against the real `TextCrossEncoder` — a mock would
  assert intent, not that reranking actually reorders candidates correctly.
- Live-stack test: real identity-service tokens (two different callers, to test visibility
  filtering for real) → real `knowledge-service` → real pgvector hybrid search → real rerank →
  real `llm-gateway` generation → real MinIO-backed provenance.

## Risks / watch-items

*(updated live with real findings during build)*

- `PGVectorStore`'s `hybrid_search` full-text side uses Postgres's `text_search_config` (default
  `"english"`) — worth confirming this doesn't silently misbehave on non-English content, though
  out of scope to fully solve for v1.
- The `TokenClient` promotion to `platform_core` touches a service (`mem0-service`) that's
  already merged and live — verified via its own existing test suite passing unchanged, not just
  "looks like a pure move."
- FastEmbed's reranker model (`Xenova/ms-marco-MiniLM-L-6-v2`) needs the same "pre-warm at
  startup, not first request" treatment as every other local-model dependency in this repo
  (Sandbox's image, Guardrails' spaCy model, Memory's embedder).
