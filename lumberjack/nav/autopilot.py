"""Autopilot: train a whole account by itself, from a brand-new Lumbridge character up.

Each round picks one task and how far to take it (next_step), then the plan runner runs it like
a plan step with place "auto" (the training route: right spot + settings for the level).

    1. combat basics: Attack/Strength/Defence to COMBAT_BASE first - enough HP to pickpocket,
       fight Al Kharid warriors and survive random events
    2. Prayer to PRAYER_EARLY: dragon bones make it quick, and it's handy for everything after
    3. then always the lowest skill (ties: the faster-training task first), up to its next
       milestone - the next training-route tier (oaks at 15, willows at 30...) or the next
       multiple of 10 - but at most STEP_MINUTES at a time, so everything moves along

A task that ends badly twice in a row is rested for COOLDOWN_S, so one broken skill can't
stall the run.
"""
import time

# task -> the skill(s) its level is (combat: the lowest melee stat)
TASK_SKILL = {"woodcutting": ["woodcutting"], "fishing": ["fishing"], "mining": ["mining"],
              "combat": ["attack", "strength", "defence"], "ranged": ["ranged"], "magic": ["magic"],
              "thieving": ["thieving"], "firemaking": ["firemaking"], "cooking": ["cooking"],
              "prayer": ["prayer"], "fletching": ["fletching"], "crafting": ["crafting"], "herblore": ["herblore"],
              "runecrafting": ["runecrafting"], "agility": ["agility"], "hunter": ["hunter"], "smithing": ["smithing"]}
# faster XP first when levels tie (spawned supplies beat gathering)
SPEED = ["prayer", "herblore", "runecrafting", "magic", "fletching", "crafting", "cooking", "firemaking", "smithing",
         "thieving", "hunter", "ranged", "combat", "fishing", "woodcutting", "mining", "agility"]
COMBAT_BASE = 10
PRAYER_EARLY = 43
STEP_MINUTES = 30
FAILS_BEFORE_REST = 2
COOLDOWN_S = 3600


def task_level(task, levels):
    have = [levels.get(s) for s in TASK_SKILL.get(task, [task]) if levels.get(s) is not None]
    return min(have) if have else 1


def milestone(task, level, target):
    """The next level worth stopping at: a route tier or the next multiple of 10 (<= target)."""
    from lumberjack.nav.training import ROUTES
    tiers = [lv for lv, _, _ in ROUTES.get(task, []) if lv > level]
    tens = (level // 10 + 1) * 10
    return min([t for t in tiers + [tens, target] if t > level])


class Autopilot:
    def __init__(self, target=99, skip=()):
        self.target = target
        self.tasks = [t for t in TASK_SKILL if t not in set(skip)]
        self.fails = {}
        self.rest_until = {}

    def done(self, task, ok, now=None):
        """Record how a step went."""
        now = time.monotonic() if now is None else now
        if ok:
            self.fails[task] = 0
            return
        self.fails[task] = self.fails.get(task, 0) + 1
        if self.fails[task] >= FAILS_BEFORE_REST:
            self.rest_until[task] = now + COOLDOWN_S
            self.fails[task] = 0

    def next_step(self, levels, now=None):
        """(task, until level, minutes, why) - or None when every skill is at the target (or
        resting)."""
        now = time.monotonic() if now is None else now
        ready = [t for t in self.tasks if self.rest_until.get(t, 0) <= now
                 and task_level(t, levels) < self.target]
        if not ready:
            return None
        lv = {t: task_level(t, levels) for t in ready}
        if "combat" in ready and lv["combat"] < COMBAT_BASE:
            return "combat", COMBAT_BASE, STEP_MINUTES, f"combat basics first (to {COMBAT_BASE})"
        if "prayer" in ready and lv["prayer"] < PRAYER_EARLY:
            return "prayer", PRAYER_EARLY, STEP_MINUTES, f"quick Prayer to {PRAYER_EARLY}"
        task = min(ready, key=lambda t: (lv[t], SPEED.index(t) if t in SPEED else 99))
        until = milestone(task, lv[task], self.target)
        return task, until, STEP_MINUTES, f"lowest skill ({lv[task]}) - to {until}"
