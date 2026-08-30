"""grant example-service the Skill Registry publisher scope

example-service stands in as a skill author (same "simulated principal" pattern D-030 already
used for Agent Registry's publisher). See
docs/superpowers/specs/2026-08-30-skill-registry-harness-design.md.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

clients = sa.table(
    "clients",
    sa.column("client_id", sa.String),
    sa.column("allowed_scopes", sa.String),
)


def upgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(
            allowed_scopes="upstream:call agentcard:sign registry:publish skill_registry:publish"
        )
    )


def downgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes="upstream:call agentcard:sign registry:publish")
    )
