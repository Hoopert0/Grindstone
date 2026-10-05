"""Get the newest Grindstone from GitHub - safely, so it can run by itself.

    update() -> {'ok', 'updated', 'message', 'from', 'to', 'addon_changed'}

Used when Grindstone starts (lumberjack.start) and by the panel's Update button.

* With a shared save (this repo holds `save/`): the save sync's pull (it commits what this PC
  learned first, then rebases on GitHub).
* Otherwise (a plain copy, e.g. a friend's): fetch, then fast-forward only. What this PC learned
  in game - screen templates and maps (LOCAL_DATA) - is set aside, the update applied, and put
  back, so the PC's own copies always win. Any other local change (edited code) makes it skip
  the update and say so, instead of overwriting someone's work.

Nothing here needs a GitHub account: a public repo pulls without signing in.
"""
import shutil
import tempfile
from pathlib import Path

from lumberjack import procs

REPO = Path(__file__).resolve().parents[1]
LOCAL_DATA = ("lumberjack/assets/templates/", "lumberjack/assets/maps/",
              "lumberjack/assets/places.json", "lumberjack/assets/fishing_spots.json",
              "save/")                       # (the retired shared save: leftovers never block an update)


def _git(*args, timeout=60):
    return procs.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=timeout)


def _out(*args):
    r = _git(*args)
    return r.stdout.strip() if r.returncode == 0 else None


def branch():
    return _out("rev-parse", "--abbrev-ref", "HEAD") or "main"


def _local(path):
    return any(path == p.rstrip("/") or path.startswith(p) for p in LOCAL_DATA)


def _changed_paths():
    """[(status, path)] from git status (renames: the new path)."""
    r = _git("status", "--porcelain", "--untracked-files=all")
    out = r.stdout if r.returncode == 0 else ""          # (not stripped: the first column is a space)
    rows = []
    for line in out.splitlines():
        status, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append((status, path.strip('"')))
    return rows


def update():
    from lumberjack import savesync, version
    res = {"ok": False, "updated": False, "message": "", "from": version.code_version()[0], "to": None,
           "addon_changed": False}
    old = _out("rev-parse", "HEAD")
    if _out("remote") in (None, ""):
        res.update(ok=True, message="not a copy from GitHub - nothing to update")
        return res
    if savesync.SYNC and savesync.SAVE.exists():       # shared-save setup: the save sync pulls
        savesync.ensure_identity()
        if not savesync.pull():
            res["message"] = "couldn't download the update (git pull failed - see the log)"
            return res
    else:
        br = branch()
        if _git("fetch", "-q", "origin", br).returncode:
            res["message"] = "couldn't reach GitHub (offline?)"
            return res
        if _out("rev-list", "--count", f"HEAD..origin/{br}") in ("0", None):
            res.update(ok=True, message="already up to date")
            return res
        changed = _changed_paths()
        mine = [p for _, p in changed if not _local(p)]
        if mine:
            res["message"] = ("you've changed files the update would touch (" + ", ".join(mine[:4])
                              + (" ..." if len(mine) > 4 else "") + ") - skipped it")
            return res
        err = _fast_forward(br, [p for _, p in changed])
        if err:
            res["message"] = err
            return res
    new = _out("rev-parse", "HEAD")
    res["to"] = version.code_version()[0]
    res["updated"] = bool(old and new and old != new)
    if res["updated"] and _git("diff", "--quiet", old, new, "--", "lumberjack/agent").returncode:
        res["addon_changed"] = True
    res.update(ok=True, message=(f"updated to v{res['to']}" if res["updated"] else "already up to date"))
    return res


RELEASE_PREFIX = "Grindstone v"        # the public copy's commits (tools/make_public.py)


def _only_releases_here(br):
    """Every commit here that GitHub's branch doesn't have is a published release (nothing made on
    this PC), so following a replaced history loses nothing."""
    out = _out("log", "--format=%s", f"origin/{br}..HEAD")
    if out is None:
        return False
    subjects = [s for s in out.splitlines() if s.strip()]
    return all(s.startswith(RELEASE_PREFIX) for s in subjects)


def _fast_forward(br, learned):
    """Set the learned files aside, fast-forward, put them back. Error message or None."""
    keep = Path(tempfile.mkdtemp(prefix="lumberjack-learned-"))
    try:
        for p in learned:
            src = REPO / p
            if src.is_file():
                dst = keep / p
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        tracked = [p for p in learned if _git("ls-files", "--error-unmatch", p).returncode == 0]
        if tracked:
            _git("checkout", "--", *tracked)          # back to the committed copies for the merge
        for p in learned:                             # untracked ones could collide with new files
            if p not in tracked and (REPO / p).is_file():
                (REPO / p).unlink()
        r = _git("merge", "--ff-only", "-q", f"origin/{br}")
        err = None if r.returncode == 0 else "couldn't apply the update: " + (r.stderr.strip() or "git merge failed")
        if err and _only_releases_here(br):
            # the published history was replaced (e.g. tidied up): this copy holds nothing of its own,
            # so move to the new history
            r = _git("reset", "-q", "--keep", f"origin/{br}")
            err = None if r.returncode == 0 else "couldn't apply the update: " + (r.stderr.strip() or "git reset failed")
    finally:
        for f in keep.rglob("*"):                      # this PC's learned copies win, always
            if f.is_file():
                dst = REPO / f.relative_to(keep)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
        shutil.rmtree(keep, ignore_errors=True)
    return err
