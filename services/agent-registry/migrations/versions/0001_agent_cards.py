"""create agent_cards table

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
        "agent_cards",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("provider", JSONB, nullable=True),
        sa.Column("capabilities", JSONB, nullable=False),
        sa.Column("default_input_modes", JSONB, nullable=True),
        sa.Column("default_output_modes", JSONB, nullable=True),
        sa.Column("skills", JSONB, nullable=True),
        sa.Column("security_schemes", JSONB, nullable=True),
        sa.Column("security", JSONB, nullable=True),
        sa.Column("interfaces", JSONB, nullable=True),
        sa.Column("extensions", JSONB, nullable=True),
        sa.Column("card", JSONB, nullable=False),
        sa.Column("signing_algorithm", sa.String(20), nullable=False),
        sa.Column("signing_key_id", sa.String(100), nullable=False),
        sa.Column("signature_value", sa.String(4000), nullable=False),
        sa.Column("published_by", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", "version", name="uq_agent_cards_name_version"),
    )


def downgrade() -> None:
    op.drop_table("agent_cards")
