"""create the builds table

Revision ID: 0001
Revises:
Create Date: 2026-09-08
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
        "builds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("requester", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("task", sa.Text, nullable=False),
        sa.Column("kickoff_raw_output", sa.Text, nullable=False),
        sa.Column("tool_was_called", sa.Boolean, nullable=False),
        sa.Column("agent_card_id", sa.String(36), nullable=False),
        sa.Column("eval_suite_names", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("builds")
