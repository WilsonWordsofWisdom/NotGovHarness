"""create suites table

Revision ID: 0001
Revises:
Create Date: 2026-08-30
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
        "suites",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("applies_to", JSONB, nullable=False),
        sa.Column("metrics", JSONB, nullable=True),
        sa.Column("redteam_config", JSONB, nullable=True),
        sa.Column("dataset_object_key", sa.String(500), nullable=True),
        sa.Column("case_count", sa.Integer, nullable=True),
        sa.Column("scan_findings", JSONB, nullable=False),
        sa.Column("published_by", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", "version", name="uq_suites_name_version"),
    )


def downgrade() -> None:
    op.drop_table("suites")
