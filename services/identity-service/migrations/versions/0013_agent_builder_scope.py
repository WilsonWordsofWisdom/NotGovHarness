"""grant example-service the Builder scope; seed agent-builder-service as its own OAuth2 client

`example-service` stands in as the usual D-030 simulated caller, gaining `builder:build`.
`agent-builder-service` is seeded as a genuine new identity (D-073), NOT by reusing
`example-service`'s existing `agentcard:sign`/`registry:publish` grants — checked first that
Agent Registry's own test_live_stack.py depends on `example-service` holding those exact scopes,
so they stay untouched. `agent-builder-service` gets its own copies of the scopes it needs as a
real service-calls-service caller (D-062/D-067): `agentcard:sign`, `registry:publish`,
`llm_gateway:call`, `agent_gateway:call`.
See docs/superpowers/specs/2026-09-08-agent-builder-harness-design.md.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
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
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call memory:write knowledge:write builder:build"
)
OLD_EXAMPLE_SERVICE_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call memory:write knowledge:write"
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
                "client_id": "agent-builder-service",
                "spiffe_id": None,
                "allowed_scopes": (
                    "agentcard:sign registry:publish llm_gateway:call agent_gateway:call"
                ),
                # bcrypt hash of "agent-builder-service-dev-secret" (dev-only fixed secret, same
                # posture as every other seeded client).
                "secret_hash": "$2b$12$.UEMj1l2kSrQak6ckQhcXOGDFBxm1dyRpqA7rnc5oMXl2dzubTogK",
            },
        ],
    )


def downgrade() -> None:
    op.execute(clients.delete().where(clients.c.client_id == "agent-builder-service"))
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=OLD_EXAMPLE_SERVICE_SCOPES)
    )
