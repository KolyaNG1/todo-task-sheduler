from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from planner.application.services import PlannerService
from planner.infrastructure.artifacts import ArtifactPaths
from planner.infrastructure.database import create_engine_for_paths, create_session_factory
from planner.infrastructure.models import Base
from planner.infrastructure.settings import Settings


@dataclass(slots=True)
class Container:
    settings: Settings
    artifacts: ArtifactPaths
    engine: Engine
    session_factory: sessionmaker[Session]

    def initialize(self) -> None:
        self.artifacts.ensure()
        Base.metadata.create_all(self.engine)
        self._apply_compatibility_migrations()
        self._normalize_legacy_datetimes()
        with self.session_factory.begin() as session:
            PlannerService(session).initialize_workspace()
        self.artifacts.write_manifest(application_version="0.3.0", schema_revision="0006", extra={"utc_normalized_at": datetime.now(UTC).isoformat()})

    def _apply_compatibility_migrations(self) -> None:
        """Дополняет локальную SQLite обратно совместимо и с резервной копией."""
        required_columns = {
            "tasks": {"repeat_rule", "color", "goal_id", "goal_position", "parent_task_id", "child_position"},
            "schedule_blocks": {"planner_run_id", "planner_proposal_id", "completed_at", "skipped_at"},
            "work_sessions": {"direction_id"},
        }
        with self.engine.connect() as connection:
            existing_tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = "goals" not in existing_tables
            for table, columns in required_columns.items():
                if table in existing_tables:
                    actual = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
                    missing = missing or not columns.issubset(actual)
        if missing:
            self.artifacts.backup_database(reason="before-schema-0006")
        with self.engine.begin() as connection:
            # Новые таблицы create_all добавляет на чистой базе. Для старой базы
            # создаём их явно, а недостающие столбцы добавляем без удаления строк.
            Base.metadata.tables["goals"].create(connection, checkfirst=True)
            definitions = {
                "tasks": {
                    "repeat_rule": "VARCHAR(16) NOT NULL DEFAULT 'NONE'",
                    "color": "VARCHAR(9)",
                    "goal_id": "VARCHAR(36)",
                    "goal_position": "INTEGER NOT NULL DEFAULT 0",
                    "parent_task_id": "VARCHAR(36)",
                    "child_position": "INTEGER NOT NULL DEFAULT 0",
                },
                "schedule_blocks": {
                    "planner_run_id": "VARCHAR(36)",
                    "planner_proposal_id": "VARCHAR(36)",
                    "completed_at": "DATETIME",
                    "skipped_at": "DATETIME",
                },
                "work_sessions": {"direction_id": "VARCHAR(36)"},
            }
            for table, columns in definitions.items():
                actual = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in actual:
                        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_tasks_parent_position ON tasks (parent_task_id, child_position)")

    def _normalize_legacy_datetimes(self) -> None:
        """Один раз переводит старые наивные локальные даты в UTC.

        До версии 0.2.0 приложение записывало локальное московское время в
        SQLite, а затем читало его как UTC. Маркер в manifest исключает повторный
        сдвиг новых записей.
        """
        if self.artifacts.read_manifest().get("utc_normalized_at"):
            return
        self.artifacts.backup_database(reason="before-utc-normalization")
        columns = {
            "actors": (None, ("created_at", "updated_at")),
            "workspaces": (None, ("created_at", "updated_at", "deleted_at")),
            "workspace_members": ("workspace_id", ("created_at", "updated_at")),
            "workspace_state": ("workspace_id", ("created_at", "updated_at", "notification_scan_at")),
            "directions": ("workspace_id", ("created_at", "updated_at", "default_deadline_at")),
            "labels": ("workspace_id", ("created_at", "updated_at")),
            "goals": ("workspace_id", ("created_at", "updated_at", "deadline_at", "completed_at", "deleted_at")),
            "tasks": ("workspace_id", ("created_at", "updated_at", "deadline_at", "earliest_start_at", "completed_at", "deleted_at")),
            "task_labels": (None, ("created_at", "updated_at")),
            "availability_profiles": ("workspace_id", ("created_at", "updated_at")),
            "availability_profile_slots": (None, ("created_at", "updated_at")),
            "availability_overrides": ("workspace_id", ("created_at", "updated_at")),
            "availability_override_slots": (None, ("created_at", "updated_at")),
            "fixed_event_rules": ("workspace_id", ("created_at", "updated_at")),
            "schedule_blocks": ("workspace_id", ("created_at", "updated_at", "start_at", "end_at", "completed_at", "skipped_at")),
            "planner_runs": ("workspace_id", ("created_at", "updated_at", "horizon_start", "horizon_end", "expires_at")),
            "planner_proposals": (None, ("created_at", "updated_at", "start_at", "end_at")),
            "work_sessions": ("workspace_id", ("created_at", "updated_at", "started_at", "ended_at")),
            "work_session_segments": (None, ("created_at", "updated_at", "start_at", "end_at")),
            "notifications": ("workspace_id", ("created_at", "updated_at", "read_at", "resolved_at")),
            "audit_log": ("workspace_id", ("created_at", "updated_at")),
            "outbox_events": ("workspace_id", ("created_at", "updated_at", "occurred_at", "processed_at")),
        }
        with self.engine.begin() as connection:
            tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
            zones = {row[0]: row[1] for row in connection.exec_driver_sql("SELECT id, timezone FROM workspaces")}
            default_zone = next(iter(zones.values()), "Europe/Moscow")
            for table, (workspace_column, names) in columns.items():
                if table not in tables:
                    continue
                actual = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
                usable = [name for name in names if name in actual]
                if not usable:
                    continue
                selected = ", ".join(["id"] + ([workspace_column] if workspace_column else []) + usable)
                for row in connection.exec_driver_sql(f"SELECT {selected} FROM {table}").mappings():
                    zone_name = zones.get(row.get(workspace_column), default_zone) if workspace_column else default_zone
                    updates = {}
                    for name in usable:
                        value = row[name]
                        if value is None:
                            continue
                        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
                        if parsed.tzinfo is None:
                            updates[name] = parsed.replace(tzinfo=ZoneInfo(zone_name)).astimezone(UTC).replace(tzinfo=None)
                    if updates:
                        assignments = ", ".join(f"{name} = :{name}" for name in updates)
                        connection.exec_driver_sql(f"UPDATE {table} SET {assignments} WHERE id = :id", {**updates, "id": row["id"]})


def build_container(settings: Settings | None = None) -> Container:
    resolved = settings or Settings.from_environment()
    artifacts = ArtifactPaths(resolved.artifacts_dir.resolve())
    engine = create_engine_for_paths(artifacts, resolved)
    return Container(resolved, artifacts, engine, create_session_factory(engine))
