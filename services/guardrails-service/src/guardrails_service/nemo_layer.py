"""NeMo Guardrails layer — a Colang keyword-blocklist input flow, no LLM configured.

D-053 verified live that NeMo's pattern-based rails run with `models: []`. Two more real findings
made while wiring this up, both found only once this ran inside a real ASGI server (a bare-script
scratch test never hits either): (1) `LLMRails.generate()`'s default behavior only stops at a
blocking input flow — an input that *doesn't* match falls through to full dialog generation,
which needs an LLM regardless of whether any rail needed one; fixed with
`GenerationOptions(rails=GenerationRailsOptions(dialog=False))`, which runs input rails only. (2)
The *synchronous* `generate()` refuses outright to run inside an already-running event loop
(`RuntimeError: You are using the sync generate inside async code`) — invisible in a standalone
script (no event loop running there) but fatal the moment this is called from a FastAPI request
handler. Fixed by using `generate_async` and making this module's own `check()` async too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nemoguardrails import LLMRails, RailsConfig
from nemoguardrails.rails.llm.options import (
    GenerationOptions,
    GenerationRailsOptions,
    GenerationResponse,
)

_RAILS_DIR = Path(__file__).resolve().parents[2] / "rails"
_config = RailsConfig.from_path(str(_RAILS_DIR))
_rails = LLMRails(_config)
_options = GenerationOptions(rails=GenerationRailsOptions(dialog=False))

_BLOCK_MESSAGE = "This request was blocked by a guardrails input flow."


@dataclass
class Finding:
    layer: str
    rule: str
    severity: str  # "block" | "info"
    detail: str


async def check(text: str) -> list[Finding]:
    result = await _rails.generate_async(
        messages=[{"role": "user", "content": text}], options=_options
    )
    if not isinstance(result, GenerationResponse):
        raise TypeError(f"expected a GenerationResponse, got {type(result)!r}")
    response = result.response[0] if result.response else {}
    content = response.get("content") if isinstance(response, dict) else None
    if content == _BLOCK_MESSAGE:
        return [
            Finding(
                layer="nemo_guardrails",
                rule="block banned keywords",
                severity="block",
                detail="matched a banned-keyword Colang input flow",
            )
        ]
    return []
