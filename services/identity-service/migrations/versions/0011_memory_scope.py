"""grant example-service the Memory write scope; seed the mem0-service client

example-service stands in as the risk-taking caller (D-030 pattern) for `memory:write`, the same
as every prior "future caller" grant. Separately, `mem0-service` is seeded as its own real OAuth2
client with `llm_gateway:call` — D-062: this is the first harness where a *platform service*
calls *another platform service*, not a simulated future external caller, so it gets a real
registered identity rather than borrowing example-service's.
See docs/superpowers/specs/2026-09-08-memory-harness-design.md.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
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
    "guardrails:check llm_gateway:call memory:write"
)
OLD_EXAMPLE_SERVICE_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call"
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
                "client_id": "mem0-service",
                "spiffe_id": None,
                "allowed_scopes": "llm_gateway:call",
                # bcrypt hash of "mem0-service-dev-secret" (dev-only fixed secret, same posture
                # as every other seeded client).
                "secret_hash": "$2b$12$/gVGHUrzuHLqFa5SbFRRVeAVyj6HeIPCUKzieY4zH/2dUaqanKB6a",
            }
        ],
    )


def downgrade() -> None:
    op.execute(clients.delete().where(clients.c.client_id == "mem0-service"))
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=OLD_EXAMPLE_SERVICE_SCOPES)
    )
