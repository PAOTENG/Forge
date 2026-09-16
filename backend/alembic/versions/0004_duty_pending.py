"""Duty pending approval tokens.

Revision ID: 0004_duty_pending
Revises: 0003_duty_alerts
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_duty_pending"
down_revision = "0003_duty_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = inspect(op.get_bind()).get_table_names()
    if "duty_pending" in tables:
        return
    op.create_table(
        "duty_pending",
        sa.Column("token", sa.String(32), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE")),
        sa.Column("tool", sa.String(80), nullable=False, server_default=""),
        sa.Column("arguments", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("duty_pending")
