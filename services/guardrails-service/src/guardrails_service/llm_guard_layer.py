"""LLM Guard layer — rule-based scanners only for v1 (D-051..D-053's sibling finding, recorded
informationally: LLM Guard's ML-based scanners each need a model download; the rule-based ones
verified here need none, at scan time or at all).
"""

from __future__ import annotations

from dataclasses import dataclass

from llm_guard.input_scanners import BanSubstrings, TokenLimit

# A small, illustrative default list — not exhaustive. Real coverage is NeMo's/Guardrails AI's
# job too (defense in depth); this layer's purpose is proving the mechanism, not being the one
# true blocklist.
_BANNED_PHRASES = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard your instructions",
]

_ban_substrings = BanSubstrings(substrings=_BANNED_PHRASES, case_sensitive=False)
_token_limit = TokenLimit(limit=4096)


@dataclass
class Finding:
    layer: str
    rule: str
    severity: str  # "block" | "info"
    detail: str


def check(text: str) -> list[Finding]:
    findings: list[Finding] = []

    _, is_valid, _ = _ban_substrings.scan(text)
    if not is_valid:
        findings.append(
            Finding(
                layer="llm_guard",
                rule="BanSubstrings",
                severity="block",
                detail="matched a banned prompt-injection phrase",
            )
        )

    _, is_valid, _ = _token_limit.scan(text)
    if not is_valid:
        findings.append(
            Finding(
                layer="llm_guard",
                rule="TokenLimit",
                severity="block",
                detail="text exceeds the configured token limit",
            )
        )

    return findings
