"""Fish + cook (e.g. net shrimps/anchovies at Draynor and cook them on the spot).

    FISH (skills.fishing.Fisher) until the backpack has LOG_ROOM slots left
      -> sort the backpack by hover text (raw / cooked / burnt / logs)
      -> a fire in view? use the raw fish on it.  No fire: chop 1-2 logs from a nearby
         willow (or oak/tree), light one with the tinderbox
      -> "Cook All", repeated (and a new fire lit) until no raw fish are left
      -> drop the burnt fish; drop the cooked fish, or bank them
      -> back to fishing

Spare logs are kept for the next fire. The tools (net, tinderbox, axe) are the items in
the backpack at Start ("Keep what I'm carrying"); a missing tinderbox can be spawned.
With cook=False it's plain fishing (drop or bank the raw fish), plus XP tracking.
"""
import random
import time

from lumberjack import actions
from lumberjack.core import regions as R
from lumberjack.skills import cooking, firemaking
from lumberjack.skills.base import StopBot
from lumberjack.skills.fishing import Fisher
from lumberjack.ui import inventory, mouseover
from lumberjack.vision import trees
from lumberjack.vision.activity import ActivityMonitor
from lumberjack.xp import XpTracker

TREE_LEVELS = {"tree": 1, "oak": 15, "willow": 30, "maple": 45, "yew": 60, "magic": 75}
TREE_LOGS = {"tree": "logs", "oak": "oak_logs", "willow": "willow_logs", "maple": "maple_logs",
             "yew": "yew_logs", "magic": "magic_logs"}
LOG_ROOM = 2               # slots left free for logs when cooking
MAX_FIRES = 4              # fires per load before giving up on the rest of the raw fish
CHOP_TRIES = 8
CHOP_TIMEOUT_S = 60
FIRE_TREE_RADIUS = 20      # tiles searched for a tree to cut fire logs from (game data)


def usable_trees(allowed, levels, available=mouseover.available, names_known=False):
    """Allowed trees (best first) whose names + logs are learned (or come from the game's own
    data: names_known) and that our Woodcutting and Firemaking levels allow (unknown levels
    don't restrict)."""
    wc, fm = levels.get("woodcutting"), levels.get("firemaking")
    if names_known:
        available = lambda n: True
    ok = [t for t in allowed if t in TREE_LEVELS and available(t) and available(TREE_LOGS[t])
          and (wc is None or TREE_LEVELS[t] <= wc) and (fm is None or TREE_LEVELS[t] <= fm)]
    return sorted(ok, key=lambda t: -TREE_LEVELS[t])


def remaining(raw, done):
    return [s for s in raw if s not in set(done)]


