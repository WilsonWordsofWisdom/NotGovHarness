"""Infra-free: parse_skill_md against the spec's own valid/invalid examples
(agentskills.io/specification) — no Postgres/MinIO needed to prove the structural rules.
"""

from __future__ import annotations

import pytest
from skill_registry.skillmd import SkillValidationError, parse_skill_md

MINIMAL = """---
name: skill-name
description: A description of what this skill does and when to use it.
---
"""

WITH_OPTIONAL_FIELDS = """---
name: pdf-processing
description: Extract PDF text, fill forms, merge files. Use when handling PDFs.
license: Apache-2.0
metadata:
  author: example-org
  version: "1.0"
---

# PDF processing

Step-by-step instructions here.
"""


def test_minimal_valid_skill_parses():
    parsed = parse_skill_md(MINIMAL, directory_name="skill-name")
    assert parsed.name == "skill-name"
    assert parsed.description == "A description of what this skill does and when to use it."
    assert parsed.license is None
    assert parsed.compatibility is None
    assert parsed.metadata == {}
    assert parsed.allowed_tools is None


def test_valid_skill_with_optional_fields_parses():
    parsed = parse_skill_md(WITH_OPTIONAL_FIELDS, directory_name="pdf-processing")
    assert parsed.license == "Apache-2.0"
    assert parsed.metadata == {"author": "example-org", "version": "1.0"}
    assert "Step-by-step instructions" in parsed.body


@pytest.mark.parametrize(
    "name",
    ["PDF-Processing", "-pdf", "pdf--processing", "pdf-", "", "a" * 65],
)
def test_invalid_name_patterns_are_rejected(name: str):
    content = f"---\nname: {name!r}\ndescription: valid description\n---\n"
    with pytest.raises(SkillValidationError, match="name"):
        parse_skill_md(content, directory_name=name or "placeholder")


def test_name_not_matching_directory_is_rejected():
    with pytest.raises(SkillValidationError, match="directory"):
        parse_skill_md(MINIMAL, directory_name="different-name")


def test_missing_description_is_rejected():
    content = "---\nname: skill-name\n---\n"
    with pytest.raises(SkillValidationError, match="description"):
        parse_skill_md(content, directory_name="skill-name")


def test_oversized_description_is_rejected():
    content = f"---\nname: skill-name\ndescription: {'a' * 1025}\n---\n"
    with pytest.raises(SkillValidationError, match="description"):
        parse_skill_md(content, directory_name="skill-name")


def test_oversized_compatibility_is_rejected():
    content = f"---\nname: skill-name\ndescription: valid\ncompatibility: {'a' * 501}\n---\n"
    with pytest.raises(SkillValidationError, match="compatibility"):
        parse_skill_md(content, directory_name="skill-name")


def test_non_string_map_metadata_is_rejected():
    content = "---\nname: skill-name\ndescription: valid\nmetadata:\n  count: 5\n---\n"
    with pytest.raises(SkillValidationError, match="metadata"):
        parse_skill_md(content, directory_name="skill-name")


def test_missing_frontmatter_delimiters_is_rejected():
    with pytest.raises(SkillValidationError, match="frontmatter"):
        parse_skill_md("name: skill-name\ndescription: valid\n", directory_name="skill-name")


def test_non_mapping_frontmatter_is_rejected():
    with pytest.raises(SkillValidationError, match="mapping"):
        parse_skill_md("---\n- just\n- a\n- list\n---\n", directory_name="skill-name")
