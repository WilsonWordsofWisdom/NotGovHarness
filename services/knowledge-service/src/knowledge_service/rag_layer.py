"""The retrieval-augmented-generation pipeline: hybrid search (D-065) + FastEmbed rerank (D-066)
+ generation via llm-gateway (D-062/D-067), with a minimal permission filter (D-068).

Construction is lazy (`_ensure_ready`, built on first real use in a threadpool), not done at
`RagLayer.__init__` — `PGVectorStore` connects to Postgres and `FastEmbedEmbedding`/
`FastEmbedRerank` load their ONNX models eagerly, so doing this at service-startup/import time
would make even `GET /healthz` depend on a live database and model downloads, same lesson as
Memory's `Mem0Layer`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from llama_index.core import Document, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.embeddings.fastembed import FastEmbedEmbedding
from llama_index.llms.openai_like import OpenAILike
from llama_index.vector_stores.postgres import PGVectorStore
from starlette.concurrency import run_in_threadpool

from platform_core.llm_gateway_auth import TokenClient, call_with_auth_retry

from .config import Settings
from .fastembed_reranker import FastEmbedRerank

_SYSTEM_PROMPT = (
    "Answer the question using only the context below. If the context doesn't contain the "
    "answer, say you don't know — do not make anything up.\n\nContext:\n{context}\n\n"
    "Question: {question}"
)


@dataclass
class RagAnswer:
    answer: str
    source_document_ids: list[str]


class RagLayer:
    def __init__(self, settings: Settings, token_client: TokenClient) -> None:
        self._settings = settings
        self._token_client = token_client
        self._index: VectorStoreIndex | None = None
        self._reranker: FastEmbedRerank | None = None
        self._llm: OpenAILike | None = None

    def _build(self) -> tuple[VectorStoreIndex, FastEmbedRerank, OpenAILike]:
        embed_model = FastEmbedEmbedding(model_name=self._settings.embedding_model)
        vector_store = PGVectorStore.from_params(
            host=self._settings.pg_host,
            port=str(self._settings.pg_port),
            database=self._settings.pg_dbname,
            user=self._settings.pg_user,
            password=self._settings.pg_password,
            table_name=self._settings.pg_table_name,
            embed_dim=self._settings.embedding_dims,
            hybrid_search=True,
        )
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex.from_vector_store(
            vector_store, embed_model=embed_model, storage_context=storage_context
        )
        reranker = FastEmbedRerank(
            model_name=self._settings.reranker_model, top_n=self._settings.top_k_rerank
        )
        llm = OpenAILike(
            model=self._settings.llm_gateway_model,
            api_base=self._settings.llm_gateway_url,
            # Placeholder — overwritten before every generate call via call_with_auth_retry's
            # apply_token callback (same pattern as Mem0Layer, D-062).
            api_key="unset",
            is_chat_model=True,
            context_window=8192,
            # Real bug found live: OpenAI/OpenAILike caches its underlying SyncOpenAI client on
            # first use (reuse_client=True is the default) — that cached client captures
            # `api_key` at construction time only, so mutating `llm.api_key` afterward (this
            # harness's whole token-refresh mechanism, D-062) was silently ignored. Every request
            # kept using the token from the very first call until it expired, then failed
            # forever, "signature has expired," with no recovery — because the reactive retry
            # also just mutates `llm.api_key` and re-hits the same stale cached client.
            # reuse_client=False makes `_get_client()` build a fresh SyncOpenAI from the current
            # `api_key` on every call.
            reuse_client=False,
        )
        return index, reranker, llm

    async def _ensure_ready(self) -> tuple[VectorStoreIndex, FastEmbedRerank, OpenAILike]:
        if self._index is None:
            self._index, self._reranker, self._llm = await run_in_threadpool(self._build)
        assert self._index is not None
        assert self._reranker is not None
        assert self._llm is not None
        return self._index, self._reranker, self._llm

    async def warm_up(self) -> None:
        """Pre-construct the pipeline (pgvector connection + model loads) at service startup
        rather than on the first live request — same "pre-warm, don't surprise the first caller"
        lesson as every other local-model dependency in this repo."""
        await self._ensure_ready()

    async def ingest(self, document_id: str, text: str, metadata: dict[str, Any]) -> int:
        """Chunks and embeds `text`, tagging every chunk with `metadata` (document_id, visibility,
        ingested_by — D-068's permission fields live at the chunk level too, so retrieval can
        filter without a second lookup). Returns the number of chunks created."""
        index, _reranker, _llm = await self._ensure_ready()
        splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)
        doc = Document(text=text, doc_id=document_id, metadata=metadata)
        nodes = splitter.get_nodes_from_documents([doc])
        for node in nodes:
            node.id_ = str(uuid.uuid4())
        await run_in_threadpool(index.insert_nodes, nodes)
        return len(nodes)

    def _apply_token(self, llm: OpenAILike, token: str) -> None:
        llm.api_key = token

    async def query(self, question: str, caller_id: str) -> RagAnswer:
        index, reranker, llm = await self._ensure_ready()
        retriever = index.as_retriever(
            vector_store_query_mode="hybrid",
            similarity_top_k=self._settings.top_k_retrieve,
        )
        candidates: list[NodeWithScore] = await run_in_threadpool(retriever.retrieve, question)

        # D-068's permission filter, applied in Python rather than a nested metadata-filter
        # query DSL — simpler to get right at this scale, and easy to verify directly.
        visible = [
            n
            for n in candidates
            if n.node.metadata.get("visibility") == "shared"
            or n.node.metadata.get("ingested_by") == caller_id
        ]

        reranked = await run_in_threadpool(
            reranker._postprocess_nodes, visible, QueryBundle(query_str=question)
        )

        if not reranked:
            return RagAnswer(
                answer="I don't know — no relevant documents found.", source_document_ids=[]
            )

        context = "\n\n".join(n.node.get_content() for n in reranked)
        prompt = _SYSTEM_PROMPT.format(context=context, question=question)

        response = await call_with_auth_retry(
            self._token_client,
            lambda token: self._apply_token(llm, token),
            lambda: run_in_threadpool(llm.complete, prompt),
        )
        source_ids = sorted({n.node.metadata.get("document_id", "") for n in reranked})
        return RagAnswer(answer=response.text, source_document_ids=[s for s in source_ids if s])
