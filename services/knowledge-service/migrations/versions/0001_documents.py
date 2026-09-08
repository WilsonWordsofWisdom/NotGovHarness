"""enable the vector extension; create the documents table

`PGVectorStore`'s own chunk/embedding table is created and owned by LlamaIndex itself on first
use, the same "we provision the database, a library owns its own table in it" shape as Memory
(D-060) — this migration never touches that table, only the extension and this service's own
`documents` metadata table.

Revision ID: 0001
Revises:
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("ingested_by", sa.String(200), nullable=False),
        sa.Column("visibility", sa.String(10), nullable=False),
        sa.Column("minio_object_key", sa.String(500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("documents")
    op.execute("DROP EXTENSION IF EXISTS vector")
