"""Добавляет дерево задач и порядок дочерних чекпоинтов.

Revision ID: 0006_task_hierarchy
Revises: 0005_goal_task_sequence
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_task_hierarchy"
down_revision = "0005_goal_task_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("tasks")}
    if "parent_task_id" not in columns:
        op.add_column("tasks", sa.Column("parent_task_id", sa.String(length=36), nullable=True))
    if "child_position" not in columns:
        op.add_column("tasks", sa.Column("child_position", sa.Integer(), nullable=False, server_default="0"))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("tasks")}
    if "ix_tasks_parent_position" not in indexes:
        op.create_index("ix_tasks_parent_position", "tasks", ["parent_task_id", "child_position"])


def downgrade() -> None:
    pass
