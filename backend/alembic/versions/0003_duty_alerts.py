"""Duty alerts.

Revision ID: 0003_duty_alerts
Revises: 0002_plugins_models
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_duty_alerts"
down_revision = "0002_plugins_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = inspect(op.get_bind()).get_table_names()
    if "duty_alerts" in tables:
        return
    op.create_table(
        "duty_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(240), nullable=False, server_default="未命名告警"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("service", sa.String(120), nullable=False, server_default=""),
        sa.Column("severity", sa.String(32), nullable=False, server_default="warning"),
        sa.Column("source", sa.String(40), nullable=False, server_default="webhook"),
        sa.Column("stack", sa.Text(), nullable=False, server_default=""),
        sa.Column("link", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("duty_alerts")
