"""grant example-service the Agent Runtime invoke scope; seed agent-runtime as its own OAuth2 client

`example-service` stands in as the usual D-030 simulated caller, gaining `agent_runtime:invoke`
so this branch is independently testable before anything else (Evals Runner's new target,
Deployment Pipeline) consumes it. `agent-runtime` is seeded as its own real OAuth2 client
(D-062/D-087) -- one shared identity reused by every deployed instance, not provisioned per
deployment -- with `llm_gateway:call` for its own outbound kickoff calls.
See docs/superpowers/specs/2026-09-25-deployment-pipeline-harness-design.md.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
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
    "agent_runtime:invoke"
)
OLD_EXAMPLE_SERVICE_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call memory:write knowledge:write builder:build evals:run"
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
                "client_id": "agent-runtime",
                "spiffe_id": None,
                "allowed_scopes": "llm_gateway:call",
                # bcrypt hash of "agent-runtime-dev-secret".
                "secret_hash": "$2b$12$yJOKFeo.FpVfpg2TE8C6f.vMFkIwXXnITN/A85NIs4xDwjgbbtjzW",
            },
        ],
    )


def downgrade() -> None:
    op.execute(clients.delete().where(clients.c.client_id == "agent-runtime"))
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=OLD_EXAMPLE_SERVICE_SCOPES)
    )
