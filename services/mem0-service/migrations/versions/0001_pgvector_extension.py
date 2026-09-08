"""enable the vector extension

No application-owned tables in this database — Mem0's pgvector backend creates and owns its own
table(s) here. This migration's only job is making the extension available.

Revision ID: 0001
Revises:
Create Date: 2026-09-08
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
