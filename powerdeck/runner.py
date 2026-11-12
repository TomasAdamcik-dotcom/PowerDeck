"""Command execution abstraction (injectable for tests)."""
from __future__ import annotations

import subprocess
from typing import Protocol


class Runner(Protocol):
    def __call__(self, argv: list[str]) -> tuple[int, str]: ...


def system_runner(argv: list[str]) -> tuple[int, str]:
    """Run a command without a shell. Returns (returncode, stdout)."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return r.returncode, (r.stdout or "").strip() if r.returncode == 0 else (r.stderr or r.stdout or "").strip()
