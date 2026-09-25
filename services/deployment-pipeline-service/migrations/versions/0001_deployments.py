"""create the deployments table

Revision ID: 0001
Revises:
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("requester", sa.String(200), nullable=False),
        sa.Column("agent_name", sa.String(128), nullable=False),
        sa.Column("agent_version", sa.String(50), nullable=False),
        sa.Column("container_id", sa.String(64), nullable=True),
        sa.Column("host_port", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="deploying"),
        sa.Column("eval_run_id", sa.String(36), nullable=True),
        sa.Column("error", sa.String(2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("deployments")
