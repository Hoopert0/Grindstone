"""Farming: the Falador farm's two allotments and herb patch, nonstop, with the admin ::grow
command (one growth stage per use) and spawned seeds and tools.

Each patch's state is its varbit (the add-on's `varbit` query; the server's Patch.kt):

    0-2 weeds      -> use a rake on it (4 xp per weed stage)
    3   empty      -> supercompost (26 xp, far fewer diseases), then the best seed for the level
    growing        -> ::grow until grown          diseased -> plant cure     dead -> dig it up (spade)
    grown          -> Pick (herbs) / Harvest (allotments): one click picks until the patch is empty

Patches are found on screen by the varbit their object follows (the add-on adds it to `locs`).
Seeds, levels and states come from the 2009scape server (Plantable.kt, Patch.kt).
"""
import random
import time

from lumberjack import actions, items
from lumberjack.skills.base import BotBase, StopBot

FARM = (3054, 3307, 0)                      # Falador farm, south of the city
ALLOTMENTS = (708, 709)                     # the server's FarmingPatch varbits
HERB = 780
PATCH_RADIUS = 15
GROW_EVERY_S = 2.0
FREE_SLOTS = 6

# (level, seed key, item id, varbit value at planting, growth stages)
ALLOTMENT_SEEDS = [(1, "potato_seed", 5318, 6, 4), (5, "onion_seed", 5319, 13, 4),
                   (7, "cabbage_seed", 5324, 20, 4), (12, "tomato_seed", 5322, 27, 4),
                   (20, "sweetcorn_seed", 5320, 34, 6), (31, "strawberry_seed", 5323, 43, 6),
                   (47, "watermelon_seed", 5321, 52, 8)]
HERB_SEEDS = [(9, "guam_seed", 5291, 4), (14, "marrentill_seed", 5292, 11), (19, "tarromin_seed", 5293, 18),
              (26, "harralander_seed", 5294, 25), (32, "ranarr_seed", 5295, 32), (38, "toadflax_seed", 5296, 46),
              (44, "irit_seed", 5297, 53), (50, "avantoe_seed", 5298, 39), (56, "kwuarm_seed", 5299, 68),
              (62, "snapdragon_seed", 5300, 75), (67, "cadantine_seed", 5301, 82), (73, "lantadyme_seed", 5302, 89),
              (79, "dwarf_weed_seed", 5303, 96), (85, "torstol_seed", 5304, 103)]
HERB_STAGES = 4
TOOLS = {"rake": 5341, "seed_dibber": 5343, "spade": 952}
SUPERCOMPOST, PLANT_CURE = 6034, 6036
KEEP = set(TOOLS.values()) | {SUPERCOMPOST, PLANT_CURE, 995} | {s[2] for s in ALLOTMENT_SEEDS + HERB_SEEDS}


def herb_state(v):
    """'weeds' | 'empty' | 'growing' | 'grown' | 'diseased' | 'dead' for a herb patch value."""
    if v <= 2:
        return "weeds"
    if v == 3:
        return "empty"
    if v >= 170:
        return "dead"
    if v >= 128:
        return "diseased"
    base = max((s[3] for s in HERB_SEEDS if s[3] <= v), default=None)
    if base is None:
        return "growing"
    return "grown" if v - base >= HERB_STAGES else "growing"


def allotment_state(v):
    if v & 0x40:
        return "dead"                       # (also "watered" - we never water)
    if v & 0x80:
        return "diseased"
    v &= 0x3F
    if v <= 2:
        return "weeds"
    if v == 3:
        return "empty"
    seed = max((s for s in ALLOTMENT_SEEDS if s[3] <= v), key=lambda s: s[3], default=None)
    if seed is None:
        return "growing"
    return "grown" if v - seed[3] >= seed[4] else "growing"


def best_seed(kind, level):
    table = HERB_SEEDS if kind == "herb" else ALLOTMENT_SEEDS
    ok = [s for s in table if s[0] <= level]
    return max(ok, key=lambda s: s[0]) if ok else None


