"""Group publications of one upload (several networks) by batch_id.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("publications") as batch:
        batch.add_column(sa.Column("batch_id", sa.String(length=36), nullable=True))
        batch.create_index("ix_publications_batch_id", ["batch_id"])
    # Earlier publications were single-network uploads: each is its own batch.
    op.execute("UPDATE publications SET batch_id = id WHERE batch_id IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("publications") as batch:
        batch.drop_index("ix_publications_batch_id")
        batch.drop_column("batch_id")
