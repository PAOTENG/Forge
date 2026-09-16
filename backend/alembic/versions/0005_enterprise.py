"""Users, auth sessions, duty assignment, audit actor.

Revision ID: 0005_enterprise
Revises: 0004_duty_pending
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0005_enterprise"
down_revision = "0004_duty_pending"
branch_labels = None
depends_on = None


def _tables():
    return inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("username", sa.String(40), nullable=False, unique=True),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("role", sa.String(20), nullable=False, server_default="engineer"),
            sa.Column("display_name", sa.String(80), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("token", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "duty_alerts" in tables:
        cols = _columns("duty_alerts")
        if "assigned_to" not in cols:
            op.add_column("duty_alerts", sa.Column("assigned_to", sa.String(40), server_default=""))
        if "acked_by" not in cols:
            op.add_column("duty_alerts", sa.Column("acked_by", sa.String(40), server_default=""))
        if "acked_at" not in cols:
            op.add_column("duty_alerts", sa.Column("acked_at", sa.DateTime(timezone=True)))
    if "audits" in tables and "actor" not in _columns("audits"):
        op.add_column("audits", sa.Column("actor", sa.String(40), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("audits", "actor")
    op.drop_column("duty_alerts", "acked_at")
    op.drop_column("duty_alerts", "acked_by")
    op.drop_column("duty_alerts", "assigned_to")
    op.drop_table("auth_sessions")
    op.drop_table("users")