class Farmer(BotBase):
    name = "farming"

    def __init__(self, **kw):
        kw.pop("keep_carried", None)
        super().__init__(keep_carried=False, **kw)
        self.harvests = self.planted = 0
        self.composted = set()               # patches given supercompost since they were last cleared
        self.came_from = None
        self.last_grow = 0.0

    def progress(self):
        return self.harvests

    def stats(self):
        return f"{self.planted} planted, {self.harvests} harvested"

    def run(self):
        self.log.info("Farming started - press F12 to stop")
        try:
            self.prepare()
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs is None:
                raise StopBot("Farming needs the game's own data (patch states)")
            me = self.gs.player()
            self.came_from = (me["tile"][0], me["tile"][1], me.get("plane", 0))
            self.go_to_farm()
            while True:
                self.check_stop()
                self.tick()
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            try:
                self.leave()
            except Exception as e:
                self.log.debug("leave: %s", e)
            self.state = "stopped"
            self.inp.close()

    def level(self):
        from lumberjack.core import gamestate
        return (gamestate.skill("farming") or {}).get("base", 1)

    def go_to_farm(self):
        from lumberjack.nav import places
        x, y, plane = FARM
        me = self.gs.player()
        if max(abs(me["tile"][0] - x), abs(me["tile"][1] - y)) <= 8:
            return
        self.state = "teleporting to the Falador farm"
        if not places.teleport(self.ctx, self.gs, (x, y), plane):
            raise StopBot(f"couldn't teleport to the Falador farm (::tele {x} {y} {plane})")
        actions.reset_camera(self.ctx)

    # ---- one pass --------------------------------------------------------------------------
    def patches(self):
        """{varbit: (state, loc)} for the patches we farm (the herb patch from level 9)."""
        wanted = list(ALLOTMENTS) + ([HERB] if self.level() >= 9 else [])
        locs = {l["varbit"]: l for l in self.gs.locs(PATCH_RADIUS) if l.get("varbit") in wanted}
        if not locs:
            raise StopBot("no farming patches here - an add-on from before patch support? Restart the game "
                          "with the Grindstone icon")
        values = self.gs.varbits(*locs)
        out = {}
        for vb, loc in locs.items():
            v = values.get(vb, 0)
            out[vb] = (herb_state(v) if vb == HERB else allotment_state(v), loc, v)
        return out

    def tick(self):
        self.make_room()
        patches = self.patches()
        order = ["grown", "diseased", "dead", "weeds", "empty"]
        todo = sorted(((order.index(st), vb, st, loc, v) for vb, (st, loc, v) in patches.items() if st in order),
                      key=lambda t: t[:2])
        if todo:
            _, vb, st, loc, v = todo[0]
            self.value = v                   # before we touch it: wait_changed compares with this
            getattr(self, f"on_{st}")(vb, loc)
            return
        if time.monotonic() - self.last_grow >= GROW_EVERY_S:      # everything is growing
            self.state = "growing (::grow)"
            self.inp.move(260, 300)
            self.inp.type_text("::grow", enter=True)
            self.last_grow = time.monotonic()
        self.sleep(0.8)

    def on_grown(self, vb, loc):
        op = "Pick" if vb == HERB else "Harvest"
        self.ensure(TOOLS["spade"], "spade")          # the server wants one to harvest
        self.state = f"{op.lower()}ing"
        if self.click(loc, op):
            self.harvests += 1
            self.wait_changed(vb, 30)
            self.composted.discard(vb)

    def on_diseased(self, vb, loc):
        self.state = "curing a diseased patch"
        if self.use_on(PLANT_CURE, "plant_cure", loc):
            self.wait_changed(vb, 8)

    def on_dead(self, vb, loc):
        self.state = "clearing dead plants"
        if self.use_on(TOOLS["spade"], "spade", loc):
            self.sleep(1.0)
            self.continue_dialog_yes()
            self.wait_changed(vb, 10)
            self.composted.discard(vb)

    def on_weeds(self, vb, loc):
        self.state = "raking weeds"
        if self.use_on(TOOLS["rake"], "rake", loc):
            self.wait_changed(vb, 25, until=lambda v: v >= 3)

    def on_empty(self, vb, loc):
        if vb not in self.composted:
            self.state = "adding supercompost"
            if self.use_on(SUPERCOMPOST, "supercompost", loc):
                self.composted.add(vb)
                self.sleep(2.0)
            return
        seed = best_seed("herb" if vb == HERB else "allotment", self.level())
        if seed is None:
            return
        self.ensure(TOOLS["seed_dibber"], "seed_dibber")
        self.state = f"planting {seed[1].replace('_', ' ')}s"
        if self.use_on(seed[2], seed[1], loc, amount=10):
            if self.wait_changed(vb, 8):
                self.planted += 1

    # ---- helpers ----------------------------------------------------------------------------
    def slot_of(self, iid):
        from lumberjack.core import backpack
        return next((i for i, s in enumerate(backpack.slots() or []) if s["id"] == iid), None)

    def ensure(self, iid, key, amount=1):
        """The slot holding item `iid`, spawning `amount` when there's none."""
        slot = self.slot_of(iid)
        if slot is None:
            items.spawn(self.ctx, key, amount)
            self.sleep(0.6)
            slot = self.slot_of(iid)
        if slot is None:
            raise StopBot(f"no {key.replace('_', ' ')} and couldn't spawn one (backpack full?)")
        return slot

    def use_on(self, iid, key, loc, amount=1):
        """Use item `iid` (spawned if missing) on the patch. True if clicked."""
        from lumberjack.core import interact
        from lumberjack.core.gamestate import top_entry
        slot = self.ensure(iid, key, amount)
        if not actions.use_slot(self.ctx, self.gs, slot, "Use"):
            return False
        for x, y in interact.points_for(loc):
            if not interact.on_screen(x, y):
                continue
            self.inp.move(x + random.randint(-2, 2), y + random.randint(-2, 2))
            self.sleep(random.uniform(0.12, 0.18))
            top = top_entry(self.gs.menu())
            if top and top["verb"] == "Use" and "->" in top["subject"]:
                self.inp.click()
                return True
        actions.cancel_selection(self.ctx, force=True)
        interact.walk_to_tile(self.ctx, self.gs, loc["tile"], arrive=2)     # off screen: get closer
        return False

    def click(self, loc, op):
        from lumberjack.core import interact
        if interact.use_option(self.ctx, self.gs, interact.points_for(loc), op, loc["name"]):
            return True
        interact.walk_to_tile(self.ctx, self.gs, loc["tile"], arrive=2)
        return False

    def wait_changed(self, vb, limit_s, until=None):
        """Wait until the patch's varbit changes (or `until(value)`). True if it did."""
        start = getattr(self, "value", None)
        if start is None:
            start = self.gs.varbits(vb).get(vb)
        end = time.monotonic() + limit_s
        while time.monotonic() < end:
            self.sleep(0.6)
            v = self.gs.varbits(vb).get(vb)
            if (until(v) if until else v != start):
                return True
        return False

    def continue_dialog_yes(self):
        """Digging up a patch asks "Are you sure?" - pick the yes option."""
        from lumberjack.ui import widgets
        for w in widgets.find(self.gs, "yes"):
            if w["w"] > 0:
                x, y = widgets.center(w)
                self.inp.click(x, y)
                return True
        return False

    def make_room(self):
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        if sum(1 for s in inv if s["id"] < 0) >= FREE_SLOTS:
            return
        junk = [i for i, s in enumerate(inv) if s["id"] >= 0 and s["id"] not in KEEP]
        if junk:
            self.state = "dropping the harvest"
            actions.drop_known(self.ctx, self.gs, junk)

    def leave(self):
        """Stay put: a run also ends before every retry, and teleporting back to where it started
        sent us away from the spot each time. The next step travels by itself (Autopilot surfaces
        at Lumbridge first when it needs open ground)."""
        return
