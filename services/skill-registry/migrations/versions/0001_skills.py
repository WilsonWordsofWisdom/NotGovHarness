"""create skills table

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
        "skills",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column("license", sa.String(200), nullable=True),
        sa.Column("compatibility", sa.String(500), nullable=True),
        sa.Column("metadata", JSONB, nullable=False),
        sa.Column("allowed_tools", sa.String(2000), nullable=True),
        sa.Column("skill_md", sa.Text, nullable=False),
        sa.Column("bundle_object_key", sa.String(500), nullable=False),
        sa.Column("bundle_size_bytes", sa.Integer, nullable=False),
        sa.Column("published_by", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", "version", name="uq_skills_name_version"),
    )


def downgrade() -> None:
    op.drop_table("skills")
