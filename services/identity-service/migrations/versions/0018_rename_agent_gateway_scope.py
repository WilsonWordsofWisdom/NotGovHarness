"""rename the agent_gateway:call scope to mcp_gateway_registry:call (D-105)

The harness formerly called Agent Gateway was renamed MCP Gateway Registry (D-105) -- it was
always a façade over ContextForge that also carries ContextForge's own MCP server/tool registry
(D-008/D-017), not a registry of agents, and the old name was found to be a real source of
confusion against the separate Agent Registry service. Every client already holding
agent_gateway:call gets it swapped in place rather than granted a second, additive scope, so the
rename doesn't silently leave the old scope string valid.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

clients = sa.table(
    "clients",
    sa.column("client_id", sa.String),
    sa.column("allowed_scopes", sa.String),
)

# Exact current values of every client found (live, not assumed) to hold agent_gateway:call.
EXAMPLE_SERVICE_OLD = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call memory:write knowledge:write builder:build "
    "evals:run agent_runtime:invoke deploy:create"
)
EXAMPLE_SERVICE_NEW = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish mcp_gateway_registry:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call memory:write knowledge:write builder:build "
    "evals:run agent_runtime:invoke deploy:create"
)

AGENT_BUILDER_OLD = "agentcard:sign registry:publish llm_gateway:call agent_gateway:call"
AGENT_BUILDER_NEW = "agentcard:sign registry:publish llm_gateway:call mcp_gateway_registry:call"


def upgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=EXAMPLE_SERVICE_NEW)
    )
    op.execute(
        clients.update()
        .where(clients.c.client_id == "agent-builder-service")
        .values(allowed_scopes=AGENT_BUILDER_NEW)
    )


def downgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=EXAMPLE_SERVICE_OLD)
    )
    op.execute(
        clients.update()
        .where(clients.c.client_id == "agent-builder-service")
        .values(allowed_scopes=AGENT_BUILDER_OLD)
    )
