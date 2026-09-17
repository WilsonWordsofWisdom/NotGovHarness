"""Runs Ragas metrics against a case, judged by `llm-gateway` (D-079).

Uses the non-deprecated `ragas.metrics.collections` + `llm_factory(provider="openai", client=...)`
integration point, not the old top-level `ragas.metrics.<MetricName>` imports. `llm_factory`
takes a pre-initialized client, so — same reasoning as the DeepEval engine — a fresh
`openai.AsyncOpenAI` is constructed with a fresh token on every call rather than cached.

Ragas metrics return a continuous score with no built-in pass/fail concept (unlike DeepEval's
`.success`); `passed` here is our own threshold applied to that score.
"""

from __future__ import annotations

from typing import Any

import openai
from ragas.llms.base import llm_factory
from ragas.metrics.collections import FactualCorrectness, Faithfulness

from platform_core.llm_gateway_auth import TokenClient

from ..eval_registry_client import Golden


async def _judge_client(base_url: str, model: str, token_client: TokenClient) -> Any:
    token = await token_client.ensure_token()
    client = openai.AsyncOpenAI(base_url=base_url, api_key=token)
    return llm_factory(model, provider="openai", client=client)


async def run_metric(
    metric_id: str,
    params: dict[str, Any],
    golden: Golden,
    actual_output: str,
    base_url: str,
    model: str,
    token_client: TokenClient,
) -> dict[str, Any]:
    llm = await _judge_client(base_url, model, token_client)
    threshold = params.get("threshold", 0.5)

    if metric_id == "faithfulness":
        if not golden.context:
            raise ValueError("'faithfulness' requires the golden to have 'context'")
        result = await Faithfulness(llm=llm).ascore(
            user_input=golden.input,
            response=actual_output,
            retrieved_contexts=golden.context,
        )
    elif metric_id == "factual_correctness":
        if not golden.expected_output:
            raise ValueError("'factual_correctness' requires the golden to have 'expected_output'")
        result = await FactualCorrectness(llm=llm).ascore(
            response=actual_output, reference=golden.expected_output
        )
    else:
        raise ValueError(f"unknown ragas metric_id: {metric_id!r}")

    score = float(result.value)
    return {
        "metric_id": metric_id,
        "engine": "ragas",
        "score": score,
        "reason": result.reason,
        "passed": score >= threshold,
    }
