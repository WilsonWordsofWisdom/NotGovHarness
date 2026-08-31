"""create executions table

Revision ID: 0001
Revises:
Create Date: 2026-08-31
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
        "executions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("requester", sa.String(200), nullable=False),
        sa.Column("language", sa.String(50), nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stdout", sa.Text, nullable=False),
        sa.Column("stderr", sa.Text, nullable=False),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("timeout_seconds", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("executions")
