from __future__ import annotations

import argparse

from planner.bootstrap import build_container


def main() -> None:
    parser = argparse.ArgumentParser(description="Служебные команды планировщика")
    parser.add_argument("command", choices=["init", "info"])
    args = parser.parse_args()
    container = build_container()
    container.initialize()
    if args.command == "info":
        print(f"Каталог артефактов: {container.artifacts.root}")
        print(f"SQLite: {container.artifacts.database_path}")
    else:
        print("База и начальная рабочая область готовы.")
