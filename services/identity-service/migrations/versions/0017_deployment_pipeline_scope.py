"""grant example-service the deploy:create scope; seed deployment-pipeline-service as its own
OAuth2 client

`example-service` stands in as the usual D-030 simulated caller, gaining `deploy:create`.
`deployment-pipeline-service` is seeded as a genuine new identity (D-062/D-073 pattern), with
`evals:run` -- to call the eval-gate. Reading Agent Registry needs no scope.
See docs/superpowers/specs/2026-09-25-deployment-pipeline-harness-design.md.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
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
    "guardrails:check llm_gateway:call memory:write knowledge:write builder:build evals:run "
    "agent_runtime:invoke deploy:create"
)
OLD_EXAMPLE_SERVICE_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call memory:write knowledge:write builder:build evals:run "
    "agent_runtime:invoke"
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
                "client_id": "deployment-pipeline-service",
                "spiffe_id": None,
                "allowed_scopes": "evals:run",
                # bcrypt hash of "deployment-pipeline-service-dev-secret".
                "secret_hash": "$2b$12$68Yvla3bQmWL.4yXLmHQs.Cp6RrEfCBhsvGZkUI39MCoo2GlruNVa",
            },
        ],
    )


def downgrade() -> None:
    op.execute(clients.delete().where(clients.c.client_id == "deployment-pipeline-service"))
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=OLD_EXAMPLE_SERVICE_SCOPES)
    )
