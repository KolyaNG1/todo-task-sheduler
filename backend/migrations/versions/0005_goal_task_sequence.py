"""Добавляет фиксированную последовательность задач цели.

Revision ID: 0005_goal_task_sequence
Revises: 0004_product_core
Create Date: 2026-09-10
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_goal_task_sequence"
down_revision = "0004_product_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tasks")}
    if "goal_position" not in columns:
        op.add_column("tasks", sa.Column("goal_position", sa.Integer(), nullable=False, server_default="0"))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("tasks")}
    if "ix_tasks_goal_position" not in indexes:
        op.create_index("ix_tasks_goal_position", "tasks", ["goal_id", "goal_position"])


def downgrade() -> None:
    pass
