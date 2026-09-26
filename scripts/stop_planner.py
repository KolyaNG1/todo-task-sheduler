"""Останавливает прежние экземпляры планировщика на закреплённых портах."""

from __future__ import annotations

import re
import subprocess
import sys
import time


def listening_processes(ports: set[int]) -> set[int]:
    output = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    result: set[int] = set()
    for line in output.splitlines():
        match = re.search(r"\s(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]):(\d+)\s+.*\sLISTENING\s+(\d+)\s*$", line)
        if match and int(match.group(1)) in ports:
            result.add(int(match.group(2)))
    return result


def main() -> int:
    ports = {int(value) for value in sys.argv[1:]}
    for process_id in listening_processes(ports):
        subprocess.run(
            ["taskkill", "/PID", str(process_id), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    deadline = time.monotonic() + 5
    while listening_processes(ports) and time.monotonic() < deadline:
        time.sleep(0.1)
    remaining = listening_processes(ports)
    if remaining:
        print(
            "Не удалось остановить прежний экземпляр планировщика "
            f"(процессы: {', '.join(map(str, sorted(remaining)))}).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
