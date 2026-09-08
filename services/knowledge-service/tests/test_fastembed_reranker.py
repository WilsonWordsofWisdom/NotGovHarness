"""FastEmbedRerank tested against the real FastEmbed cross-encoder (D-066) — a mock would assert
this design's intent, not that reranking actually reorders candidates by relevance.
"""

from __future__ import annotations

from knowledge_service.fastembed_reranker import FastEmbedRerank
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode


def test_reranker_reorders_by_relevance_not_input_order():
    reranker = FastEmbedRerank(top_n=2)
    # Deliberately ordered so the correct answer starts last — a no-op reranker would leave it
    # last; a real one must move it to the front.
    nodes = [
        NodeWithScore(node=TextNode(text="The office kitchen has coffee and snacks."), score=0.9),
        NodeWithScore(
            node=TextNode(text="Returns are accepted within 30 days of purchase."), score=0.8
        ),
        NodeWithScore(
            node=TextNode(text="Employees get 15 days of paid annual leave per year."), score=0.1
        ),
    ]

    result = reranker._postprocess_nodes(
        nodes, QueryBundle(query_str="how many vacation days do I get")
    )

    assert len(result) == 2
    assert result[0].node.get_content() == "Employees get 15 days of paid annual leave per year."
    # Genuinely reordered, not the input's original top-2 by input-order/score.
    assert [n.node.get_content() for n in result] != [n.node.get_content() for n in nodes[:2]]


def test_reranker_respects_top_n():
    reranker = FastEmbedRerank(top_n=1)
    nodes = [
        NodeWithScore(node=TextNode(text="Alpha document about cats."), score=0.5),
        NodeWithScore(node=TextNode(text="Beta document about dogs."), score=0.5),
        NodeWithScore(node=TextNode(text="Gamma document about birds."), score=0.5),
    ]

    result = reranker._postprocess_nodes(nodes, QueryBundle(query_str="tell me about dogs"))

    assert len(result) == 1
    assert "dogs" in result[0].node.get_content()


def test_reranker_returns_up_to_top_n_when_no_query_given():
    reranker = FastEmbedRerank(top_n=2)
    nodes = [
        NodeWithScore(node=TextNode(text="a"), score=0.1),
        NodeWithScore(node=TextNode(text="b"), score=0.2),
        NodeWithScore(node=TextNode(text="c"), score=0.3),
    ]

    result = reranker._postprocess_nodes(nodes, None)

    assert len(result) == 2
