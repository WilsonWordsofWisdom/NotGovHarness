"""Heuristic static scan for skill bundles.

A skill is unusual among things a registry stores: its whole point is that an agent *reads it
and follows it* — both the bundled scripts (executed) and the `SKILL.md` body itself
(interpreted by the agent as instructions). So this scans every text-like file in the bundle,
`SKILL.md` included, for two related threats: bundled code that does something destructive or
exfiltrates credentials, and *prose* trying to instruct the agent into doing the same
(a skill-bundle-shaped prompt injection).

This is pattern-matching against known-dangerous constructs — not a sandboxed dynamic analysis,
not an ML classifier, and not a guarantee of safety. It catches unsophisticated and copy-pasted
malicious content (the common case for an open registry) and says nothing about a genuinely
novel or carefully obfuscated attack. See the harness design's Risks section.

Findings are one of two severities:
- ``block`` — the publish is rejected outright (a destructive command, a reverse-shell pattern,
  a disallowed binary).
- ``warn`` — stored alongside the skill and returned to the publisher/browser, but doesn't block
  (a `shell=True` subprocess call, a hardcoded raw-IP URL) — a human judgment call, not a clear
  "this is malicious."
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

DISALLOWED_EXTENSIONS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".msi",
    ".scr",
    ".jar",
    ".class",
    ".com",
}

# Magic-byte prefixes for common executable formats, in case the extension is disguised.
_BINARY_MAGIC: dict[bytes, str] = {
    b"MZ": "Windows PE executable",
    b"\x7fELF": "ELF executable",
    b"\xfe\xed\xfa": "Mach-O executable",
    b"\xca\xfe\xba\xbe": "Mach-O fat binary / Java class",
}

_SCRIPT_EXTENSIONS = {".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".ts", ".rb", ".pl", ".ps1"}
_TEXT_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".json", ".csv", ".toml", ".cfg", ".ini"}

# (rule, pattern, human-readable detail). Checked against every text-like file, SKILL.md
# included — a malicious instruction in prose is exactly as real a threat here as one in code.
_BLOCK_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
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
            r"|you\s+must\s+(now\s+)?exfiltrate)",
            re.IGNORECASE,
        ),
        "instruction-override / prompt-injection phrasing aimed at the agent reading this file",
    ),
)

_WARN_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
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
    file: str
    rule: str
    severity: str  # "block" | "warn"
    detail: str


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not any(f.severity == "block" for f in self.findings)


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _extension(name: str) -> str:
    lower = name.lower()
    return "." + lower.rsplit(".", 1)[-1] if "." in lower else ""


def scan_bundle(files: dict[str, bytes]) -> ScanResult:
    """Scan every file in an extracted bundle. ``files`` maps a path relative to the skill
    directory (e.g. ``"scripts/run.py"``, ``"SKILL.md"``) to its raw bytes.
    """
    result = ScanResult()

    for name, content in files.items():
        ext = _extension(name)

        if ext in DISALLOWED_EXTENSIONS:
            result.findings.append(
                Finding(
                    name,
                    "disallowed-extension",
                    "block",
                    f"{ext} is not permitted in a skill bundle",
                )
            )
            continue

        for magic, label in _BINARY_MAGIC.items():
            if content.startswith(magic):
                result.findings.append(
                    Finding(
                        name, "binary-signature", "block", f"looks like a {label}, not source/text"
                    )
                )
                break
        else:
            if ext not in _SCRIPT_EXTENSIONS and ext not in _TEXT_EXTENSIONS:
                # an unrecognized binary/asset type (image, font, ...) — not scanned as text
                continue

            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue

            for rule, pattern, detail in _BLOCK_PATTERNS:
                if pattern.search(text):
                    result.findings.append(Finding(name, rule, "block", detail))

            for rule, pattern, detail in _WARN_PATTERNS:
                if pattern.search(text):
                    result.findings.append(Finding(name, rule, "warn", detail))

            if ext in _SCRIPT_EXTENSIONS and len(content) > 200 and _entropy(content) > 5.7:
                result.findings.append(
                    Finding(
                        name,
                        "high-entropy",
                        "warn",
                        "unusually high-entropy content for a script — possibly obfuscated",
                    )
                )

    return result
