"""grant example-service the Evals-run scope; seed evals-runner-service as its own OAuth2 client

`example-service` stands in as the usual D-030 simulated caller, gaining `evals:run`.
`evals-runner-service` is seeded as a genuine new identity (D-062/D-073 pattern), with just
`llm_gateway:call` -- reading Eval Registry needs no scope (its GET endpoints are open to any
caller).
See docs/superpowers/specs/2026-09-18-evals-runner-harness-design.md.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
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
    "guardrails:check llm_gateway:call memory:write knowledge:write builder:build evals:run"
)
OLD_EXAMPLE_SERVICE_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call memory:write knowledge:write builder:build"
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
                "client_id": "evals-runner-service",
                "spiffe_id": None,
                "allowed_scopes": "llm_gateway:call",
                # bcrypt hash of "evals-runner-service-dev-secret".
                "secret_hash": "$2b$12$3CgKAD12PfB9lwmR52cfaOD7viepfyI3qlg5sklb74Je3Mm6vkv4G",
            },
        ],
    )


def downgrade() -> None:
    op.execute(clients.delete().where(clients.c.client_id == "evals-runner-service"))
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=OLD_EXAMPLE_SERVICE_SCOPES)
    )
