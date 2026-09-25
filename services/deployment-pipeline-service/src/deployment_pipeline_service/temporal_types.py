"""Pure dataclasses shared by the workflow, the activities, and the API layer.

Kept import-free (no SQLAlchemy, no docker SDK, no platform_core.db) so the workflow module —
which Temporal's SDK re-imports inside a deterministic sandbox to validate replay-safety — never
has to import anything non-deterministic. Same separation Approvals/HITL's own
`temporal_types.py` already established, for the same reason (D-049's `asyncio.run()`-at-import
bug).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeploymentRequest:
    deployment_id: str
    agent_name: str
    agent_version: str
    # Found live during build, not anticipated in the original design sketch: the design doc's
    # POST /deployments API listed only {agent_name, agent_version?} — but the eval-gate needs to
    # know *which* suite gates this deployment, and auto-discovering one by capability tag (the
    # way Agent Builder selects suites) risks silently deploying with no gate at all when nothing
    # matches, in a reference environment where suites are mostly published ad hoc. Made
    # explicit and required instead, matching how a real deploy pipeline is actually configured.
    eval_suite_name: str
    eval_suite_version: str | None


@dataclass
class AgentConfig:
    """What the card's `extensions` (D-088) carry — everything needed to run this agent."""

    role: str
    goal: str
    backstory: str
    task: str


@dataclass
class RunningContainer:
    container_id: str
    host_port: int


@dataclass
class EvalGateInput:
    suite_name: str
    suite_version: str | None
    agent_runtime_host_port: int


@dataclass
class EvalGateResult:
    run_id: str
    passed: bool


@dataclass
class DeploymentOutcome:
    deployment_id: str
    status: str  # "running" | "eval_failed" | "failed"
    container_id: str | None
    host_port: int | None
    eval_run_id: str | None
    error: str | None
