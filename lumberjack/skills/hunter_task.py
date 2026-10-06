"""Hunter: trap birds and chinchompas, nonstop (game data + admin teleport + ::item).

    teleport to the creatures for the level (Feldip Hills, Rellekka, Piscatoris)
    a trap caught something -> Check it.  A trap collapsed -> Dismantle it (the trap comes back)
    fewer traps out than the level allows (1, +1 at 20/40/60/80) -> Lay one where we stand
    (the game steps us off it; a tile that won't take one -> step aside)
    backpack filling up -> drop the bones and meat (feathers and chinchompas stack and stay)

Traps, creatures and where they live come from the 2009scape server (Traps.java, npc_spawns).
"""
import random
import time

from lumberjack import actions, items
from lumberjack.skills.base import BotBase, StopBot

# trap item -> its objects: laid (waiting) and collapsed; a catch shows a "Check" option
TRAPS = {
    "bird_snare": {"laid": {19175}, "failed": {19174}, "names": ("Bird snare",)},
    "box_trap": {"laid": {19187}, "failed": {19192}, "names": ("Box trap", "Shaking box")},
}
# (level, creature, where, trap) - the densest spawn cluster of each
ROUTE = [
    (1, "crimson swift", (2608, 2927, 0), "bird_snare"),       # Feldip Hills, 34 xp
    (11, "cerulean twitch", (2731, 3765, 0), "bird_snare"),    # Rellekka hunter area, 64.7 xp
    (19, "tropical wagtail", (2514, 2915, 0), "bird_snare"),   # Feldip Hills, 95.2 xp
    (53, "chinchompa", (2326, 3602, 0), "box_trap"),           # Piscatoris, 198 xp
    (63, "red chinchompa", (2557, 2915, 0), "box_trap"),       # Feldip Hills, 265 xp
]
JUNK = {526, 9978}                 # bones, raw bird meat
TRAP_RADIUS = 8
HOME_RADIUS = 6                    # wandered further than this -> walk back
FREE_SLOTS = 4
FOREIGN_S = 600                    # a trap that isn't ours is left alone this long


def max_traps(level):
    return 5 if level >= 80 else 4 if level >= 60 else 3 if level >= 40 else 2 if level >= 20 else 1


def best_spot(level):
    return max((r for r in ROUTE if r[0] <= level), key=lambda r: r[0])


