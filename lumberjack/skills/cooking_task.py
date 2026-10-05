"""Cooking task: spawned raw fish cooked on our own fire, nonstop (game data + 'Spawn missing
tools', singleplayer admin ::item). No fishing, bank or range needed.

    tinderbox? (spawned)  ->  logs for fires + the best raw fish the Cooking level allows
    -> light a fire -> "Cook All" (a new fire whenever the last one went out)
    -> drop the cooked and burnt fish -> walk back to where we started -> repeat

Reuses skills.cooking (use-on-fire from the scene data, the cook box) and
skills.firemaking (tinderbox on a log) - the same steps Fishing + Cook does after a catch.
"""
from lumberjack import actions, items
from lumberjack.skills import cooking, firemaking
from lumberjack.skills.base import BotBase, StopBot

# spawnable raw fish -> Cooking level (items.CATALOG has their ids)
RAW_LEVELS = {"raw_shrimps": 1, "raw_sardine": 1, "raw_herring": 5, "raw_trout": 15, "raw_pike": 20,
              "raw_salmon": 25, "raw_tuna": 30, "raw_lobster": 40, "raw_swordfish": 45}
FIRE_LOGS = 3              # logs carried for fires each load
MAX_FIRE_TRIES = 5         # failed lights in a row before giving up
MAX_STALLS = 4             # cook rounds in a row that cooked nothing -> drop the rest, start over


def best_raw(level, blocked=()):
    ok = [k for k, need in RAW_LEVELS.items() if need <= level and k not in blocked]
    return max(ok, key=lambda k: (RAW_LEVELS[k], k)) if ok else "raw_shrimps"


class Cooker(BotBase):
    name = "cooking"

    def __init__(self, spawn_tools=True, **kw):
        kw.pop("keep_carried", None)
        super().__init__(keep_carried=False, **kw)    # the tools are known by name
        self.spawn_tools = spawn_tools
        self.cooked = self.burnt = 0
        self.home = None
        self.fire_fails = 0
        self.current = None

    def progress(self):
        return self.cooked + self.burnt

    def stats(self):
        return f"{self.cooked} cooked, {self.burnt} burnt, {self.burned} fires"

    def run(self):
        self.log.info("Cooking started - press F12 to stop")
        try:
            self.prepare()
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs is None or not self.spawn_tools:
                raise StopBot("Cooking needs the game's own data and 'Spawn missing tools' (it spawns the fish)")
            self.home = self.gs.player()["tile"]
            stalls = 0
            while True:
                self.check_stop()
                tinder = self.tinderbox()
                inv = cooking.sort_backpack(self.ctx, keep={tinder})
                if not inv["raw"]:
                    self.restock(inv, tinder)
                    stalls = 0
                    continue
                self.state = f"cooking {(self.current or 'fish').replace('raw_', '')}"
                status, _ = cooking.cook(self.ctx, inv["raw"])
                if status == "no_fire":
                    self.light_fire(tinder, inv["log"])
                    continue
                left = cooking.still_raw(self.ctx, inv["raw"])
                done = len(inv["raw"]) - len(left)
                stalls = 0 if done else stalls + 1
                if stalls >= MAX_STALLS:
                    self.log.warning("Nothing cooked for a while - dropping these and starting over")
                    actions.drop_all(self.ctx, keep={tinder}, food=True)
                    stalls = 0
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            self.inp.close()

    # ---- steps -----------------------------------------------------------------------
    def tinderbox(self):
        from lumberjack.core import backpack
        items.ensure(self.ctx, lambda k: k == "tinderbox", "tinderbox", log=self.log)
        inv = backpack.slots() or []
        slot = next((i for i, s in enumerate(inv) if s["key"] == "tinderbox"), None)
        if slot is None:
            raise StopBot("no tinderbox and couldn't spawn one")
        return slot

    def restock(self, inv, tinder):
        """Count and drop the last load, walk back, spawn logs + a load of raw fish."""
        from lumberjack.core import backpack, gamestate
        if inv["cooked"] or inv["burnt"]:
            self.cooked += len(inv["cooked"])
            self.burnt += len(inv["burnt"])
            self.log.info("Load done: %s", self.stats())
            self.state = "dropping the cooked fish"
            keep = {i for i, s in enumerate(backpack.slots() or []) if s["id"] >= 0} - set(inv["cooked"]) - set(inv["burnt"])
            actions.drop_all(self.ctx, keep=keep, food=True)
        self.walk_home()
        logs = len(inv["log"])
        if logs < FIRE_LOGS:
            self.spawn_fill("logs", FIRE_LOGS - logs)
        lv = (gamestate.skill("cooking") or {}).get("base", 1)
        self.current = best_raw(lv)
        if not self.spawn_fill(self.current, 28):
            raise StopBot("couldn't spawn raw fish (backpack full of other things?)")

    def spawn_fill(self, key, want):
        """Spawn up to `want` of `key` into the free slots (a few commands if the game gives
        fewer per command). True if any arrived."""
        from lumberjack.core import backpack
        used = lambda: sum(1 for s in backpack.slots() or [] if s["id"] >= 0)
        start = used()
        self.state = f"spawning {key.replace('_', ' ')}"
        for _ in range(28):
            have = used()
            n = min(want - (have - start), 28 - have)
            if n <= 0:
                break
            items.spawn(self.ctx, key, n)
            self.ctx.sleep(0.4)
            if used() <= have:
                break
        return used() > start

    def light_fire(self, tinder, logs):
        if not logs:
            self.spawn_fill("logs", FIRE_LOGS)
            return
        self.state = "lighting a fire"
        actions.dismiss_dialog(self.ctx)
        if firemaking.light_one(self.ctx, tinder, logs[0]):
            self.burned += 1
            self.fire_fails = 0
            return
        if actions.dismiss_dialog(self.ctx):
            return
        self.fire_fails += 1
        if self.fire_fails >= MAX_FIRE_TRIES:
            raise StopBot("can't light a fire here - stand somewhere open (not indoors or on a path)")
        self.log.info("Couldn't light a fire here - moving a few tiles")
        firemaking._step_aside(self.ctx)

    def walk_home(self):
        from lumberjack.core import interact
        me = self.gs.player()["tile"]
        if self.home and max(abs(me[0] - self.home[0]), abs(me[1] - self.home[1])) > 4:
            self.state = "walking back"
            interact.walk_to_tile(self.ctx, self.gs, self.home, arrive=1)
