"""create approvals table

Revision ID: 0001
Revises:
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workflow_id", sa.String(200), nullable=False, unique=True),
        sa.Column("requester", sa.String(200), nullable=False),
        sa.Column("action_type", sa.String(200), nullable=False),
        sa.Column("action_payload", JSONB, nullable=False),
        sa.Column("risk_level", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decision_payload", JSONB, nullable=True),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("approvals")
