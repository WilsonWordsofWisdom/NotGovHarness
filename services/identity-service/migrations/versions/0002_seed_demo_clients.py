"""seed demo clients (example-service, alice)

Dev-only fixed secrets, matching the platform/platform Postgres posture elsewhere in this repo —
see docs/superpowers/specs/2026-08-23-agent-identity-harness-design.md. "alice" is the simulated
principal used to demonstrate delegation (RFC 8693 token exchange) on the example-service ->
upstream-stub hop; a real OIDC/human login is an explicit non-goal.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

clients = sa.table(
    "clients",
    sa.column("client_id", sa.String),
    sa.column("spiffe_id", sa.String),
    sa.column("allowed_scopes", sa.String),
    sa.column("secret_hash", sa.String),
)


def upgrade() -> None:
    op.bulk_insert(
        clients,
        [
            {
                "client_id": "example-service",
                "spiffe_id": None,  # registered separately by spire-registrar (step 5)
                "allowed_scopes": "upstream:call",
                "secret_hash": "$2b$12$0w/n.Hdvw0n/0QpenYFvyu/abkR.f/ubKX5LCqjwjXJSztHaDeLJ.",
            },
            {
                "client_id": "alice",
                "spiffe_id": None,
                "allowed_scopes": "upstream:call",
                "secret_hash": "$2b$12$hvKM1WSpO24v9XRdzbrPROWDoeAYjiNkEPxbx7Mm9lBKpAhnHZmeS",
            },
        ],
    )


def downgrade() -> None:
    op.execute(clients.delete().where(clients.c.client_id.in_(["example-service", "alice"])))
