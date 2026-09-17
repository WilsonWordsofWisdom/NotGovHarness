"""add judge_model to runs

The system-under-test model and the autorater/judge model are independently configurable
(previously both silently used `settings.llm_gateway_model`) — recording which judge model
produced a run's scores matters for spotting self-preference bias when they're the same model.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("judge_model", sa.String(200), nullable=False, server_default="qwen3.8"),
    )
    op.alter_column("runs", "judge_model", server_default=None)


def downgrade() -> None:
    op.drop_column("runs", "judge_model")
