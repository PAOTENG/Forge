"""Initial Forge schema with pgvector.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _tables():
    return inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    tables = _tables()

    if "sessions" not in tables:
        op.create_table(
            "sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("title", sa.String(200), nullable=False, server_default="New session"),
            sa.Column("workspace_path", sa.Text(), nullable=False),
            sa.Column("model", sa.String(120), nullable=False, server_default=""),
            sa.Column("mode", sa.String(20), nullable=False, server_default="build"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    else:
        cols = _columns("sessions")
        if "updated_at" not in cols:
            op.add_column(
                "sessions",
                sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            )

    if "messages" not in tables:
        op.create_table(
            "messages",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "session_id",
                sa.String(36),
                sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(32), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False, server_default=""),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_messages_session_id", "messages", ["session_id"])
    else:
        cols = _columns("messages")
        if "kind" not in cols:
            op.add_column(
                "messages",
                sa.Column("kind", sa.String(32), nullable=False, server_default=""),
            )

    if "audits" not in tables:
        op.create_table(
            "audits",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "session_id",
                sa.String(36),
                sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("tool_name", sa.String(80), nullable=False),
            sa.Column("arguments", sa.Text(), nullable=False, server_default=""),
            sa.Column("decision", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_audits_session_id", "audits", ["session_id"])

    if "agent_states" not in tables:
        op.create_table(
            "agent_states",
            sa.Column(
                "session_id",
                sa.String(36),
                sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("input_items", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column("pending_tool", sa.Text(), nullable=False, server_default=""),
            sa.Column("pending_arguments", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "file_chunks" not in tables:
        op.execute(
            """
            CREATE TABLE file_chunks (
                id VARCHAR(36) PRIMARY KEY,
                workspace_key VARCHAR(64) NOT NULL,
                rel_path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding vector(1024),
                created_at TIMESTAMPTZ DEFAULT now(),
                CONSTRAINT uq_file_chunks_ws_path_idx UNIQUE (workspace_key, rel_path, chunk_index)
            )
            """
        )
        op.create_index("ix_file_chunks_workspace_key", "file_chunks", ["workspace_key"])
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_file_chunks_content_trgm "
            "ON file_chunks USING gin (content gin_trgm_ops)"
        )


def downgrade() -> None:
    op.drop_table("file_chunks")
    op.drop_table("agent_states")
    op.drop_table("audits")
    op.drop_table("messages")
    op.drop_table("sessions")
