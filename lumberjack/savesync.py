"""(Retired: SYNC = False) Share the 2009scape singleplayer save between PCs through this git repo.
Each PC now keeps its own save in the game's folders (site_fixes.unlink_save undid the links);
what's left in use here: git identity, wait_for_exit, REPO.

The save folders (players, playerstats, eco, serverstore) live in <repo>/save and the
game's data folder links to them (see link_save.ps1). What the bot learned in game (hover
text, item icons, HP digits, monster/loot names - lumberjack/assets/templates) travels with
the save, so every PC knows it. Around each play session:

    before launch:  git pull  -> refuse if another PC holds the lock -> write lock -> push
    after the game client closes:  remove lock -> commit save -> push

    python -m lumberjack.savesync status|pull|push|release
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from lumberjack import procs

SYNC = False      # the character save is no longer shared through the repo (each PC keeps its own)
REPO = Path(__file__).resolve().parents[1]
SAVE = REPO / "save"
LOCK = SAVE / "in_use.txt"
LOCK_HOURS = 8
SHARED = ["save", "lumberjack/assets/templates", "lumberjack/assets/places.json",
          "lumberjack/assets/fishing_spots.json"]   # what a sync commits


def git(*args, check=True):
    return procs.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=check)


def has_remote():
    return bool(git("remote", check=False).stdout.strip())


def ensure_identity():
    """A fresh PC has no git name/email, and committing the lock would fail without one."""
    if not git("config", "user.email", check=False).stdout.strip():
        git("config", "user.name", "Grindstone bot")
        git("config", "user.email", "lumberjack-bot@localhost")


def commit_templates():
    """Commit templates learned on this PC, so pulling can't trip over them (an untracked file
    the other PC also learned would block the pull)."""
    paths = [p for p in SHARED[1:] if (REPO / p).exists()]
    if not paths:
        return                       # (a bare "git add -A" would stage the whole repo)
    git("add", "-A", *paths)
    if git("diff", "--cached", "--quiet", check=False).returncode:
        git("commit", "-q", "-m", f"templates/places: learned on {socket.gethostname()}")


def pull():
    if has_remote():
        commit_templates()
        # -X theirs: in a rebase "theirs" is our own commits - if both PCs learned the same
        # template, this PC's copy wins instead of stopping on a conflict
        r = git("pull", "--rebase", "-X", "theirs", "--autostash", "-q", check=False)
        if r.returncode:
            git("rebase", "--abort", check=False)   # never leave the repo half-rebased
            print("git pull failed:\n" + r.stderr)
            return False
    return True


def commit_and_push(message):
    git("add", "-A", *[p for p in SHARED if (REPO / p).exists()] or ["save"])
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return True  # nothing changed
    git("commit", "-q", "-m", message)
    if has_remote():
        r = git("push", "-q", check=False)
        if r.returncode:
            # someone else pushed meanwhile (e.g. code changes) - rebase and retry once
            if git("pull", "--rebase", "-X", "theirs", "-q", check=False).returncode:
                git("rebase", "--abort", check=False)
            r = git("push", "-q", check=False)
            if r.returncode:
                print("git push failed:\n" + r.stderr)
                return False
    return True


def lock_holder():
    try:
        host, stamp = LOCK.read_text().strip().split("|")
        return host, (time.time() - float(stamp)) / 3600
    except (OSError, ValueError):
        return None, None


def acquire(force=False):
    """Before playing. Returns an error message, or None when it's ours."""
    if not SYNC or not SAVE.exists():
        return None  # save not shared yet (link_save.ps1 not run) - nothing to guard
    ensure_identity()
    if not pull():
        return "Couldn't fetch the latest save from GitHub (offline?). Add --force to play anyway."
    me = socket.gethostname()
    host, age = lock_holder()
    if host and host != me and age < LOCK_HOURS and not force:
        return (f"The save is in use on '{host}' (since {age:.1f} h ago). Close the game there so it "
                f"uploads the save - or add --force if that PC is definitely done.")
    LOCK.write_text(f"{me}|{time.time()}")
    if not commit_and_push(f"save: in use on {me}"):
        print("Warning: couldn't upload the 'in use' mark - the other PC won't see that this one is playing.")
    return None


def release():
    """After playing: upload the save and free the lock."""
    if not SYNC or not SAVE.exists():
        return True
    ensure_identity()
    if LOCK.exists() and lock_holder()[0] == socket.gethostname():
        LOCK.unlink()
    return commit_and_push(f"save: {socket.gethostname()} {time.strftime('%Y-%m-%d %H:%M')}")


def wait_for_exit(pid):
    """Block until process `pid` exits. Returns its exit code (None if it couldn't be read)."""
    import ctypes
    SYNCHRONIZE, QUERY = 0x00100000, 0x1000          # + PROCESS_QUERY_LIMITED_INFORMATION
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(SYNCHRONIZE | QUERY, False, int(pid))
    if not h:
        return None
    try:
        k32.WaitForSingleObject(h, 0xFFFFFFFF)
        code = ctypes.c_ulong()
        return code.value if k32.GetExitCodeProcess(h, ctypes.byref(code)) else None
    finally:
        k32.CloseHandle(h)


def watch(pid, settle_s=30):
    """Wait for the game client to exit, give the server a moment to write the save, then release."""
    wait_for_exit(pid)
    time.sleep(settle_s)
    ok = release()
    print("save uploaded" if ok else "save upload FAILED - run: lumberjack sync")


def start_watcher(pid):
    """Detached background process that uploads the save when the client exits."""
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    subprocess.Popen([str(pyw if pyw.exists() else py), "-m", "lumberjack.savesync", "watch", str(pid)],
                     cwd=REPO, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        host, age = lock_holder()
        print(f"in use on {host} ({age:.1f} h)" if host else "not in use")
    elif cmd == "pull":
        print("ok" if pull() else "failed")
    elif cmd in ("push", "release"):
        print("ok" if release() else "failed")
    elif cmd == "watch":
        watch(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    os.chdir(REPO)
    main()
