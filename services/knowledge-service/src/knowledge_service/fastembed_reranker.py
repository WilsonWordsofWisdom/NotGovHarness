"""A cross-encoder reranker `NodePostprocessor` backed by FastEmbed, not `FlagEmbedding` (D-066).

No packaged LlamaIndex↔FastEmbed reranker integration exists — the natural one
(`llama-index-postprocessor-flag-embedding-reranker`) pulls in the full torch/transformers/
sentence-transformers stack (multiple GB) for a job FastEmbed's own `TextCrossEncoder` already
does in ~80MB, reusing the same local-ONNX posture as Memory's embedder (D-061). This is the
small glue LlamaIndex doesn't ship, not a workaround for something broken.
"""

from __future__ import annotations

from fastembed.rerank.cross_encoder import TextCrossEncoder
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
from pydantic import PrivateAttr


class FastEmbedRerank(BaseNodePostprocessor):
    model_name: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    top_n: int = 3

    _encoder: TextCrossEncoder = PrivateAttr()

    def model_post_init(self, __context: object) -> None:
        self._encoder = TextCrossEncoder(model_name=self.model_name)

    @classmethod
    def class_name(cls) -> str:
        return "FastEmbedRerank"

    def _postprocess_nodes(
        self, nodes: list[NodeWithScore], query_bundle: QueryBundle | None = None
    ) -> list[NodeWithScore]:
        if query_bundle is None or not nodes:
            return nodes[: self.top_n]

        documents = [n.node.get_content() for n in nodes]
        scores = list(self._encoder.rerank(query_bundle.query_str, documents))

        reranked = sorted(zip(scores, nodes, strict=True), key=lambda pair: pair[0], reverse=True)
        result = []
        for score, node_with_score in reranked[: self.top_n]:
            node_with_score.score = float(score)
            result.append(node_with_score)
        return result
