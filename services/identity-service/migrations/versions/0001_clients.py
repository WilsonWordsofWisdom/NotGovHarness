"""create clients table

Revision ID: 0001
Revises:
Create Date: 2026-08-27
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
        "clients",
        sa.Column("client_id", sa.String(200), primary_key=True),
        sa.Column("spiffe_id", sa.String(300), nullable=True),
        sa.Column("allowed_scopes", sa.String(500), nullable=False, server_default=""),
        sa.Column("secret_hash", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("clients")
