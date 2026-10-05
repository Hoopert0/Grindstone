"""Run helper programs (git, java, javac, taskkill, powershell) without a console window.

The panel runs under pythonw (no console), so on Windows every console program it starts would
flash a black window - several at start-up and one per version check. CREATE_NO_WINDOW stops
that; elsewhere this is plain subprocess.run.

git never asks for a GitHub sign-in from here (no prompt, no sign-in window): background checks
just fail quietly when not signed in. Only the start-up console (INTERACTIVE = True, set by
lumberjack.start) may ask once.
"""
import os
import subprocess
import sys

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0     # CREATE_NO_WINDOW
INTERACTIVE = False


def run(cmd, **kw):
    kw["creationflags"] = kw.get("creationflags", 0) | NO_WINDOW
    if cmd and str(cmd[0]).lower().endswith("git") and not INTERACTIVE:
        env = dict(kw.get("env") or os.environ)
        env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_ASKPASS="", SSH_ASKPASS="")
        kw["env"] = env
    return subprocess.run(cmd, **kw)
