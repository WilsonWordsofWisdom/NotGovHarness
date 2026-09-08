"""grant example-service the Knowledge write scope; seed a second caller and knowledge-service

example-service stands in as the usual D-030 simulated caller. A *separate* new client, "bob",
is seeded specifically as a second caller for the visibility filter's test (D-068 — a "private"
document ingested by one caller must not appear to another). "alice" is deliberately not reused
for this: she is specifically the delegation-demo subject (RFC 8693 token exchange,
identity-service's own test_live_stack.py hardcodes her scope) — overloading her with an
unrelated harness's scope would couple two independent test concerns, the same "different trust
levels get separate identities" reasoning already applied to Approvals/HITL's `reviewer` client.
Separately, `knowledge-service` is seeded as its own real OAuth2 client with `llm_gateway:call` —
same shape as `mem0-service` (D-062/D-067): a real platform service calling another, not a
simulated future external caller.
See docs/superpowers/specs/2026-09-08-knowledge-rag-harness-design.md.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
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
    "guardrails:check llm_gateway:call memory:write knowledge:write"
)
OLD_EXAMPLE_SERVICE_SCOPES = (
    "upstream:call agentcard:sign registry:publish skill_registry:publish "
    "eval_registry:publish agent_gateway:call approvals:request sandbox:execute "
    "guardrails:check llm_gateway:call memory:write"
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
                "client_id": "bob",
                "spiffe_id": None,
                "allowed_scopes": "knowledge:write",
                # bcrypt hash of "bob-dev-secret" (dev-only fixed secret, same posture as every
                # other seeded client).
                "secret_hash": "$2b$12$znGjwhyJisuYHU8kpAFXQOLVmLmc6NFVmFk4ihtTIbuAiVRKd/9C.",
            },
            {
                "client_id": "knowledge-service",
                "spiffe_id": None,
                "allowed_scopes": "llm_gateway:call",
                # bcrypt hash of "knowledge-service-dev-secret".
                "secret_hash": "$2b$12$m02x5Hpk4i7DTnVnyBBUpOjXKmXMo7uIKJ4E7d0Vo9nkYTXsql3Ze",
            },
        ],
    )


def downgrade() -> None:
    op.execute(clients.delete().where(clients.c.client_id.in_(["bob", "knowledge-service"])))
    op.execute(
        clients.update()
        .where(clients.c.client_id == "example-service")
        .values(allowed_scopes=OLD_EXAMPLE_SERVICE_SCOPES)
    )
