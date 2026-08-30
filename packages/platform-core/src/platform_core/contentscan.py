"""Shared heuristic text-scanning engine.

Pattern-matches known-dangerous constructs in text something will *read and act on* — a
destructive shell command, a reverse-shell/credential-exfiltration pattern, or prompt-injection/
instruction-override phrasing aimed at whatever (agent or LLM-judge) reads this content. Not a
sandboxed dynamic analysis, not an ML classifier, not a guarantee of safety — it catches
unsophisticated, copy-pasted malicious content and says nothing about a novel or carefully
obfuscated attack.

Built for skill-registry's bundle scan (a skill's `SKILL.md`/scripts are read and executed by an
agent) and reused by eval-registry's judge-rubric scan (an LLM-judge reads a suite's `criteria`
prose the same way) — the underlying threat and rule set are identical; only the surrounding
context (a file in a zip vs. a string field in JSON) differs per caller, which is why this lives
in `platform-core` rather than being copy-pasted into each service.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

# (rule, pattern, human-readable detail).
BLOCK_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "destructive-delete",
        re.compile(r"rm\s+-rf\s+(/|~|\$HOME|\*)(?=\s|$)"),
        "recursive force-delete of root, home, or everything",
    ),
    (
        "fork-bomb",
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "shell fork bomb",
    ),
    (
        "pipe-to-shell",
        re.compile(r"(curl|wget)\s[^\n|]*\|\s*(sudo\s+)?(sh|bash|zsh)\b"),
        "downloads and executes a remote script unexamined",
    ),
    (
        "reverse-shell",
        re.compile(
            r"(bash\s+-i\s*>&\s*/dev/tcp/|\bnc\s+-e\s+/bin/(sh|bash)\b"
            r"|socket\.socket\([^)]*\)[\s\S]{0,120}subprocess)"
        ),
        "reverse-shell pattern",
    ),
    (
        "credential-exfiltration",
        re.compile(
            r"(\.ssh/id_rsa\b|\.aws/credentials\b|\.netrc\b|/etc/shadow\b"
            r"|env\s*\|\s*curl\b|os\.environ.{0,60}requests\.(post|put))",
            re.DOTALL,
        ),
        "reads and appears to exfiltrate credentials or secrets",
    ),
    (
        "obfuscated-exec",
        re.compile(r"\bexec\s*\(\s*(base64|codecs)\.[a-zA-Z_]+decode"),
        "executes a decoded/obfuscated payload",
    ),
    (
        "prompt-injection",
        re.compile(
            r"(ignore\s+(all\s+)?(previous|prior|above)\s+instructions"
            r"|disregard\s+(all\s+)?(previous|prior|above)\s+instructions"
            r"|you\s+must\s+(now\s+)?exfiltrate"
            r"|ignore\s+the\s+(actual\s+)?(rubric|criteria)"
            r"|always\s+(score|return|give)\s+(a\s+)?(perfect|1\.0|100%|full)\s)",
            re.IGNORECASE,
        ),
        "instruction-override / prompt-injection phrasing aimed at whatever reads this content",
    ),
)

WARN_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "shell-true",
        re.compile(r"subprocess\.[a-zA-Z_]+\([^)]*shell\s*=\s*True"),
        "subprocess call with shell=True",
    ),
    ("os-system", re.compile(r"\bos\.system\s*\("), "os.system call"),
    ("raw-ip-url", re.compile(r"https?://\d{1,3}(?:\.\d{1,3}){3}\b"), "hardcoded raw-IP URL"),
    ("setuid-chmod", re.compile(r"chmod\s+[+]?[0-7]*[24]7?7?7?s"), "sets a setuid/setgid bit"),
)


@dataclass
class Finding:
    label: str  # whatever identifies this piece of content to the caller: a filename, a field name
    rule: str
    severity: str  # "block" | "warn"
    detail: str


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not any(f.severity == "block" for f in self.findings)


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def scan_text(label: str, text: str) -> list[Finding]:
    """Pattern-match BLOCK_PATTERNS/WARN_PATTERNS against ``text``, tagging any findings with
    ``label`` so the caller can trace a finding back to its source.
    """
    findings: list[Finding] = []
    for rule, pattern, detail in BLOCK_PATTERNS:
        if pattern.search(text):
            findings.append(Finding(label, rule, "block", detail))
    for rule, pattern, detail in WARN_PATTERNS:
        if pattern.search(text):
            findings.append(Finding(label, rule, "warn", detail))
    return findings
