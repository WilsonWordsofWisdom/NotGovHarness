"""add card_version to builds (D-103: revise bumps this; every pre-existing row was 0.1.0)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13
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
        "builds",
        sa.Column("card_version", sa.String(32), nullable=False, server_default="0.1.0"),
    )


def downgrade() -> None:
    op.drop_column("builds", "card_version")
