"""grant example-service the Approvals request scope; seed a reviewer client

example-service stands in as the risk-taking caller (same simulated-principal pattern as
D-030/agent_gateway:call) — no real Agent Runtime exists yet. A separate "reviewer" client is
seeded (not example-service again) because requesting and deciding are different trust levels: a
service that can create risky-action requests must not also be able to approve its own requests.
See docs/superpowers/specs/2026-08-31-approvals-hitl-harness-design.md.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

clients = sa.table(
    "clients",
    sa.column("client_id", sa.String),
    sa.column("spiffe_id", sa.String),
    sa.column("allowed_scopes", sa.String),
    sa.column("secret_hash", sa.String),
)

NEW_EXAMPLE_SERVICE_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request"
)
OLD_EXAMPLE_SERVICE_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call"
)


def upgrade() -> None:
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=NEW_EXAMPLE_SERVICE_SCOPES)
    )
    op.bulk_insert(
        clients,
        [
            {
                "client_id": "reviewer",
                "spiffe_id": None,
                "allowed_scopes": "approvals:decide",
                # bcrypt hash of "reviewer-dev-secret" (dev-only fixed secret, same posture as
                # every other seeded client).
                "secret_hash": "$2b$12$0nwn6q6V72.8dGVseqfe5utSrHoJDTZkfQ2ua6sNfrhO9BlgNST1a",
            }
        ],
    )


def downgrade() -> None:
    op.execute(clients.delete().where(clients.c.client_id == "reviewer"))
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=OLD_EXAMPLE_SERVICE_SCOPES)
    )
