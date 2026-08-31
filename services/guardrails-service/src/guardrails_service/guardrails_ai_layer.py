"""Guardrails AI layer.

D-051, applied here, not left as a per-deploy gotcha: Guardrails AI's telemetry defaults to
*enabled* even with no config file present at all, and phones home to a Guardrails-AI-owned AWS
endpoint on every `Guard().validate()` call.

A second, real finding made wiring this up: mutating `guardrails.settings.settings.rc` in memory
(the first fix tried here) does *not* stick — `Guard.__init__()` unconditionally ends with
`self.configure()`, which calls `_load_rc()` and reloads `settings.rc` fresh from
`~/.guardrailsrc` on disk every single time a `Guard` is constructed, silently discarding any
in-memory change. Confirmed live, step by step, that `Guard().use(...)` resets
`settings.rc.enable_metrics` back to `True` even after explicitly setting it `False` beforehand.
The only fix that survives this reload is writing the actual file to disk, before the first
`Guard` anywhere in the process is constructed — done here, at this module's import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_RC_PATH = Path.home() / ".guardrailsrc"
_RC_PATH.write_text("enable_metrics=false\n")

from guardrails import Guard  # noqa: E402 - must follow the telemetry-disable line above
from guardrails.validator_base import OnFailAction  # noqa: E402
from guardrails_ai.regex_match import RegexMatch  # noqa: E402

# Printable ASCII, spaces, and common punctuation only — deliberately simple for v1 (D-052: this
# is one real validator among many available as public-PyPI `guardrails-ai-<name>` packages;
# more can be added the same way without touching the Hub CLI at all).
_ALLOWED_PATTERN = r"^[\x20-\x7E]*$"

# RegexMatch's own type stub declares on_fail as Callable-only, but it accepts OnFailAction at
# runtime (this is exactly how guardrails-ai's own docs use it) — a stub inaccuracy, not a bug
# here.
_validator = RegexMatch(
    regex=_ALLOWED_PATTERN,
    on_fail=OnFailAction.EXCEPTION,  # pyright: ignore[reportArgumentType]
)
_guard = Guard().use(_validator)


@dataclass
class Finding:
    layer: str
    rule: str
    severity: str  # "block" | "info"
    detail: str


def check(text: str) -> list[Finding]:
    try:
        _guard.validate(text)
    except Exception as exc:  # noqa: BLE001 - guardrails raises its own ValidationError type
        return [
            Finding(
                layer="guardrails_ai",
                rule="regex_match",
                severity="block",
                detail=f"failed the allowed-character-set validator: {exc}",
            )
        ]
    return []
