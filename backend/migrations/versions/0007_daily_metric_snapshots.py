"""Добавляет сохранённую историю дневных показателей.

Revision ID: 0007_daily_metric_snapshots
Revises: 0006_task_hierarchy
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_daily_metric_snapshots"
down_revision = "0006_task_hierarchy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "daily_metric_snapshots" in inspector.get_table_names():
        return
    op.create_table(
        "daily_metric_snapshots",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "local_date", name="uq_daily_metric_snapshot_date"),
    )
    op.create_index("ix_daily_metric_snapshots_workspace_date", "daily_metric_snapshots", ["workspace_id", "local_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_metric_snapshots_workspace_date", table_name="daily_metric_snapshots")
    op.drop_table("daily_metric_snapshots")
