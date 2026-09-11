from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    artifacts_dir: Path
    app_name: str = "Планировщик недели"
    api_prefix: str = "/api/v1"
    sqlite_busy_timeout_ms: int = 5_000

    @classmethod
    def from_environment(cls) -> "Settings":
        project_root = _project_root()
        configured = os.getenv("PLANNER_ARTIFACTS_DIR")
        artifacts_dir = Path(configured).expanduser().resolve() if configured else project_root / "artifacts"
        return cls(project_root=project_root, artifacts_dir=artifacts_dir)
