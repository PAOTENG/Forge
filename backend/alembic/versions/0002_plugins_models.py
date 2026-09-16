"""Plugins and custom LLM models.

Revision ID: 0002_plugins_models
Revises: 0001_initial
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_plugins_models"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = inspect(op.get_bind()).get_table_names()
    if "installed_plugins" not in tables:
        op.create_table(
            "installed_plugins",
            sa.Column("plugin_id", sa.String(80), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("installed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "custom_models" not in tables:
        op.create_table(
            "custom_models",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("provider", sa.String(40), nullable=False, server_default="custom"),
            sa.Column("label", sa.String(80), nullable=False),
            sa.Column("model_id", sa.String(160), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("custom_models")
    op.drop_table("installed_plugins")
