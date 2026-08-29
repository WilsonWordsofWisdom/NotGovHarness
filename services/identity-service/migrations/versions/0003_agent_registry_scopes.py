"""grant example-service the Agent Registry publisher scopes

example-service stands in for the not-yet-built Agent Builder (Wave 4) as a card publisher — the
same "simulated principal" pattern "alice" already plays for delegation. See
docs/superpowers/specs/2026-08-30-agent-registry-harness-design.md (D-030).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
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
        .values(allowed_scopes="upstream:call agentcard:sign registry:publish")
    )


def downgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes="upstream:call")
    )
