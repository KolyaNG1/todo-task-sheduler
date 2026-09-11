from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


ARTIFACT_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    root: Path

    @property
    def database_dir(self) -> Path:
        return self.root / "database"

    @property
    def database_path(self) -> Path:
        return self.database_dir / "planner.sqlite3"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def ensure(self) -> None:
        for relative in (
            "database", "backups", "portable", "exports", "imports/incoming",
            "imports/processed", "imports/rejected", "attachments", "logs",
            "agent_runs", "tmp",
        ):
            (self.root / relative).mkdir(parents=True, exist_ok=True)

    def read_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {}
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def write_manifest(self, *, application_version: str, schema_revision: str = "initial", extra: dict | None = None) -> None:
        payload = {
            **self.read_manifest(),
            "artifact_format_version": ARTIFACT_FORMAT_VERSION,
            "application_version": application_version,
            "schema_revision": schema_revision,
            "database_path": "database/planner.sqlite3",
            "updated_at": datetime.now(UTC).isoformat(),
            "last_operation_completed": True,
        }
        if extra:
            payload.update(extra)
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def backup_database(self, *, reason: str) -> Path | None:
        """Создаёт согласованную копию SQLite, не копируя отдельно WAL-файл."""
        if not self.database_path.exists():
            return None
        self.ensure()
        safe_reason = "".join(char if char.isalnum() or char in "-_" else "-" for char in reason).strip("-") or "manual"
        target = self.root / "backups" / f"planner-{safe_reason}-{datetime.now(UTC):%Y%m%d-%H%M%S}.sqlite3"
        with sqlite3.connect(self.database_path) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        with sqlite3.connect(target) as checked:
            result = checked.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            target.unlink(missing_ok=True)
            raise RuntimeError("Резервная копия SQLite не прошла проверку целостности")
        return target
