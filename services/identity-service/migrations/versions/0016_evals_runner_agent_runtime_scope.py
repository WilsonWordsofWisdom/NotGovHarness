"""grant evals-runner-service the Agent Runtime invoke scope

D-089: evals-runner-service's own token now also needs `agent_runtime:invoke`, to grade a real
deployed agent-runtime container directly rather than only `llm-gateway`.
See docs/superpowers/specs/2026-09-25-deployment-pipeline-harness-design.md.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

clients = sa.table(
    "clients",
    sa.column("client_id", sa.String),
    sa.column("allowed_scopes", sa.String),
)

NEW_SCOPES = "llm_gateway:call agent_runtime:invoke"
OLD_SCOPES = "llm_gateway:call"


def upgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "evals-runner-service")
        .values(allowed_scopes=NEW_SCOPES)
    )


def downgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "evals-runner-service")
        .values(allowed_scopes=OLD_SCOPES)
    )
