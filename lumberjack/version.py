"""Which version of the bot is running, and is there a newer one on GitHub?

The version is the number of commits that changed the bot's code - save uploads, learned
templates and map updates don't count - so every PC on the same code shows the same number
(e.g. v142). The control panel shows it and checks GitHub every few minutes.
"""
import subprocess
import threading
import time
from pathlib import Path
from lumberjack import procs

REPO = Path(__file__).resolve().parents[1]
CODE = ["lumberjack", "lumberjack.bat", "setup_pc.ps1", "link_save.ps1",
        ":!lumberjack/assets/templates", ":!lumberjack/assets/maps"]
CHECK_EVERY_S = 600               # (each check is a quiet git fetch)


def _git(*args, timeout=10):
    r = procs.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip() if r.returncode == 0 else None


def code_version(ref="HEAD"):
    """(number, short hash) of the newest code commit reachable from `ref`, or (None, None)."""
    try:
        n = _git("rev-list", "--count", ref, "--", *CODE)
        h = _git("log", "-1", "--format=%h", ref, "--", *CODE)
    except (OSError, subprocess.SubprocessError):
        return None, None
    return (int(n), h) if n and n.isdigit() else (None, None)


RUNNING = code_version()          # what this process loaded at start
_latest = {"t": 0.0, "version": None, "checking": False}


def _check():
    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "main"
        if _git("fetch", "-q", "origin", branch, timeout=30) is not None:
            _latest["version"] = code_version(f"origin/{branch}")[0]
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        _latest["t"] = time.monotonic()
        _latest["checking"] = False


def status():
    """{'version': 142, 'commit': 'abc1234', 'latest': 145 or None, 'update': bool}.
    Starts a background check against GitHub at most every CHECK_EVERY_S."""
    if not _latest["checking"] and time.monotonic() - _latest["t"] > CHECK_EVERY_S:
        _latest["checking"] = True
        threading.Thread(target=_check, name="version-check", daemon=True).start()
    n, h = RUNNING
    latest = _latest["version"]
    return {"version": n, "commit": h, "latest": latest,
            "update": bool(n is not None and latest is not None and latest > n)}


def label():
    n, h = RUNNING
    return f"v{n} ({h})" if n is not None else "version unknown"
