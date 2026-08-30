"""Parses and validates a `SKILL.md` file against the Agent Skills spec
(agentskills.io/specification) — the field table verbatim, hand-implemented rather than shelling
out to the reference `skills-ref` tool (see the harness design's Context section for why).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", re.DOTALL)


class SkillValidationError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class ParsedSkill:
    name: str
    description: str
    license: str | None
    compatibility: str | None
    metadata: dict[str, str]
    allowed_tools: str | None
    body: str
    raw: str = field(repr=False)


def parse_skill_md(content: str, *, directory_name: str) -> ParsedSkill:
    """Raises ``SkillValidationError`` unless every rule in the spec's frontmatter table holds."""
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        raise SkillValidationError("SKILL.md must start with YAML frontmatter delimited by ---")

    frontmatter_text, body = match.group(1), match.group(2)
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillValidationError("frontmatter must be a YAML mapping")

    name = frontmatter.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 64):
        raise SkillValidationError("'name' is required and must be 1-64 characters")
    if not _NAME_RE.match(name):
        raise SkillValidationError(
            "'name' must be lowercase alphanumeric and hyphens only, no leading/trailing or "
            "consecutive hyphens"
        )
    if name != directory_name:
        raise SkillValidationError(
            f"'name' ({name!r}) must match the skill's top-level directory name "
            f"({directory_name!r})"
        )

    description = frontmatter.get("description")
    if not isinstance(description, str) or not (1 <= len(description) <= 1024):
        raise SkillValidationError("'description' is required and must be 1-1024 characters")

    license_ = frontmatter.get("license")
    if license_ is not None and not isinstance(license_, str):
        raise SkillValidationError("'license' must be a string")

    compatibility = frontmatter.get("compatibility")
    if compatibility is not None:
        if not isinstance(compatibility, str) or not (1 <= len(compatibility) <= 500):
            raise SkillValidationError("'compatibility' must be 1-500 characters if present")

    metadata = frontmatter.get("metadata")
    if metadata is None:
        metadata = {}
    elif not isinstance(metadata, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
    ):
        raise SkillValidationError("'metadata' must be a map of string to string")

    allowed_tools = frontmatter.get("allowed-tools")
    if allowed_tools is not None and not isinstance(allowed_tools, str):
        raise SkillValidationError("'allowed-tools' must be a string")

    return ParsedSkill(
        name=name,
        description=description,
        license=license_,
        compatibility=compatibility,
        metadata=metadata,
        allowed_tools=allowed_tools,
        body=body,
        raw=content,
    )
