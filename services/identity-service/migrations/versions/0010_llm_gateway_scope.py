"""grant example-service the LLM Gateway call scope

Same simulated-principal pattern as every prior "future caller" grant (D-030) — no real Agent
Runtime exists yet. See docs/superpowers/specs/2026-09-01-llm-gateway-harness-design.md.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

clients = sa.table(
    "clients",
    sa.column("client_id", sa.String),
    sa.column("allowed_scopes", sa.String),
)

NEW_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call"
)
OLD_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check"
)


def upgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=NEW_SCOPES)
    )


def downgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=OLD_SCOPES)
    )