class Hunter(BotBase):
    name = "hunter"

    def __init__(self, **kw):
        kw.pop("keep_carried", None)
        super().__init__(keep_carried=False, **kw)
        self.caught = self.laid = self.collapsed = 0
        self.spot = None
        self.came_from = None
        self.home = None
        self.lay_fails = 0
        self.tries_on = {}                  # trap tile -> Check/Dismantle clicks that changed nothing
        self.foreign = {}                   # trap tile -> until when it's left alone (someone else's)

    def progress(self):
        return self.caught

    def stats(self):
        return f"{self.caught} caught, {self.laid} traps laid, {self.collapsed} collapsed"

    def run(self):
        self.log.info("Hunter started - press F12 to stop")
        try:
            self.prepare()
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs is None:
                raise StopBot("Hunter needs the game's own data (it finds its traps in the scene)")
            me = self.gs.player()
            self.came_from = (me["tile"][0], me["tile"][1], me.get("plane", 0))
            while True:
                self.check_stop()
                self.go_to_spot()
                self.tick()
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            try:
                self.leave()
            except Exception as e:                  # the game went away: nothing to tidy
                self.log.debug("leave: %s", e)
            self.state = "stopped"
            self.inp.close()

    def level(self):
        from lumberjack.core import gamestate
        return (gamestate.skill("hunter") or {}).get("base", 1)

    def go_to_spot(self):
        """Teleport to the creatures for the level (again when the level moves us up a spot)."""
        from lumberjack.nav import places
        spot = best_spot(self.level())
        if spot == self.spot:
            return
        if self.spot is not None:
            self.pick_up_traps()                     # leave nothing behind
        lv, creature, (x, y, plane), trap = spot
        self.state = f"teleporting to the {creature}s"
        self.log.info("Hunting %ss (level %d+) with a %s", creature, lv, trap.replace("_", " "))
        if not places.teleport(self.ctx, self.gs, (x, y), plane):
            raise StopBot(f"couldn't teleport to the {creature}s (::tele {x} {y} {plane})")
        self.spot, self.home = spot, (x, y)

    # ---- one pass ------------------------------------------------------------------------
    def traps(self):
        """Our traps around us: (caught, collapsed, laid) location lists."""
        t = TRAPS[self.spot[3]]
        caught, failed, laid = [], [], []
        now = time.monotonic()
        for l in self.gs.locs(TRAP_RADIUS):
            if self.foreign.get(tuple(l["tile"]), 0) > now:
                continue                       # another hunter's trap: not ours to check or count
            ops = l.get("ops") or []
            if l["name"] in t["names"] and "Check" in ops:
                caught.append(l)
            elif l.get("id") in t["failed"]:
                failed.append(l)
            elif l.get("id") in t["laid"] or l["name"] in t["names"]:   # (a catch on its way in too)
                laid.append(l)
        return caught, failed, laid

    def tick(self):
        from lumberjack.core import interact
        caught, failed, laid = self.traps()
        if caught:
            self.state = "checking a trap"
            if self.use(caught[0], "Check"):
                self.caught += 1
            return
        if failed:
            self.state = "picking up a collapsed trap"
            if self.use(failed[0], "Dismantle"):
                self.collapsed += 1
            return
        self.make_room()
        me = self.gs.player()["tile"]
        if self.home and max(abs(me[0] - self.home[0]), abs(me[1] - self.home[1])) > HOME_RADIUS:
            self.state = "walking back"
            interact.walk_to_tile(self.ctx, self.gs, list(self.home), arrive=2)
            return
        if len(laid) < max_traps(self.level()):
            self.lay(len(laid))
            return
        self.state = f"waiting ({len(laid)} trap{'s' if len(laid) != 1 else ''} out)"
        self.sleep(random.uniform(1.2, 2.0))

    def use(self, loc, op):
        from lumberjack.core import interact
        if not interact.use_option(self.ctx, self.gs, interact.points_for(loc), op, loc["name"]):
            interact.walk_to_tile(self.ctx, self.gs, loc["tile"], arrive=1)   # off screen: get closer
            return False
        self.sleep(random.uniform(2.4, 3.0))         # walk over + the animation
        tile = tuple(loc["tile"])
        if any(tuple(l["tile"]) == tile and l.get("id") == loc.get("id") for l in self.gs.locs(TRAP_RADIUS)):
            # still there as it was: "This isn't your trap" - twice and it's left alone
            self.tries_on[tile] = self.tries_on.get(tile, 0) + 1
            if self.tries_on[tile] >= 2:
                self.log.info("The trap at %s isn't ours (another hunter's) - leaving it", list(tile))
                self.foreign[tile] = time.monotonic() + FOREIGN_S
                self.tries_on.pop(tile, None)
            return False
        self.tries_on.pop(tile, None)
        return True

    def lay(self, out):
        """Lay one more trap where we stand."""
        from lumberjack.core import backpack
        trap = self.spot[3]
        iid = items.BY_KEY[trap][1]
        inv = backpack.slots() or []
        slots = [i for i, s in enumerate(inv) if s["id"] == iid]
        if not slots:
            want = max_traps(self.level()) - out
            self.state = f"spawning {trap.replace('_', ' ')}s"
            if not items.fill(self.ctx, trap, want):
                raise StopBot(f"couldn't spawn a {trap.replace('_', ' ')} (backpack full?)")
            return
        before = self.gs.player()["tile"]
        self.state = f"laying a {trap.replace('_', ' ')}"
        if not actions.use_slot(self.ctx, self.gs, slots[0], "Lay"):
            return
        self.sleep(random.uniform(2.6, 3.2))
        if self.trap_went_down(iid, len(slots), out, before):
            self.laid += 1
            self.lay_fails = 0
            return
        self.lay_fails += 1                         # "You can't lay a trap here": step aside
        if self.lay_fails >= 6:
            raise StopBot("no tile here takes a trap")
        self.step_aside(before)

    def trap_went_down(self, iid, carried, out, before):
        """Did the trap get laid? One fewer in the backpack, one more of ours on the ground, or
        the game stepping us off it (it doesn't always: the step needs a free tile beside it)."""
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        if inv and sum(1 for s in inv if s["id"] == iid) < carried:
            return True
        if sum(len(t) for t in self.traps()) > out:
            return True
        return self.gs.player()["tile"] != before

    def step_aside(self, tile):
        from lumberjack.core import interact
        dx, dy = random.choice([(2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (-2, -2)])
        interact.walk_to_tile(self.ctx, self.gs, [tile[0] + dx, tile[1] + dy], arrive=0, max_clicks=3)

    def make_room(self):
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        if sum(1 for s in inv if s["id"] < 0) >= FREE_SLOTS:
            return
        junk = [i for i, s in enumerate(inv) if s["id"] in JUNK]
        if junk:
            self.state = "dropping bones and meat"
            actions.drop_known(self.ctx, self.gs, junk)

    # ---- leaving -------------------------------------------------------------------------
    def pick_up_traps(self):
        """Take every trap of ours back (a catch is checked, the rest dismantled)."""
        for _ in range(12):
            caught, failed, laid = self.traps()
            if not (caught or failed or laid):
                return
            loc = (caught or failed or laid)[0]
            self.use(loc, "Check" if caught else "Dismantle")

    def leave(self):
        """Stay put: a run also ends before every retry, and teleporting back to where it started
        sent us away from the spot each time. The next step travels by itself (Autopilot surfaces
        at Lumbridge first when it needs open ground)."""
        return