class FishCooker(Fisher):
    name = "fishing"

    def __init__(self, cook=True, cooked="drop", fire_trees=("willow", "oak", "tree"),
                 spawn_tools=True, **kw):
        super().__init__(when_full="bank" if cooked == "bank" else "drop", spawn_tools=spawn_tools, **kw)
        self.cook_enabled = cook
        self.learn_catches = cook            # new fish (after a method switch) must read as raw
        self.cooked_action = cooked          # "drop" | "bank"
        self.fire_trees = list(fire_trees)
        if cook:
            self.drop_at = min(self.drop_at, 28 - LOG_ROOM)
        self.xp = XpTracker()
        self.cooked = self.burnt = 0
        self.tinder_slot = None
        self.chop_activity = ActivityMonitor()

    def stats(self):
        hrs = (time.monotonic() - self.started) / 3600
        rate = f" ({self.logs_cut / hrs:.0f}/hr)" if hrs > 0.01 else ""
        cook = f", {self.cooked} cooked, {self.burnt} burnt" if self.cook_enabled else ""
        trips = f", {self.banked} bank trips" if self.banked else ""
        return f"{self.logs_cut} fish{rate}{cook}{trips}"

    # ---- start-up ----------------------------------------------------------------------
    def before_loop(self):
        self.track_xp()
        if not self.cook_enabled:
            return
        from lumberjack.core import backpack
        missing = cooking.missing_templates(self.cooked_action, names_known=backpack.source() is not None)
        if missing:
            raise StopBot("calibrate cooking first - learn " + "; ".join(missing))
        if not usable_trees(self.fire_trees, self.levels, names_known=self.gs is not None):
            raise StopBot("no tree to cut fire logs from - learn the tree and its logs "
                          f"({', '.join(self.fire_trees)}) or tick another tree")
        self.ensure_tinderbox()

    def ensure_tinderbox(self):
        frame = self.grab()
        slot = next((i for i in sorted(self.keep_slots) if actions.tool_icon(frame, i) == "tinderbox"), None)
        if slot is None:
            slot = firemaking.find_tinderbox(self.ctx)
        if slot is None and self.spawn_tools:
            from lumberjack import items
            self.state = "spawning tinderbox"
            self.log.info("No tinderbox - spawning one")
            items.spawn(self.ctx, "tinderbox")
            slot = firemaking.find_tinderbox(self.ctx)
        if slot is None:
            raise StopBot("cooking needs a tinderbox in the backpack (or turn on 'Spawn missing tools')")
        self.keep_slots = set(self.keep_slots) | {slot}
        self.tinder_slot = slot
        self.log.info("Tinderbox in slot %d", slot + 1)

    def track_xp(self):
        from lumberjack.ui import stats
        self.state = "checking xp"
        skills = ["fishing"] + (["cooking", "firemaking", "woodcutting"] if self.cook_enabled else [])
        for s in skills:
            info = stats.read_skill(self.ctx, s)
            if info:
                self.xp.update(s, info)
                self.levels[s] = info["level"]

    # ---- full backpack -----------------------------------------------------------------
    def handle_full_inventory(self):
        if not self.cook_enabled:
            super().handle_full_inventory()
            self.track_xp()
            return
        fish_tile = self._my_tile()          # where we were fishing - logs/fires may take us away
        self.cook_load()
        self.dispose()
        self.track_xp()
        if self.auto and self.check_level():
            self.ensure_tools()
        if self.walker and self.fish_spot and self.when_full != "bank":   # a bank trip walks back itself
            self.walk_to_spot(self.fish_spot)
        elif fish_tile and self.gs:
            from lumberjack.core import interact
            if interact.tiles_apart(self._my_tile() or fish_tile, fish_tile) > 2:
                self.state = "walking back to the fishing spot"
                self.log.info("Walking back to where we were fishing")
                from lumberjack.core.gamestate import tolerant_call
                tolerant_call(self, interact.walk_to_tile, self.ctx, self.gs, fish_tile, 1, logger=self.log)
        actions.reset_camera(self.ctx)

    def _my_tile(self):
        if not self.gs:
            return None
        from lumberjack.core.gamestate import GameStateError
        try:
            return tuple(self.gs.player()["tile"])
        except GameStateError:
            return None

    def cook_load(self):
        self.state = "sorting the catch"
        inv = cooking.sort_backpack(self.ctx, self.keep_slots)
        raw, logs = self.cookable(inv["raw"]), inv["log"]
        if inv["raw"] and not raw:
            return                                # all above our Cooking level: dropped with the load
        if not raw:
            self.log.warning("No raw fish recognised in the backpack - learn their names "
                             "(Fishing > Learn item)")
            return
        self.log.info("Cooking %d raw fish", len(raw))
        start_raw = list(raw)
        total, fires, stalls = len(raw), 0, 0
        while raw and stalls < 3:
            self.state = "cooking"
            status, done = cooking.cook(self.ctx, raw)
            if status == "no_fire":
                if fires >= MAX_FIRES:
                    self.log.warning("Lit %d fires and still can't cook - giving up on this load", fires)
                    break
                logs = self.make_fire(logs)
                if logs is None:
                    break
                fires += 1
                continue
            left = cooking.still_raw(self.ctx, raw)    # trust the hover, not the pixel diff
            stalls = 0 if len(left) < len(raw) else stalls + 1
            raw = left
        self.log.info("Cooked %d of %d", total - len(raw), total)
        from lumberjack.core import backpack
        if not mouseover.available("eat") and backpack.source() is None:
            cooking.learn_eat(self.ctx, [i for i in start_raw if i not in raw])

    def cookable(self, raw):
        """The raw fish our Cooking level can cook (game data; unknown fish are tried). The rest
        is dropped with the load - no fires lit and stalls spent on lobsters at Cooking 30."""
        from lumberjack.core import backpack, gamestate
        from lumberjack.skills.cooking_task import CATCH_LEVELS
        inv, lv = backpack.slots(), (gamestate.skill("cooking") or {}).get("base")
        if inv is None or lv is None:
            return raw
        ok = [i for i in raw if CATCH_LEVELS.get(inv[i]["key"], 0) <= lv]
        if len(ok) < len(raw):
            too = sorted({inv[i]["key"].replace("raw_", "") for i in raw if i not in ok})
            self.log.info("Cooking %d can't cook %s yet - dropping those raw", lv, "/".join(too))
        return ok

    def make_fire(self, logs):
        """Light a fire from a spare log (chopping one first if needed). Returns the logs
        left, or None if no fire could be lit."""
        if not logs:
            logs = self.chop_logs()
            if not logs:
                return None
        self.state = "lighting a fire"
        for _ in range(3):
            actions.dismiss_dialog(self.ctx)
            if firemaking.light_one(self.ctx, self.tinder_slot, logs[0]):
                self.burned += 1
                self.log.info("Lit a fire (%d so far)", self.burned)
                return logs[1:]
            if actions.dismiss_dialog(self.ctx):   # a level-up popped up mid-attempt
                continue
            self.log.info("Couldn't light a fire here - moving a few tiles")
            firemaking._step_aside(self.ctx)
        self.log.warning("Couldn't light a fire")
        return None

    def chop_logs(self):
        """Chop up to LOG_ROOM logs from a nearby tree. Returns the new log slots."""
        types = usable_trees(self.fire_trees, self.levels, names_known=self.gs is not None)
        before = {i for i, o in enumerate(inventory.occupied(self.grab())) if o}
        want = min(LOG_ROOM, 28 - len(before))
        if want <= 0:
            self.log.warning("No room in the backpack for a log")
            return []
        self.state = "chopping logs for a fire"
        self.ensure_axe()
        for _ in range(CHOP_TRIES):
            got = {i for i, o in enumerate(inventory.occupied(self.grab())) if o} - before
            if len(got) >= want:
                break
            kind = self.click_tree(types)
            if not kind:
                self.scan_camera("tree")
                continue
            self.wait_chop(before, want)
        new = sorted({i for i, o in enumerate(inventory.occupied(self.grab())) if o} - before)
        if new:
            self.log.info("Chopped %d log(s) for a fire", len(new))
        else:
            self.log.warning("Couldn't chop any %s logs nearby", "/".join(types))
        return new

    def ensure_axe(self):
        """Chopping fire logs needs an axe ("You do not have an axe to use."): spawn one when
        none is carried or worn, and keep it."""
        if not (self.gs and self.spawn_tools):
            return
        from lumberjack import items
        from lumberjack.core import backpack, gamestate
        lv = (gamestate.skill("woodcutting") or {}).get("base", 1)
        slot = items.ensure_best(self.ctx, items.AXE_ORDER, items.best_axe(lv), log=self.log, gs=self.gs)
        if slot is not None and slot >= 0:
            self.keep_slots = set(self.keep_slots) | {slot}
        for i, it in enumerate(backpack.slots() or []):   # an axe carried before stays kept too
            if it["key"] in items.AXE_ORDER:
                self.keep_slots = set(self.keep_slots) | {i}

    def click_tree(self, types):
        if self.gs:
            from lumberjack.core.gamestate import tolerant_call
            kind = tolerant_call(self, self.click_tree_gs, types, logger=self.log)
            if self.gs:
                return kind
        for cand in trees.find_trees(self.grab())[:6]:
            bx, by, bw, bh = cand.bbox
            for px, py in [(cand.x, cand.y), (bx + bw // 2, by + bh // 3)]:
                px += random.randint(-3, 3)
                py += random.randint(-3, 3)
                if not R.VIEWPORT.contains(px, py) or R.MOUSEOVER_TEXT.contains(px, py):
                    continue
                self.inp.move(px, py)
                self.sleep(random.uniform(0.12, 0.2))
                kind = mouseover.which(self.grab(), "chop_down", types)
                if kind:
                    self.inp.click()
                    self.log.info("Chopping a %s at (%d, %d)", kind, px, py)
                    return kind
        return None

    def click_tree_gs(self, types, walked=False):
        """A usable tree from the game's scene data - off screen too (e.g. the trees next to
        Draynor bank): walk toward the nearest, then chop it."""
        from lumberjack.core import interact
        from lumberjack.skills.woodcutting import TREE_BY_NAME
        near = [(TREE_BY_NAME.get(l["name"].lower()), l) for l in self.gs.locs(FIRE_TREE_RADIUS)]
        near = [(k, l) for k, l in near if k in types and "Chop down" in l["ops"]]
        near.sort(key=lambda kl: (types.index(kl[0]), kl[1]["dist"]))   # best type, then nearest
        for kind, loc in [kl for kl in near if interact.on_screen(*kl[1]["screen"])][:4]:
            if interact.use_option(self.ctx, self.gs, interact.points_for(loc), "Chop down", loc["name"]):
                self.log.info("Chopping a %s for a fire (%d tile(s) away)", loc["name"].lower(), loc["dist"])
                return kind
        if near and not walked:
            kind, loc = min(near, key=lambda kl: kl[1]["dist"])
            self.state = f"walking to a {loc['name'].lower()}"
            self.log.info("No usable tree on screen - walking to a %s %d tiles away", loc["name"].lower(), loc["dist"])
            interact.walk_toward(self.ctx, self.gs.player()["tile"], loc["tile"])
            end = time.monotonic() + 12
            self.sleep(0.6)
            while time.monotonic() < end and self.gs.player().get("moving"):
                self.sleep(0.2)
            return self.click_tree_gs(types, walked=True)
        if not near:
            self.log.info("No %s within %d tiles", "/".join(types), FIRE_TREE_RADIUS)
        return None

    def wait_chop(self, before, want):
        self.chop_activity.reset()
        start = time.monotonic()
        started = False
        while time.monotonic() - start < CHOP_TIMEOUT_S:
            frame = self.grab()
            self.chop_activity.update(frame)
            got = {i for i, o in enumerate(inventory.occupied(frame)) if o} - before
            if len(got) >= want or inventory.is_full(frame):
                return
            if self.chop_activity.active:
                started = True
            elif self.chop_activity.filled and (started or time.monotonic() - start > 5):
                return
            if actions.dismiss_dialog(self.ctx):
                return
            self.sleep(0.2)

    def dispose(self):
        """Count the results, drop the burnt fish and either drop or bank the cooked ones.
        Spare logs stay for the next fire (when dropping)."""
        self.state = "sorting the catch"
        inv = cooking.sort_backpack(self.ctx, self.keep_slots)
        self.cooked += len(inv["cooked"])
        self.burnt += len(inv["burnt"])
        self.log.info("%d cooked, %d burnt this load. %s", len(inv["cooked"]), len(inv["burnt"]), self.stats())
        if self.when_full == "bank":
            if inv["burnt"]:
                self.state = "dropping burnt fish"
                actions.drop_all(self.ctx, keep=set(range(28)) - set(inv["burnt"]))
            self.state = "banking"
            if not self.bank_trip():
                raise StopBot("couldn't bank - stopped with the fish kept")
            return
        from lumberjack.core import backpack
        if inv["other"] and not mouseover.available("eat") and backpack.source() is None:
            raise StopBot("couldn't recognise the cooked fish - use Fishing > Learn item on one "
                          "(e.g. Shrimps) to teach 'Eat'")
        self.state = "dropping"
        actions.drop_all(self.ctx, keep=set(self.keep_slots) | set(inv["log"]), food=True)
