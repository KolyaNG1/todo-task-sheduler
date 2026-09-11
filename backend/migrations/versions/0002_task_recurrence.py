"""Добавляет правило повторения обычной задаче.

Revision ID: 0002_task_recurrence
Revises: 0001_initial_schema
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_task_recurrence"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Историческая 0001 в ранних сборках уже строилась из текущих моделей.
    # Оставляем миграцию идемпотентной, чтобы чистая база и старая база обе
    # проходили обновление без ошибки дублирующего столбца.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tasks")}
    if "repeat_rule" not in columns:
        op.add_column("tasks", sa.Column("repeat_rule", sa.String(length=16), nullable=False, server_default="NONE"))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("repeat_rule")
