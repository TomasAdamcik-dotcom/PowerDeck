"""Command execution abstraction (injectable for tests)."""
from __future__ import annotations

import shutil
import subprocess
from typing import Protocol


PKEXEC_TIMEOUT = 300
# Debian keeps rfkill & co. in /usr/sbin, which is not on a normal user's PATH there
SBIN_DIRS = "/usr/sbin:/sbin"


def find(cmd: str) -> str | None:
    """Full path of a command: from PATH, else from the fixed system sbin directories."""
    return shutil.which(cmd) or shutil.which(cmd, path=SBIN_DIRS)


class Runner(Protocol):
    def __call__(self, argv: list[str]) -> tuple[int, str]: ...


def system_runner(argv: list[str]) -> tuple[int, str]:
    """Run a command without a shell. Returns (returncode, stdout)."""
    if argv and "/" not in argv[0]:
        argv = [find(argv[0]) or argv[0], *argv[1:]]
    try:
        # pkexec waits for the user to type a password; don't kill the dialog after a minute
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=PKEXEC_TIMEOUT if argv[0].endswith("/pkexec") else 60)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    if r.returncode == 0:
        return 0, (r.stdout or "").strip()
    out = (r.stderr or r.stdout or "").strip()
    if argv[0].endswith("/pkexec") and r.returncode in (126, 127) and not out:
        out = "authorization dismissed or denied"
    return r.returncode, out
