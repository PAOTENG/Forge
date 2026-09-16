"""File backups for duty rollback.

Revision ID: 0006_file_backups
Revises: 0005_enterprise
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0006_file_backups"
down_revision = "0005_enterprise"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = inspect(op.get_bind()).get_table_names()
    if "file_backups" in tables:
        return
    op.create_table(
        "file_backups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rel_path", sa.String(500), nullable=False, server_default=""),
        sa.Column("action", sa.String(20), nullable=False, server_default="write"),
        sa.Column("existed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("actor", sa.String(40), nullable=False, server_default=""),
        sa.Column("model", sa.String(80), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("blob_dir", sa.String(400), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_file_backups_session_id", "file_backups", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_file_backups_session_id", table_name="file_backups")
    op.drop_table("file_backups")
