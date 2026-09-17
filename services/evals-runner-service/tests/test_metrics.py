"""Skip-if-down: both metric engines judged for real against a real `llm-gateway` — the judge
calls themselves are real LLM calls, so these need the live stack, unlike a plain stub test.
One pair is engineered to obviously pass, one to obviously fail, per the design doc's criterion 2
("proving real judgment, not a rubber stamp").
"""

from __future__ import annotations

from evals_runner_service.eval_registry_client import Golden
from evals_runner_service.metrics import deepeval_engine, ragas_engine

from platform_core.llm_gateway_auth import TokenClient

ACTUAL_OUTPUT = "The capital of France is Paris."

# Full sentences, not bare words (D-083): ragas's FactualCorrectness does strict NLI entailment
# against `reference` alone — a one-word reference has no information to entail a claim like
# "Paris is a city" from, so it scores 0.0 even for an obviously-correct case.
CORRECT = Golden(
    input="What is the capital of France? Answer in a single full sentence.",
    expected_output="Paris is the capital of France.",
    context=None,
    expected_tools=None,
    metadata={},
)
WRONG = Golden(
    input="What is the capital of France? Answer in a single full sentence.",
    expected_output="Berlin is the capital of France.",
    context=None,
    expected_tools=None,
    metadata={},
)
G_EVAL_PARAMS = {
    "name": "Correctness",
    "criteria": "Determine whether the actual output matches the expected output factually.",
    "evaluation_params": ["input", "actual_output", "expected_output"],
    "threshold": 0.5,
}


def _token_client(identity_url: str) -> TokenClient:
    return TokenClient(
        identity_url=identity_url,
        client_id="example-service",
        client_secret="example-service-dev-secret",
        scope="llm_gateway:call",
    )


async def test_deepeval_g_eval_passes_a_correct_case_and_fails_a_wrong_one(
    platform_identity_url, platform_llm_gateway_url
):
    model = deepeval_engine.DeepEvalTargetModel(
        platform_llm_gateway_url, "qwen3.8", _token_client(platform_identity_url)
    )

    passing = await deepeval_engine.run_metric(
        "g_eval", G_EVAL_PARAMS, CORRECT, ACTUAL_OUTPUT, model
    )
    assert passing["passed"] is True

    failing = await deepeval_engine.run_metric(
        "g_eval", G_EVAL_PARAMS, WRONG, ACTUAL_OUTPUT, model
    )
    assert failing["passed"] is False


async def test_ragas_factual_correctness_passes_a_correct_case_and_fails_a_wrong_one(
    platform_identity_url, platform_llm_gateway_url
):
    token_client = _token_client(platform_identity_url)
    args = (platform_llm_gateway_url, "qwen3.8", token_client)

    passing = await ragas_engine.run_metric(
        "factual_correctness", {}, CORRECT, ACTUAL_OUTPUT, *args
    )
    assert passing["passed"] is True

    failing = await ragas_engine.run_metric(
        "factual_correctness", {}, WRONG, ACTUAL_OUTPUT, *args
    )
    assert failing["passed"] is False
