"""Ожидает готовности API и статического интерфейса при локальном запуске."""

from __future__ import annotations

import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


def ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=1) as response:  # noqa: S310 -- только localhost из bat
            return 200 <= response.status < 400
    except (OSError, URLError):
        return False


def main() -> int:
    urls = sys.argv[1:]
    if not urls:
        return 1
    for _ in range(20):
        if all(ready(url) for url in urls):
            return 0
        time.sleep(1)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
