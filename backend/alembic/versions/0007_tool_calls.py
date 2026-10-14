"""Append-only hashed tool-call audit log.

Revision ID: 0007_tool_calls
Revises: 0006_file_backups
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0007_tool_calls"
down_revision = "0006_file_backups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = inspect(op.get_bind()).get_table_names()
    if "tool_calls" in tables:
        return
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(80), nullable=False, server_default=""),
        sa.Column("tool_call_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("agent_name", sa.String(80), nullable=False, server_default=""),
        sa.Column("arguments", sa.Text(), nullable=False, server_default=""),
        sa.Column("output", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(40), nullable=False, server_default=""),
        sa.Column("state", sa.String(16), nullable=False, server_default="start"),
        sa.Column("decision", sa.String(16), nullable=False, server_default=""),
        sa.Column("prev_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tool_calls_session_id", "tool_calls", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_calls_session_id", table_name="tool_calls")
    op.drop_table("tool_calls")
