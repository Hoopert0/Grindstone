"""XP tracking for the panel: XP gained, XP/hour and time to the next level per skill.

Fed by Stats-tab tooltip reads (ui.stats.read_skill) - the bots call update() at start and
after every load, so numbers refresh every few minutes.
"""
import time


class XpTracker:
    def __init__(self):
        self.skills = {}   # skill -> {"start", "t0", "xp", "t", "level", "remainder"}

    def update(self, skill, info):
        if not info or info.get("xp") is None:
            return
        now = time.monotonic()
        s = self.skills.setdefault(skill, {"start": info["xp"], "t0": now, "start_level": info["level"]})
        s.update(xp=info["xp"], t=now, level=info["level"], remainder=info.get("remainder"))

    def summary(self):
        out = {}
        for skill, s in self.skills.items():
            gained = s["xp"] - s["start"]
            hours = (s["t"] - s["t0"]) / 3600
            per_hour = gained / hours if hours > 0.02 and gained > 0 else 0
            rem = s.get("remainder")
            out[skill] = {
                "level": s["level"],
                "levels_gained": s["level"] - s["start_level"],
                "xp": s["xp"],
                "gained": gained,
                "per_hour": round(per_hour),
                "remainder": rem,
                "eta_s": round(rem / per_hour * 3600) if per_hour and rem else None,
            }
        return out
