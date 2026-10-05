"""Slayer: melee on the training route's monsters with a matching Slayer task, nonstop.

The admin command ::setslayertask <npc id> 255 gives the task for the monster we're about to
fight (re-given every RENEW_KILLS kills, before 255 run out), so every kill also gives Slayer
XP (= the monster's hitpoints). Fighting, eating, gear: as Combat.

Every route monster is a task in the 2009scape server's Tasks.java (npc ids from there).
"""
from lumberjack.skills.combat import Fighter

TASK_NPC = {"chicken": 41, "cow": 81, "goblin": 100, "hill_giant": 117, "ice_warrior": 125,
            "ankou": 4381, "fire_giant": 110}
TASK_AMOUNT = 255                 # the most the command allows
RENEW_KILLS = 200


def task_npc(targets):
    return next((TASK_NPC[t] for t in targets if t in TASK_NPC), None)


class SlayerFighter(Fighter):
    name = "slayer"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.assigned_at = None          # kills when the task was last given

    def check_levels(self, force_style=False):
        if self.gs is not None and (self.assigned_at is None or self.kills - self.assigned_at >= RENEW_KILLS):
            self.assign()
        return super().check_levels(force_style=force_style)

    def assign(self):
        from lumberjack.skills.base import StopBot
        npc = task_npc(self.targets)
        if npc is None:
            raise StopBot(f"none of {'/'.join(self.targets)} is a Slayer task")
        self.state = "setting the Slayer task"
        self.log.info("Slayer task: %s x%d (::setslayertask %d %d)", self.targets[0].replace("_", " "),
                      TASK_AMOUNT, npc, TASK_AMOUNT)
        self.inp.move(260, 300)
        self.inp.type_text(f"::setslayertask {npc} {TASK_AMOUNT}", enter=True)
        self.sleep(1.2)
        self.assigned_at = self.kills
