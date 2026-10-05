"""What each run achieved - one JSON line per bot run in logs/runs.jsonl (per PC, not synced):

    {"task", "place", "start" (unix time), "minutes", "xp": {skill: gained}, "levels": {skill: [from, to]},
     "reason"}

Written by the control panel around every run (plans, single tasks, retries); shown in the Plan
tab so you can see what a night of running did. Needs the game's own data for XP.
"""
import json
import time
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "logs" / "runs.jsonl"
KEEP_LINES = 5000


def snapshot():
    """{skill: [level, xp]} from the game, or None."""
    from lumberjack.core import gamestate
    gs = gamestate.shared()
    if gs is None:
        return None
    try:
        return {k: [v["level"], v["xp"]] for k, v in gs.skills().items()}
    except Exception:
        return None


def gains(before, after):
    """(xp gained per skill, [from, to] per skill that levelled) - only skills that changed."""
    if not before or not after:
        return {}, {}
    xp, lv = {}, {}
    for k, (l1, x1) in after.items():
        l0, x0 = before.get(k, (l1, x1))
        if x1 > x0:
            xp[k] = x1 - x0
        if l1 > l0:
            lv[k] = [l0, l1]
    return xp, lv


def record(task, place, start, before, after, reason):
    xp, lv = gains(before, after)
    row = {"task": task, "place": place, "start": round(start), "minutes": round((time.time() - start) / 60, 1),
           "xp": xp, "levels": lv, "reason": reason}
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        with FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if FILE.stat().st_size > KEEP_LINES * 400:          # trim now and then
            lines = FILE.read_text(encoding="utf-8").splitlines()[-KEEP_LINES:]
            FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return row


def load(limit=None):
    try:
        rows = [json.loads(l) for l in FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []
    return rows[-limit:] if limit else rows


def summary(hours=24, now=None):
    """Totals over the last `hours`: {'minutes', 'xp': {skill: n}, 'levels': {skill: [from, to]},
    'runs', 'problems' (runs that didn't end well)}, plus the most recent runs."""
    now = now or time.time()
    rows = [r for r in load() if r.get("start", 0) >= now - hours * 3600]
    xp, lv = {}, {}
    for r in rows:
        for k, n in r.get("xp", {}).items():
            xp[k] = xp.get(k, 0) + n
        for k, (a, b) in r.get("levels", {}).items():
            lv[k] = [min(a, lv.get(k, [a, b])[0]), max(b, lv.get(k, [a, b])[1])]
    good = ("time limit reached", "reached", "stopped from control panel", "F12 pressed")
    return {"hours": hours, "runs": len(rows), "minutes": round(sum(r.get("minutes", 0) for r in rows)),
            "xp": dict(sorted(xp.items(), key=lambda kv: -kv[1])), "levels": lv,
            "problems": sum(1 for r in rows if not str(r.get("reason", "")).startswith(good)),
            "recent": rows[-15:][::-1]}
