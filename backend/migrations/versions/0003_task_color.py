"""Добавляет личный цвет задаче.

Revision ID: 0003_task_color
Revises: 0002_task_recurrence
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_task_color"
down_revision = "0002_task_recurrence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tasks")}
    if "color" not in columns:
        op.add_column("tasks", sa.Column("color", sa.String(length=9), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("color")
