"""grant example-service the Agent Gateway call scope

example-service stands in as an "Agent Runtime" caller (same "simulated principal" pattern
D-030 already used for Agent Registry's publisher) — nothing in this platform is a real running
agent yet (Agent Builder/Runtime, Wave 4). See
docs/superpowers/specs/2026-08-31-agent-gateway-harness-design.md.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

clients = sa.table(
    "clients",
    sa.column("client_id", sa.String),
    sa.column("allowed_scopes", sa.String),
)

NEW_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call"
)
OLD_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish eval_registry:publish"
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
