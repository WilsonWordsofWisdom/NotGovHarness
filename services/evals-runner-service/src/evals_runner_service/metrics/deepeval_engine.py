"""Runs DeepEval metrics against a case, judged by `llm-gateway` (D-078).

Uses bare metric objects (`.a_measure()`), not the high-level `evaluate()` — that wrapper is
built for local/CLI use (console reports, local result files, login-prompt nudges) a server
process has no use for. `DeepEvalTargetModel` constructs its underlying OpenAI-compatible client
fresh on every call rather than caching it on the instance, sidestepping D-069's whole class of
bug by construction.
"""

from __future__ import annotations

from typing import Any, cast

import openai
from deepeval.metrics import AnswerRelevancyMetric, BaseMetric, FaithfulnessMetric, GEval
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams

from platform_core.llm_gateway_auth import TokenClient

from ..eval_registry_client import Golden


class DeepEvalTargetModel(DeepEvalBaseLLM):
    """Points DeepEval's judge calls at `llm-gateway`."""

    def __init__(self, base_url: str, model: str, token_client: TokenClient) -> None:
        self._base_url = base_url
        self._model = model
        self._token_client = token_client

    def load_model(self) -> DeepEvalTargetModel:
        return self

    def get_model_name(self) -> str:
        return self._model

    def generate(self, prompt: str) -> str:  # pragma: no cover - async path is what's used
        raise NotImplementedError("evals-runner-service only uses the async DeepEval path")

    async def a_generate(self, prompt: str) -> str:
        token = await self._token_client.ensure_token()
        client = openai.AsyncOpenAI(base_url=self._base_url, api_key=token)
        resp = await client.chat.completions.create(
            model=self._model, messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content or ""


def _build_metric(metric_id: str, params: dict[str, Any], model: DeepEvalTargetModel) -> BaseMetric:
    if metric_id == "answer_relevancy":
        return AnswerRelevancyMetric(threshold=params.get("threshold", 0.5), model=model)
    if metric_id == "faithfulness":
        return FaithfulnessMetric(threshold=params.get("threshold", 0.5), model=model)
    if metric_id == "g_eval":
        evaluation_params = [
            SingleTurnParams(p) for p in params.get("evaluation_params", ["input", "actual_output"])
        ]
        return GEval(
            name=params.get("name", "G-Eval"),
            criteria=params["criteria"],
            evaluation_params=evaluation_params,
            threshold=params.get("threshold", 0.5),
            model=model,
        )
    raise ValueError(f"unknown deepeval metric_id: {metric_id!r}")


async def run_metric(
    metric_id: str,
    params: dict[str, Any],
    golden: Golden,
    actual_output: str,
    model: DeepEvalTargetModel,
) -> dict[str, Any]:
    metric = _build_metric(metric_id, params, model)
    # expected_tools is deliberately not passed: DeepEval's LLMTestCase expects a list of its own
    # ToolCall objects there, not the plain strings Eval Registry's Golden schema stores — v1's
    # metric set (answer_relevancy/faithfulness/g_eval) never needs it anyway (D-080 defers
    # tool-calling assertions along with everything else this build doesn't exercise for real).
    # retrieval_context's declared type is invariant in its list element type, so a plain
    # list[str] doesn't structurally match List[str | RetrievedContextData] under pyright, even
    # though it's a valid value at runtime — cast rather than restructure the data.
    retrieval_context = cast("list[str | Any] | None", golden.context)
    test_case = LLMTestCase(
        input=golden.input,
        actual_output=actual_output,
        expected_output=golden.expected_output,
        context=golden.context,
        retrieval_context=retrieval_context,
    )
    await metric.a_measure(test_case)
    return {
        "metric_id": metric_id,
        "engine": "deepeval",
        "score": metric.score,
        "reason": metric.reason,
        "passed": bool(metric.success),
    }
