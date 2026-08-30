"""Infra-free: parse_suite_metadata / parse_goldens against crafted valid/invalid suites — no
Postgres/MinIO needed to prove the schema rules.
"""

from __future__ import annotations

import pytest
from eval_registry.suite import SuiteValidationError, parse_goldens, parse_suite_metadata

VALID_CASES_SUITE = {
    "name": "tool-correctness-baseline",
    "version": "1.0.0",
    "description": "Baseline tool-use correctness checks.",
    "kind": "cases",
    "applies_to": ["tool_use"],
    "metrics": [{"engine": "deepeval", "metric_id": "ToolCorrectnessMetric", "params": {}}],
}

VALID_REDTEAM_SUITE = {
    "name": "safety-baseline",
    "version": "1.0.0",
    "description": "Baseline red-team safety pack.",
    "kind": "redteam",
    "applies_to": ["always"],
    "redteam_config": {"purpose": "test for jailbreak resistance", "plugins": ["harmful"]},
}


def test_valid_cases_suite_parses():
    parsed = parse_suite_metadata(VALID_CASES_SUITE)
    assert parsed.name == "tool-correctness-baseline"
    assert parsed.kind == "cases"
    assert parsed.metrics == VALID_CASES_SUITE["metrics"]
    assert parsed.redteam_config is None


def test_valid_redteam_suite_parses():
    parsed = parse_suite_metadata(VALID_REDTEAM_SUITE)
    assert parsed.kind == "redteam"
    assert parsed.redteam_config == VALID_REDTEAM_SUITE["redteam_config"]
    assert parsed.metrics is None


def test_missing_name_is_rejected():
    bad = {**VALID_CASES_SUITE}
    del bad["name"]
    with pytest.raises(SuiteValidationError, match="name"):
        parse_suite_metadata(bad)


def test_missing_version_is_rejected():
    bad = {**VALID_CASES_SUITE}
    del bad["version"]
    with pytest.raises(SuiteValidationError, match="version"):
        parse_suite_metadata(bad)


def test_unknown_kind_is_rejected():
    bad = {**VALID_CASES_SUITE, "kind": "not-a-real-kind"}
    with pytest.raises(SuiteValidationError, match="kind"):
        parse_suite_metadata(bad)


def test_cases_suite_without_metrics_is_rejected():
    bad = {**VALID_CASES_SUITE}
    del bad["metrics"]
    with pytest.raises(SuiteValidationError, match="metrics"):
        parse_suite_metadata(bad)


def test_metric_with_unknown_engine_is_rejected():
    bad = {
        **VALID_CASES_SUITE,
        "metrics": [{"engine": "not-a-real-engine", "metric_id": "x", "params": {}}],
    }
    with pytest.raises(SuiteValidationError, match="engine"):
        parse_suite_metadata(bad)


def test_redteam_suite_without_purpose_is_rejected():
    bad = {**VALID_REDTEAM_SUITE, "redteam_config": {"plugins": ["harmful"]}}
    with pytest.raises(SuiteValidationError, match="purpose"):
        parse_suite_metadata(bad)


def test_oversized_description_is_rejected():
    bad = {**VALID_CASES_SUITE, "description": "a" * 1025}
    with pytest.raises(SuiteValidationError, match="description"):
        parse_suite_metadata(bad)


# ---- goldens ----

VALID_JSONL = (
    '{"input": "What is 2+2?", "expected_output": "4"}\n'
    '{"input": "Summarize this doc.", "context": ["doc text here"]}\n'
)


def test_valid_goldens_parse():
    goldens = parse_goldens(VALID_JSONL)
    assert len(goldens) == 2
    assert goldens[0].input == "What is 2+2?"
    assert goldens[0].expected_output == "4"
    assert goldens[1].context == ["doc text here"]


def test_blank_lines_are_skipped():
    goldens = parse_goldens('{"input": "a"}\n\n\n{"input": "b"}\n')
    assert len(goldens) == 2


def test_empty_dataset_is_rejected():
    with pytest.raises(SuiteValidationError, match="at least one golden"):
        parse_goldens("\n\n")


def test_invalid_json_line_is_rejected():
    with pytest.raises(SuiteValidationError, match="invalid JSON"):
        parse_goldens("not json at all\n")


def test_golden_missing_input_is_rejected():
    with pytest.raises(SuiteValidationError, match="input"):
        parse_goldens('{"expected_output": "4"}\n')


def test_golden_with_non_list_context_is_rejected():
    with pytest.raises(SuiteValidationError, match="context"):
        parse_goldens('{"input": "a", "context": "not a list"}\n')
