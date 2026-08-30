"""Validates eval suite metadata + goldens against the schema in the harness design doc.

No external standard here (D-011/D-012 set the shape; D-039/D-040 the concrete schema) — unlike
Agent Registry (A2A) and Skill Registry (Agent Skills), this is a from-scratch design, not a
spec-compliance check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

KNOWN_ENGINES = {"deepeval", "ragas", "promptfoo", "custom"}
KNOWN_KINDS = {"cases", "redteam"}
NAME_MAX = 128
DESCRIPTION_MAX = 1024


class SuiteValidationError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class ParsedSuite:
    name: str
    version: str
    description: str
    kind: str  # "cases" | "redteam"
    applies_to: list[str]
    metrics: list[dict[str, Any]] | None  # "cases" kind
    redteam_config: dict[str, Any] | None  # "redteam" kind


@dataclass
class Golden:
    input: str
    expected_output: str | None
    context: list[str] | None
    expected_tools: list[str] | None
    metadata: dict[str, Any]


def parse_suite_metadata(raw: dict[str, Any]) -> ParsedSuite:
    """Raises ``SuiteValidationError`` unless ``raw`` matches the suite metadata schema."""
    name = raw.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= NAME_MAX):
        raise SuiteValidationError(f"'name' is required and must be 1-{NAME_MAX} characters")

    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise SuiteValidationError("'version' is required and must be a non-empty string")

    description = raw.get("description")
    if not isinstance(description, str) or not (1 <= len(description) <= DESCRIPTION_MAX):
        raise SuiteValidationError(
            f"'description' is required and must be 1-{DESCRIPTION_MAX} characters"
        )

    kind = raw.get("kind", "cases")
    if kind not in KNOWN_KINDS:
        raise SuiteValidationError(f"'kind' must be one of {sorted(KNOWN_KINDS)}")

    applies_to = raw.get("applies_to", [])
    if not isinstance(applies_to, list) or not all(isinstance(t, str) for t in applies_to):
        raise SuiteValidationError("'applies_to' must be a list of strings")

    metrics: list[dict[str, Any]] | None = None
    redteam_config: dict[str, Any] | None = None

    if kind == "cases":
        metrics = raw.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise SuiteValidationError(
                "'metrics' is required and must be a non-empty list for a 'cases' suite"
            )
        for i, m in enumerate(metrics):
            if not isinstance(m, dict):
                raise SuiteValidationError(f"metrics[{i}] must be an object")
            engine = m.get("engine")
            if engine not in KNOWN_ENGINES:
                raise SuiteValidationError(
                    f"metrics[{i}].engine must be one of {sorted(KNOWN_ENGINES)}, got {engine!r}"
                )
            metric_id = m.get("metric_id")
            if not isinstance(metric_id, str) or not metric_id:
                raise SuiteValidationError(f"metrics[{i}].metric_id is required")
            params = m.get("params", {})
            if not isinstance(params, dict):
                raise SuiteValidationError(f"metrics[{i}].params must be an object")
    else:  # kind == "redteam"
        redteam_config = raw.get("redteam_config")
        if not isinstance(redteam_config, dict):
            raise SuiteValidationError("'redteam_config' is required for a 'redteam' suite")
        purpose = redteam_config.get("purpose")
        if not isinstance(purpose, str) or not purpose:
            raise SuiteValidationError("redteam_config.purpose is required")

    return ParsedSuite(
        name=name,
        version=version,
        description=description,
        kind=kind,
        applies_to=applies_to,
        metrics=metrics,
        redteam_config=redteam_config,
    )


def parse_goldens(jsonl_text: str) -> list[Golden]:
    """Raises ``SuiteValidationError`` unless every non-blank line is a valid golden. A blank
    dataset (zero goldens) is also rejected — a `cases` suite with nothing to test isn't useful.
    """
    goldens: list[Golden] = []
    for i, line in enumerate(jsonl_text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SuiteValidationError(f"line {i}: invalid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise SuiteValidationError(f"line {i}: each golden must be a JSON object")

        input_ = obj.get("input")
        if not isinstance(input_, str) or not input_:
            raise SuiteValidationError(f"line {i}: 'input' is required and must be non-empty")

        expected_output = obj.get("expected_output")
        if expected_output is not None and not isinstance(expected_output, str):
            raise SuiteValidationError(f"line {i}: 'expected_output' must be a string if present")

        context = obj.get("context")
        if context is not None and (
            not isinstance(context, list) or not all(isinstance(c, str) for c in context)
        ):
            raise SuiteValidationError(f"line {i}: 'context' must be a list of strings if present")

        expected_tools = obj.get("expected_tools")
        if expected_tools is not None and (
            not isinstance(expected_tools, list)
            or not all(isinstance(t, str) for t in expected_tools)
        ):
            raise SuiteValidationError(
                f"line {i}: 'expected_tools' must be a list of strings if present"
            )

        metadata = obj.get("metadata", {})
        if not isinstance(metadata, dict):
            raise SuiteValidationError(f"line {i}: 'metadata' must be an object if present")

        goldens.append(
            Golden(
                input=input_,
                expected_output=expected_output,
                context=context,
                expected_tools=expected_tools,
                metadata=metadata,
            )
        )

    if not goldens:
        raise SuiteValidationError("dataset must contain at least one golden")
    return goldens
