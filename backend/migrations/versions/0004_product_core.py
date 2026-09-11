"""Добавляет цели и состояние календарных блоков.

Revision ID: 0004_product_core
Revises: 0003_task_color
Create Date: 2026-09-10
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_product_core"
down_revision = "0003_task_color"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "goals" not in inspector.get_table_names():
        op.create_table(
            "goals",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("direction_id", sa.String(length=36), sa.ForeignKey("directions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("title", sa.String(length=220), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("color", sa.String(length=9), nullable=True),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
    task_columns = _columns("tasks")
    if "goal_id" not in task_columns:
        op.add_column("tasks", sa.Column("goal_id", sa.String(length=36), nullable=True))
    block_columns = _columns("schedule_blocks")
    for name in ("planner_run_id", "planner_proposal_id"):
        if name not in block_columns:
            op.add_column("schedule_blocks", sa.Column(name, sa.String(length=36), nullable=True))
    for name in ("completed_at", "skipped_at"):
        if name not in block_columns:
            op.add_column("schedule_blocks", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    session_columns = _columns("work_sessions")
    if "direction_id" not in session_columns:
        op.add_column("work_sessions", sa.Column("direction_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    # SQLite поддерживает удаление столбцов неполно; безопасный откат схемы
    # выполняется через резервную копию, созданную до обновления.
    pass
