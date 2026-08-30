"""Heuristic static scan for skill bundles.

A skill is unusual among things a registry stores: its whole point is that an agent *reads it
and follows it* — both the bundled scripts (executed) and the `SKILL.md` body itself
(interpreted by the agent as instructions). So this scans every text-like file in the bundle,
`SKILL.md` included, for two related threats: bundled code that does something destructive or
exfiltrates credentials, and *prose* trying to instruct the agent into doing the same
(a skill-bundle-shaped prompt injection).

The actual pattern-matching rules live in ``platform_core.contentscan`` (shared with
eval-registry's judge-rubric scan — see D-041); this module handles what's specific to a skill
bundle: which files get scanned as text, disallowed/disguised binaries, and per-script entropy.
"""

from __future__ import annotations

from platform_core.contentscan import Finding, ScanResult, entropy, scan_text

__all__ = ["Finding", "ScanResult", "scan_bundle"]

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

            result.findings.extend(scan_text(name, text))

            if ext in _SCRIPT_EXTENSIONS and len(content) > 200 and entropy(content) > 5.7:
                result.findings.append(
                    Finding(
                        name,
                        "high-entropy",
                        "warn",
                        "unusually high-entropy content for a script — possibly obfuscated",
                    )
                )

    return result
