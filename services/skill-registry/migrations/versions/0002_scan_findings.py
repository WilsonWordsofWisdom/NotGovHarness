"""add scan_findings column

Advisory (non-blocking) findings from the malicious-content scan at publish time. See
services/skill-registry/src/skill_registry/scan.py.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column("scan_findings", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("skills", "scan_findings")
