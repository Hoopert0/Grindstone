"""Thieving: pickpocket NPCs (game data), nonstop.

    nearest NPC with "Pickpocket" whose name is wanted -> pickpocket it
    XP went up -> success.  HP went down -> caught and stunned: wait it out
    HP below eat_below -> eat (food spawned when there's none: 'Spawn missing tools')
    backpack full -> drop the loot (coins stack and stay)

Which NPC fits which level, and where they are, comes from the training route
(nav.training: men -> Al Kharid warriors -> guards -> knights).
"""
import random
import time

from lumberjack import actions, items
from lumberjack.skills.base import BotBase, StopBot

ATTEMPT_S = 1.9            # a pickpocket takes ~3 ticks
STUN_S = 5.0               # caught: stunned ~4 s
NO_TARGET_S = 90           # nothing to pickpocket this long -> stop (a plan travels back)
FOOD = "lobster"
FOOD_SPAWN = 10
LEVELS = {"man": 1, "woman": 1, "farmer": 10, "al-kharid_warrior": 25, "al_kharid_warrior": 25,
          "rogue": 32, "guard": 40, "knight_of_ardougne": 55, "paladin": 70, "hero": 80}


class Thief(BotBase):
    name = "thieving"

    def __init__(self, targets=("man", "woman"), eat_below=50, spawn_tools=True, **kw):
        kw.pop("keep_carried", None)
        super().__init__(keep_carried=False, **kw)
        self.targets = [t.lower() for t in targets]
        self.eat_below = eat_below / 100 if eat_below > 1 else eat_below
        self.spawn_tools = spawn_tools
        self.stolen = self.caught = self.eaten = 0
        self.last_target_at = time.monotonic()

    def progress(self):
        return self.stolen

    def stats(self):
        return f"{self.stolen} pickpockets, caught {self.caught}x, ate {self.eaten}"

    def run(self):
        self.log.info("Thieving started (%s) - press F12 to stop", "/".join(self.targets))
        try:
            self.prepare()
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs is None:
                raise StopBot("Thieving needs the game's own data (NPC list)")
            while True:
                self.check_stop()
                self.ensure_hp()
                self.make_room()
                n = self.pick_target()
                if n is None:
                    if time.monotonic() - self.last_target_at > NO_TARGET_S:
                        raise StopBot(f"nothing to pickpocket here ({'/'.join(self.targets)})")
                    self.state = "looking for someone to pickpocket"
                    self.sleep(2.0)
                    continue
                self.last_target_at = time.monotonic()
                self.attempt(n)
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            self.inp.close()

    # ---- steps -----------------------------------------------------------------------
    def _hp_xp(self):
        s = self.gs.skills()
        return s["hitpoints"]["boosted"], s["hitpoints"]["level"], s["thieving"]["xp"]

    def pick_target(self):
        from lumberjack.core import interact
        from lumberjack.core.backpack import key
        cands = [n for n in self.gs.npcs() if key(n["name"]) in self.targets and "Pickpocket" in n["ops"]]
        cands.sort(key=lambda n: n["dist"])
        for n in cands[:4]:
            if interact.on_screen(*n["screen"]):
                return n
        if cands:                                  # off screen: walk up to the nearest
            self.state = f"walking to a {cands[0]['name']}"
            interact.walk_to_tile(self.ctx, self.gs, cands[0]["tile"], arrive=2, max_clicks=4)
            return None
        return None

    def attempt(self, n):
        from lumberjack.core import interact
        hp0, _, xp0 = self._hp_xp()
        self.state = f"pickpocketing a {n['name']}"
        if interact.use_option(self.ctx, self.gs, interact.points_for(n), "Pickpocket", n["name"]) is None:
            self.sleep(0.5)
            return
        self.sleep(ATTEMPT_S + random.uniform(0, 0.3))
        actions.dismiss_dialog(self.ctx)
        hp1, _, xp1 = self._hp_xp()
        if xp1 > xp0:
            self.stolen += 1
        elif hp1 < hp0:
            self.caught += 1
            self.state = "stunned"
            self.sleep(STUN_S)

    def ensure_hp(self):
        hp, base, _ = self._hp_xp()
        if base and hp / base >= self.eat_below:
            return
        from lumberjack.core import backpack
        for _ in range(2):
            inv = backpack.slots() or []
            food = [i for i, s in enumerate(inv) if backpack.kind(s["key"]) == "cooked"]
            if food:
                self.state = "eating"
                if actions.use_slot(self.ctx, self.gs, food[0], "Eat"):
                    self.eaten += 1
                    self.sleep(1.8)
                    self.inp.move(260, 300)
                    return
            if not self.spawn_tools:
                break
            self.make_room(need=FOOD_SPAWN)
            items.fill(self.ctx, FOOD, FOOD_SPAWN)
        raise StopBot(f"HP low ({hp}/{base}) and no food")

    def make_room(self, need=1):
        """Keep `need` slots free: drop the loot (coins, food and tools stay)."""
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        free = sum(1 for s in inv if s["id"] < 0)
        if free >= need:
            return
        junk = [i for i, s in enumerate(inv) if s["id"] >= 0 and s["key"] != "coins"
                and backpack.kind(s["key"]) not in ("cooked", "tool")]
        if junk:
            self.state = "dropping loot"
            actions.drop_known(self.ctx, self.gs, junk)
