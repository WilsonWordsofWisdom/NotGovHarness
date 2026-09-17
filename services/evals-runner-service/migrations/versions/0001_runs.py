"""create the runs table

Revision ID: 0001
Revises:
Create Date: 2026-09-18
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
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("requester", sa.String(200), nullable=False),
        sa.Column("suite_name", sa.String(128), nullable=False),
        sa.Column("suite_version", sa.String(50), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("case_results", sa.JSON, nullable=False),
        sa.Column("pass_count", sa.Integer, nullable=False),
        sa.Column("fail_count", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("runs")
