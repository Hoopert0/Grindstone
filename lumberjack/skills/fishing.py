"""Fishing: find a fishing spot, use it with the chosen method, drop or bank the catch.

    FIND_SPOT --(none in view)--> SCAN_CAMERA --> walk to fish_spot / wait --> give up
        |  hover says "<Action> Fishing spot"  (spot NPC name in YELLOW)
        |  left-click if <Action> is our method, else right-click > our option
        v
      FISH  --(idle / no catch for a while / spot moved)--> FIND_SPOT
        |  inventory full
        v
    DROP (all but kept slots) or BANK TRIP --> FIND_SPOT

Fishing spots offer two methods each; the hover text shows the left-click one:
    net/bait spot (Draynor, Lumbridge swamp): "Net"  (right-click: "Bait")
    lure/bait spot (rivers):                  "Lure" (right-click: "Bait")
    cage/harpoon spot:                        "Cage" (right-click: "Harpoon")
    net/harpoon spot:                         "Net"  (right-click: "Harpoon")

Templates (captured in game, see the calibration helpers at the bottom):
    assets/templates/mouseover/<action>.png   white action word, e.g. net.png, lure.png
    assets/templates/mouseover/fishing_spot.png   yellow "Fishing spot"
    assets/templates/menu/<method>.png        menu row action word, e.g. bait.png

Tools/bait are whatever the backpack holds at Start (the keep-list), plus anything spawned
with spawn_tools (singleplayer ::item). Bait and feathers are a stack that gets used up: when
a kept slot empties the bait is gone - respawned with spawn_tools, else the bot stops.
"""
import random
import time

from lumberjack import actions
from lumberjack.core import regions as R
from lumberjack.skills.base import BotBase, StopBot
from lumberjack.ui import inventory, menu, mouseover
from lumberjack.vision import fishing_spots
from lumberjack.vision.activity import ActivityMonitor

SERVER_TICK = 0.6

TARGET = "fishing_spot"          # mouseover template: yellow "Fishing spot"
TARGET_COLOR = "yellow"          # NPC names are yellow
# method -> Fishing level needed (the cheapest fish of that method)
METHOD_LEVELS = {"net": 1, "bait": 5, "lure": 20, "harpoon": 35, "cage": 40}
# every action word a fishing spot can show on hover (its left-click option)
HOVER_ACTIONS = ("net", "bait", "lure", "cage", "harpoon")
# template names, per method: hover word (mouseover/) and menu row word (menu/)
HOVER_TEMPLATE = {m: m for m in METHOD_LEVELS}
MENU_TEMPLATE = {m: m for m in METHOD_LEVELS}
# left-click (hover) word of the spots that offer each method; a method that isn't its
# spots' left-click option is picked from the right-click menu
SPOT_LEFT_CLICK = {"net": ("net",), "lure": ("lure",), "cage": ("cage",),
                   "bait": ("net", "lure"), "harpoon": ("cage", "net")}
# spot type (its left-click word) -> the methods it offers, left-click first. Net spots are
# taken to be net/bait (Lumbridge swamp, Draynor); sea net/harpoon spots aren't supported.
OFFERS = {"net": ("net", "bait"), "lure": ("lure", "bait"), "cage": ("cage", "harpoon")}
# the menu verb of each method, as the game names it ("Net Fishing spot")
METHOD_VERB = {"net": "Net", "bait": "Bait", "lure": "Lure", "cage": "Cage", "harpoon": "Harpoon"}
SPOT_NAME = "Fishing spot"
# methods that use up a stack (fishing bait / feathers)
USES_BAIT = {"bait": "fishing bait", "lure": "feathers"}
TOOLS = {"net": "small fishing net", "bait": "fishing rod + fishing bait",
         "lure": "fly fishing rod + feathers", "cage": "lobster pot", "harpoon": "harpoon"}
# the same as items.CATALOG keys - what 'Spawn missing tools' spawns per method
TOOL_ITEMS = {"net": ["small_fishing_net"], "bait": ["fishing_rod", "fishing_bait"],
              "lure": ["fly_fishing_rod", "feather"], "cage": ["lobster_pot"], "harpoon": ["harpoon"]}
STACKS = {"fishing_bait": 500, "feather": 500}   # stackables spawn as one stack of this many

CANDIDATES_TRIED = 6
WALK_TO_SPOT_TILES = 40           # an off-screen spot this close is walked to (game data)
GS_IDLE_READS = 12                # ~2.4 s of "not animating, walking or facing a spot" = stopped
HOVER_READS = (0.12, 0.12, 0.15)  # seconds before each re-read of one hover point
HOVER_OFFSETS = [(0, 0), (0, -5), (-5, 3), (5, 3), (0, -11), (-9, -4), (9, -4)]
BAD_SPOT_S = 8                   # skip a non-spot hover position for this long
FRAME_GAP_S = 0.3                # ripples animate: look at two frames this far apart
FISH_TIMEOUT_S = 600             # hard cap on one click
NO_CATCH_S = 75                  # busy (or "busy") this long without a catch -> re-click
NEVER_STARTED_S = 6              # no movement this long after clicking -> missed
DUD_SPOT_S = 45                  # a spot a click caught nothing at is passed over this long
MAX_FAILED_CLICKS = 6            # clicks in a row that caught nothing...
CHECKED_S = 120.0                # a stretch of shore that had no spot for us: skip it this long
NO_CATCH_STOP_S = 180.0          # ...and nothing caught for this long -> stop (spots hop about a lot)
SCANS_BEFORE_MOVING = 4
EMPTY_ROUNDS_BEFORE_GIVING_UP = 12
WAIT_FOR_SPOT_S = 8              # spots move; wait a bit before scanning again


# ---- pure helpers (unit tested) ---------------------------------------------------------
def click_plan(hover_action, method, menu_ready):
    """How to use a hovered spot: 'left' (left-click does our method), 'menu' (right-click
    and pick our option - learning the menu word first if needed), or None (not a fishing
    spot / this spot doesn't offer our method)."""
    if not hover_action:
        return None
    if hover_action == method:
        return "left"
    return "menu" if menu_ready or method in OFFERS.get(hover_action, ()) else None


SHARK_LEVEL = 76


def harpoon_spots(offering, method, level):
    """Harpoon is on two kinds of spot: cage/harpoon (tuna, swordfish) and net/harpoon (sharks,
    76+). Below 76 a net/harpoon spot only says "You need level 76", so skip those; from 76 on
    prefer them. Other methods: unchanged."""
    if method != "harpoon":
        return offering
    sword = [sp for sp in offering if "Cage" in sp["ops"]]
    shark = [sp for sp in offering if "Net" in sp["ops"]]
    if level is not None and level >= SHARK_LEVEL:
        return shark or sword
    return sword


def usable_method(method, spot_action, map_methods, missing):
    """Can we fish `method` right now? `spot_action` = the left-click word of the spot we're
    at (None = not seen yet), `map_methods` = methods some saved fishing spot lists,
    `missing` = missing_now(method). Returns (ok, why-not)."""
    if missing:
        return False, "not calibrated (" + ", ".join(missing) + ")"
    if spot_action is None or method in OFFERS.get(spot_action, ()):
        return True, None
    if method in map_methods:
        return True, None
    return False, "no spot offering it here - save a Fishing spot for it on the map"


def methods_to_switch(level, methods, current, spot_action, map_methods, missing_now):
    """(best usable method for `level`, {method: why-not} for better ones we can't use yet).
    The current method gets no free pass: if the spots here don't offer it, it's dropped too."""
    ok, blocked = [], {}
    for m in methods:
        if m not in METHOD_LEVELS or (level is not None and METHOD_LEVELS[m] > level):
            continue
        good, why = usable_method(m, spot_action, map_methods, missing_now(m))
        if good:
            ok.append(m)
        else:
            blocked[m] = why
    best = pick_method(ok, level)
    if best:
        blocked = {m: w for m, w in blocked.items() if METHOD_LEVELS[m] > METHOD_LEVELS[best]}
    return best, blocked


def pick_method(methods, level):
    """Best method we have the level for (methods: allowed list). None if none fits.
    level=None (unknown) -> the first allowed method."""
    methods = [m for m in methods if m in METHOD_LEVELS]
    if not methods:
        return None
    if level is None:
        return methods[0]
    ok = [m for m in methods if METHOD_LEVELS[m] <= level]
    return max(ok, key=lambda m: METHOD_LEVELS[m]) if ok else None


def catch_count(occupied, keep_slots):
    """Occupied slots that aren't kept (tools/bait) = fish in the backpack."""
    return sum(1 for i, o in enumerate(occupied) if o and i not in keep_slots)


def emptied_kept(occupied, keep_slots):
    """Kept slots that are now empty (bait/feathers used up, or a tool gone)."""
    return sorted(i for i in keep_slots if i < len(occupied) and not occupied[i])


def missing_templates(method, available=mouseover.available, menu_available=None):
    """Template names still to capture before `method` can run (empty list = ready).

    Needed: the yellow target, plus either our own hover word (we're the left-click
    option) or some other hover word + our menu word (right-click option)."""
    if menu_available is None:
        menu_available = _menu_available
    missing = []
    if not available(TARGET):
        missing.append(f"mouseover/{TARGET}")
    firsts = SPOT_LEFT_CLICK[method]
    if not any(available(HOVER_TEMPLATE[a]) for a in firsts):
        missing.append(" or ".join(f"mouseover/{HOVER_TEMPLATE[a]}" for a in firsts))
    if method not in firsts and not menu_available(MENU_TEMPLATE[method]):
        missing.append(f"menu/{MENU_TEMPLATE[method]}")
    return missing


def _menu_available(name):
    return (menu.TEMPLATES / f"{name}.png").exists()


def missing_now(method, available=mouseover.available):
    """missing_templates, minus the menu word: the bot learns that itself the first time it
    right-clicks a spot that offers the method (e.g. 'Bait' on a net spot)."""
    return missing_templates(method, available, menu_available=lambda n: True)


def hover_kind(strip):
    """What a hover strip shows, for the log when no spot was found: 'nothing', 'walk' (only
    white text, e.g. "Walk here"), 'npc' (a yellow name), 'object' (cyan), 'item' (orange -
    something in the backpack may be selected: "Use Raw shrimps -> ...")."""
    for color, kind in (("orange", "item"), ("yellow", "npc"), ("cyan", "object")):
        if mouseover.strip_mask(strip, color).sum() // 255 >= 15:
            return kind
    return "walk" if mouseover.strip_mask(strip, "white").sum() // 255 >= 15 else "nothing"


def hover_action(frame):
    """Which fishing action the hover text shows on a Fishing spot ('net', ...), else None."""
    for a in HOVER_ACTIONS:
        t = HOVER_TEMPLATE[a]
        if mouseover.available(t) and mouseover.which(frame, t, [TARGET], TARGET_COLOR):
            return a
    return None


# ---- the bot ----------------------------------------------------------------------------
class Fisher(BotBase):
    name = "fishing"

    def __init__(self, method="net", when_full="drop", fish_spot=None, bank_spot=None,
                 drop_at=28, auto=False, max_fish=None, spawn_tools=True, **kw):
        if max_fish is not None:
            kw.setdefault("max_logs", max_fish)
        super().__init__(**kw)
        self.methods = [method] if isinstance(method, str) else list(method)
        bad = [m for m in self.methods if m not in METHOD_LEVELS]
        if bad or not self.methods:
            raise ValueError(f"unknown fishing method(s) {bad} - pick from {', '.join(METHOD_LEVELS)}")
        self.method = self.methods[0]
        self.when_full = when_full        # "drop" | "bank"
        self.fish_spot, self.bank_spot = fish_spot, bank_spot
        self.drop_at = drop_at
        self.auto = auto                  # read the Fishing level from the Stats tab
        self.spawn_tools = spawn_tools    # ::item the net/rod/bait... if it's missing
        self.levels = {}
        self.activity = ActivityMonitor()
        self.bad_spots = []               # (x, y, expiry)
        self.failed_clicks = 0
        self.empty_rounds = 0
        self.spot_action = None           # left-click word of the spots here ('net', 'lure'...)
        self.level_up = False             # a level-up dialog showed while fishing
        self.hinted = set()               # "lure is unlocked, but..." said once per method
        self.learn_catches = False        # learn unknown catches as 'raw_fish' (for cooking)
        self.bank_anchor = None           # no map: where the bank's minimap icon sits from the spot
        self.gs = None                    # the game's own data (core.gamestate), when readable

    # logs_cut doubles as the fish count (the control panel shows it)
    def progress(self):
        return self.logs_cut

    def stats(self):
        hrs = (time.monotonic() - self.started) / 3600
        rate = f" ({self.logs_cut / hrs:.0f}/hr)" if hrs > 0.01 else ""
        trips = f", {self.banked} bank trips" if self.banked else ""
        return f"{self.logs_cut} fish{rate}{trips}"

    # ---- main loop -------------------------------------------------------------------
    def run(self):
        self.log.info("Fishing (%s) started - press F12 to stop", self.method)
        learner = None
        try:
            self.connect_game_data()            # with game data no templates are needed
            if not any(not self._missing(m) for m in self.methods):
                self.check_templates()          # raises with what's missing
            self.prepare()
            if self.auto:
                self.check_level()
            self.ensure_tools()
            if not self.keep_slots:
                raise StopBot(f"nothing to keep - {self.method} fishing needs {TOOLS[self.method]} in the "
                              f"backpack, and 'Keep what I'm carrying' ticked (or they'd be dropped)")
            if self.walker:
                from lumberjack.nav.learner import MapLearner
                learner = MapLearner(self.walker, self.win)
                learner.start()
            self.check_templates()
            if self.fish_spot and self.walker:
                if not self.walk_to_spot(self.fish_spot):
                    raise StopBot(f"couldn't reach '{self.fish_spot}'")
                actions.reset_camera(self.ctx)
            if self.when_full == "bank" and not self.walker and not self.gs:
                self.remember_bank_icon()       # with game data, booths are found directly
            self.before_loop()
            while True:
                actions.dismiss_dialog(self.ctx)
                if self.gs:
                    actions.cancel_selection(self.ctx)   # a stray "Use <item>" blocks every click
                frame = self.grab()
                occ = inventory.occupied(frame)
                self.check_bait(occ)
                if sum(occ) >= self.drop_at:
                    self.handle_full_inventory()
                    continue
                self.state = "finding fishing spot"
                if not self.find_and_use_spot():
                    self.no_spot_found()
                    continue
                self.empty_rounds = 0
                self.state = f"fishing ({self.method})"
                self.fish()
                if self.level_up:
                    self.level_up = False
                    if self.auto and self.check_level():
                        self.ensure_tools()
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            if learner:
                learner.stop()
            self.inp.close()

    def before_loop(self):
        """Hook for tasks built on Fisher (e.g. fish + cook): runs once, right before fishing."""

    def check_templates(self):
        missing = self._missing(self.method)
        if missing:
            raise StopBot("calibrate first - missing template(s): " + ", ".join(missing))

    def map_fish_spots(self, method=None):
        """[(name, spot)] saved Fishing spots (on the map) listing `method` (None = any)."""
        if not self.walker:
            return []
        return [(n, sp) for n, sp in self.walker.map.spots.items()
                if sp.get("kind") == "fish" and (method is None or method in (sp.get("trees") or []))]

    def check_level(self):
        """Auto mode: read Fishing and switch to the best method we have the level for, can
        fish here (or at a saved spot) and have calibrated. True if the method changed."""
        from lumberjack.ui import stats
        self.state = "checking levels"
        lv = stats.read_level(self.ctx, "fishing")
        if not lv:
            self.log.warning("Couldn't read the Fishing level - fishing with %s", self.method)
            return False
        self.levels["fishing"] = lv[0]
        return self.choose_method(lv[0])

    def choose_method(self, level):
        """Switch to the best usable method for `level`. True if the method changed."""
        map_methods = {m for _, sp in self.map_fish_spots() for m in (sp.get("trees") or [])}
        best, blocked = methods_to_switch(level, self.methods, self.method, self.spot_action,
                                          map_methods, self._missing)
        for m, why in blocked.items():
            if m not in self.hinted:
                self.hinted.add(m)
                self.log.info("Fishing %d unlocks %s fishing, but %s", level, m, why)
        if best is None:
            if any(METHOD_LEVELS[m] <= level for m in self.methods):
                raise StopBot(f"none of {'/'.join(self.methods)} can be fished here ({self.spot_action} spots)"
                              " - calibrate it or save a Fishing spot for it on the map")
            need = min(METHOD_LEVELS[m] for m in self.methods)
            raise StopBot(f"Fishing level {level} is too low for {'/'.join(self.methods)} (needs {need})")
        if best == self.method:
            return False
        self.log.info("Fishing level %d - switching from %s to %s fishing", level, self.method, best)
        self.method = best
        self.failed_clicks = 0
        if self.spot_action is not None and best not in OFFERS.get(self.spot_action, ()):
            spots = self.map_fish_spots(best)
            if spots:
                self.fish_spot = spots[0][0]
                self.spot_action = None
                if self.walk_to_spot(self.fish_spot):
                    actions.reset_camera(self.ctx)
        return True

    def busy_from_game(self):
        """True while the game shows us animating (fishing) or walking, None without game data."""
        if not self.gs:
            return None
        from lumberjack.core.gamestate import GameStateError
        try:
            pl = self.gs.player()
        except GameStateError:
            return None
        # the fishing animation drops out between casts: still facing the spot counts as fishing
        return pl.get("anim", -1) != -1 or bool(pl.get("moving")) or pl.get("interacting", -1) not in (-1, None)

    def learn_catch(self, new):
        """Make sure the newest catch is recognised as a raw fish: an unknown "Use <name>" in
        a fresh slot is saved as a sample of the generic 'raw_fish' name (new fish arrive
        with a new method - trout, salmon... - and would otherwise be taken for burnt fish)."""
        from lumberjack.core import backpack
        from lumberjack.skills import cooking
        if backpack.source() is not None:
            return          # the game names every catch - nothing to learn
        for i in new[-1:]:
            if cooking.slot_kind(self.ctx, i) == "raw":
                continue
            strip = R.MOUSEOVER_TEXT.crop(self.grab())
            if mouseover.action_ok(strip, "use") and mouseover.strip_mask(strip, "orange").any():
                if mouseover.available(cooking.GENERIC_RAW):
                    mouseover.add_variant(strip, cooking.GENERIC_RAW, "orange", any_width=True)
                else:
                    from lumberjack.tools.calibrate_name import save_name
                    save_name(strip, cooking.GENERIC_RAW, "orange")
                self.log.info("Learned a new kind of raw fish (slot %d)", i + 1)
        self.inp.move(260, 300)

    # ---- tools ------------------------------------------------------------------------
    def find_tool(self, tool):
        """Backpack slot holding `tool` (by name from the game, else its icon or hover name), or None."""
        from lumberjack.core import backpack
        found = backpack.find(tool)
        if found is not None:
            return found[0] if found else None
        actions.open_tab(self.ctx, "inventory")
        frame = self.grab()
        occ = inventory.occupied(frame)
        for i, o in enumerate(occ):
            if o and actions.tool_icon(frame, i) == tool:
                return i
        if not mouseover.available(tool):
            return None
        found = None
        for i, o in enumerate(occ):
            if not o or actions.tool_icon(frame, i):
                continue
            self.inp.move(*R.INV_SLOTS[i].center, steps=2)
            self.sleep(0.2)
            if mouseover.which(self.grab(), "use", [tool], "orange"):
                found = i
                break
        self.inp.move(260, 300)
        return found

    def ensure_tools(self):
        """Make sure the method's tools/bait are in the backpack and kept; spawn what's missing."""
        for tool in TOOL_ITEMS[self.method]:
            slot = self.find_tool(tool)
            if slot is None and self.spawn_tools:
                slot = self.spawn_tool(tool)
            if slot is not None:
                self.keep_slots = set(self.keep_slots) | {slot}
        self.drop_other_methods_tools()

    def drop_other_methods_tools(self):
        """Lobsters at Catherby don't need the fly rod, feathers and net from earlier tiers: drop
        other methods' gear (named by the game's data) - it's spawned again if the method changes."""
        if not (self.gs and self.spawn_tools):
            return
        from lumberjack.core import backpack
        inv = backpack.slots()
        if inv is None:
            return
        mine = set(TOOL_ITEMS[self.method])
        other = {t for tools in TOOL_ITEMS.values() for t in tools} - mine
        drop = [i for i, s in enumerate(inv) if s["key"] in other]
        if not drop:
            return
        self.log.info("Dropping fishing gear %s doesn't use: %s", self.method,
                      ", ".join(sorted({inv[i]["key"].replace("_", " ") for i in drop})))
        actions.drop_known(self.ctx, self.gs, drop)
        self.keep_slots = set(self.keep_slots) - set(drop)

    def spawn_tool(self, tool):
        """::item the tool and remember what it looks like. Returns its slot or None."""
        from lumberjack import items
        from lumberjack.core import backpack, gamestate
        before = inventory.occupied(self.grab())
        if all(before) and gamestate.shared() is not None and backpack.slots() is not None:
            actions.drop_all(self.ctx, keep=self.keep_slots, food=True)     # fish make room for the tool
            before = inventory.occupied(self.grab())
        if all(before):
            raise StopBot(f"no room in the backpack for a {tool.replace('_', ' ')}")
        self.state = f"spawning {tool.replace('_', ' ')}"
        self.log.info("No %s - spawning %s", tool.replace("_", " "),
                      f"{STACKS[tool]}" if tool in STACKS else "one")
        items.spawn(self.ctx, tool, STACKS.get(tool, 1))
        self.sleep(SERVER_TICK)
        frame = self.grab()
        new = [i for i, o in enumerate(inventory.occupied(frame)) if o and not before[i]]
        if not new:
            self.log.warning("Spawning %s didn't add anything to the backpack", tool.replace("_", " "))
            return None
        slot = new[0]
        if gamestate.shared() is None:            # the game names it: no picture to learn
            learn_tool(self.ctx, slot, tool, stack=tool in STACKS)
        return slot

    def check_bait(self, occ):
        gone = emptied_kept(occ, self.keep_slots)
        if not gone:
            return
        if self.spawn_tools:
            self.log.info("A kept slot emptied (%s) - getting the %s again", ", ".join(str(s + 1) for s in gone),
                          TOOLS[self.method])
            self.keep_slots = set(self.keep_slots) - set(gone)
            self.ensure_tools()
            return
        slots = ", ".join(str(s + 1) for s in gone)
        if self.method in USES_BAIT:
            raise StopBot(f"out of {USES_BAIT[self.method]} (kept slot {slots} is empty)")
        raise StopBot(f"a kept item disappeared (slot {slots}) - is the {TOOLS[self.method]} still there?")

    # ---- finding & clicking ----------------------------------------------------------
    def _candidates(self):
        f1 = self.grab()
        self.sleep(FRAME_GAP_S)
        f2 = self.grab()
        return fishing_spots.merge(fishing_spots.find_fishing_spots(f2, f1),
                                   fishing_spots.find_fishing_spots(f1))

    def _hover(self, x, y):
        """Move there and read the hover text a few times: with players and the spot on the
        same tiles ("/ 6 more options") the top line can flip between frames. Returns
        (action, kind) - action as hover_action, kind as hover_kind of the last read."""
        self.inp.move(x, y)
        kind = "nothing"
        for wait in HOVER_READS:
            self.sleep(wait)
            frame = self.grab()
            act = hover_action(frame)
            if act:
                return act, "npc"
            kind = hover_kind(R.MOUSEOVER_TEXT.crop(frame))
        return None, kind

    def _missing(self, method):
        """Templates still to learn for `method` - none when the game's own data is readable
        (spots come from its NPC list and the menu text confirms the option)."""
        return [] if self.gs else missing_now(method)

    def connect_game_data(self):
        from lumberjack.core.gamestate import GameState
        gs = GameState()
        if gs.available():
            self.gs = gs
            self.log.info("Reading the game's own data (spots, menu) - no pixel guessing")
        else:
            self.log.info("Game data not readable (restart the game to rebuild the add-on) - using the screen")

    def find_and_use_spot(self):
        """Use the nearest fishing spot offering our method. True if we clicked one."""
        if self.gs:
            from lumberjack.core.gamestate import tolerant_call
            r = tolerant_call(self, self.find_and_use_spot_gs, logger=self.log)
            if self.gs:
                return bool(r)
        return self.find_and_use_spot_pixels()

    def find_and_use_spot_gs(self, walked=False):
        """Spots from the game's NPC list, projected to the screen by the game itself; the
        menu entries (text, not pixels) confirm what a click will do."""
        from lumberjack.core.gamestate import find_entry, menu_row_point, top_entry
        from lumberjack.nav import spot_memory
        verb = METHOD_VERB[self.method]
        spots = self.gs.npcs(SPOT_NAME)
        for sp in spots:
            spot_memory.remember(sp)
        offering = harpoon_spots([sp for sp in spots if verb in sp["ops"]], self.method,
                                 (getattr(self, "levels", None) or {}).get("fishing"))
        if not offering and not walked and self.walk_to_remembered_spot(verb):
            return self.find_and_use_spot_gs(walked=True)
        if not spots:
            self.report_gs("no fishing spots nearby")
            return False
        if not offering:
            self.spot_action = spots[0]["ops"][0].lower() if spots[0]["ops"] else None
            self.log.info("The spots here offer %s - no %s option", "/".join(spots[0]["ops"]), verb)
            if self.auto and "fishing" in self.levels and self.choose_method(self.levels["fishing"]):
                self.ensure_tools()
            return False
        now = time.monotonic()
        dud = getattr(self, "dud_spots", {})
        fresh = [sp for sp in offering if dud.get(sp.get("index"), 0) <= now]
        offering = fresh or offering            # every spot gave nothing lately: try them anyway
        visible = [sp for sp in offering if self._on_screen(*sp["screen"])]
        if not visible:
            near = min(offering, key=lambda sp: sp["dist"])
            if near["dist"] <= WALK_TO_SPOT_TILES and not walked:   # e.g. back from a fire or the bank
                from lumberjack.core import interact
                self.state = "walking to the fishing spot"
                self.log.info("Fishing spot %d tiles away, off screen - walking toward it", near["dist"])
                interact.walk_toward(self.ctx, self.gs.player()["tile"], near["tile"])
                end = time.monotonic() + 12
                self.sleep(0.6)
                while time.monotonic() < end and self.gs.player().get("moving"):
                    self.sleep(0.2)
                return self.find_and_use_spot_gs(walked=True)
            self.report_gs(f"{len(offering)} spot(s) nearby but none on screen")
            return False
        visible.sort(key=lambda sp: sp["dist"])
        for sp in visible[:4]:
            self.spot_action = sp["ops"][0].lower()
            (gx, gy), (bx, by) = sp["screen"], sp["body"]
            for x, y in ((gx, gy), ((gx + bx) // 2, (gy + by) // 2), (gx, gy - 6)):
                if not self._on_screen(x, y):
                    continue
                self.inp.move(x + random.randint(-2, 2), y + random.randint(-2, 2))
                self.sleep(random.uniform(0.08, 0.14))
                menu = self.gs.menu()
                top = top_entry(menu)
                if top and top["verb"] == verb and top["subject"] == SPOT_NAME:
                    self.last_spot = sp.get("index")
                    self.inp.click()
                    self.log.info("Clicked %s at a spot %d tile(s) away", verb, sp["dist"])
                    return True
                if find_entry(menu, verb, SPOT_NAME):
                    # our option is in the menu but not on top (bait at a net spot, or players
                    # on the same tile): right-click and pick its row
                    px, py = self.inp.pos
                    self.inp.right_click(px, py)
                    self.sleep(random.uniform(0.2, 0.3))
                    menu = self.gs.menu()
                    e = find_entry(menu, verb, SPOT_NAME)
                    if menu.get("open") and e:
                        rx, ry = menu_row_point(menu, e["row"])
                        self.last_spot = sp.get("index")
                        self.inp.click(rx + random.randint(-12, 12), ry + random.randint(-1, 1))
                        self.log.info("Chose %s from the menu (row %d)", verb, e["row"] + 1)
                        return True
                    self.inp.move(px, max(R.VIEWPORT.y + 30, py - 90))   # leaving closes the menu
                    self.sleep(0.2)
        self.report_gs(f"hovered {len(visible[:4])} spot(s) but the menu never offered {verb}")
        return False

    REMEMBERED_MAX = 80        # walk to a remembered spot this far away at most

    def walk_to_remembered_spot(self, verb):
        """No spot offering `verb` in range (the game only lists NPCs ~15 tiles around us): walk to
        the nearest place one was seen before (or a ★ fishing place). True if we walked."""
        from lumberjack.core import interact
        from lumberjack.nav import spot_memory
        me = self.gs.player()["tile"]
        now = time.monotonic()
        checked = {t: until for t, until in getattr(self, "checked_spots", {}).items() if until > now}
        checked[tuple(me)] = now + CHECKED_S          # nothing for us here right now
        self.checked_spots = checked
        tile, source = spot_memory.nearest(verb, me, self.REMEMBERED_MAX, skip=list(checked))
        if tile is None:
            return False
        checked[tuple(tile)] = now + CHECKED_S        # if it's empty there too, try the next one
        dist = max(abs(tile[0] - me[0]), abs(tile[1] - me[1]))
        self.state = "walking back to the fishing spots"
        self.log.info("No %s spot in range - walking to %s (%d tiles)",
                      verb.lower(), "where one was seen" if source == "remembered" else source, dist)
        ok = interact.walk_to_tile(self.ctx, self.gs, list(tile), arrive=4, max_clicks=14)
        actions.reset_camera(self.ctx)
        return ok or max(abs(tile[0] - self.gs.player()["tile"][0]),
                         abs(tile[1] - self.gs.player()["tile"][1])) < dist

    def report_gs(self, what):
        if getattr(self, "_last_gs_report", None) != what:
            self.log.info("No fishing spot to click: %s", what)
            self._last_gs_report = what

    @staticmethod
    def _on_screen(x, y):
        return R.VIEWPORT.contains(x, y) and not R.MOUSEOVER_TEXT.contains(x, y)

    def find_and_use_spot_pixels(self):
        """Hover candidates; use the first verified fishing spot. True if we clicked one."""
        now = time.monotonic()
        self.bad_spots = [b for b in self.bad_spots if b[2] > now]
        menu_ready = _menu_available(MENU_TEMPLATE[self.method])
        seen = {}
        cands = self._candidates()[:CANDIDATES_TRIED]
        for cand in cands:
            if any(abs(cand.x - bx) < 15 and abs(cand.y - by) < 15 for bx, by, _ in self.bad_spots):
                continue
            for dx, dy in HOVER_OFFSETS:
                px = cand.x + dx + random.randint(-2, 2)
                py = cand.y + dy + random.randint(-2, 2)
                if not R.VIEWPORT.contains(px, py) or R.MOUSEOVER_TEXT.contains(px, py):
                    continue
                act, kind = self._hover(px, py)
                if act:
                    self.spot_action = act
                else:
                    seen[kind] = seen.get(kind, 0) + 1
                    continue
                plan = click_plan(act, self.method, menu_ready)
                if plan == "left" and not menu_ready and self._learn_menu_row(px, py):
                    self.log.info("Clicked %s at (%d, %d) via its menu (learned the option)", self.method, px, py)
                    return True
                if plan == "left":
                    self.inp.click()
                    self.log.info("Clicked %s at (%d, %d), %.0f px away", self.method, px, py, cand.dist)
                    return True
                if plan == "menu" and self._use_via_menu(px, py):
                    self.log.info("Chose %s from the menu at (%d, %d)", self.method, px, py)
                    return True
                if act:   # a fishing spot, but not one offering our method (or the menu failed)
                    self.log.info("Spot at (%d, %d) is '%s' - no %s option", px, py, act, self.method)
                    if self.auto and plan is None and "fishing" in self.levels:
                        # now we know what spots are here: pick again (or walk to a saved spot)
                        if self.choose_method(self.levels["fishing"]):
                            self.ensure_tools()
                            return False
                    break
            else:
                # no hover read cleanly (players on the same tiles put "Walk here" or their
                # names on top): the right-click menu lists every option - pick ours there
                if menu_ready and self._use_via_menu(cand.x, cand.y):
                    self.log.info("Chose %s from the menu at (%d, %d) (hover text unclear)",
                                  self.method, cand.x, cand.y)
                    return True
            self.bad_spots.append((cand.x, cand.y, now + BAD_SPOT_S))
        self.report_no_spot(cands, seen)
        return False

    def report_no_spot(self, cands, seen):
        """Say what the hovers showed (and keep a capture) so a failed search can be diagnosed."""
        if not cands:
            self.log.info("No ripples found in view")
            return
        what = ", ".join(f"{n}x {k}" for k, n in sorted(seen.items(), key=lambda kv: -kv[1]))
        hint = ""
        if seen.get("item"):
            hint = " - an item looks selected ('Use ... ->'): click the backpack tab to cancel it"
        elif seen.get("npc"):
            hint = " - a yellow name showed but didn't read as '<method> Fishing spot': Learn fishing spot again?"
        self.log.info("Hovered %d ripple(s), no fishing spot: hover showed %s%s", len(cands), what or "-", hint)
        try:
            import cv2
            from pathlib import Path
            out = Path(__file__).resolve().parents[1] / "captures"
            out.mkdir(exist_ok=True)
            cv2.imwrite(str(out / "no_fishing_spot.png"), fishing_spots.draw(self.grab(), cands))
        except Exception:
            pass

    def _learn_menu_row(self, x, y):
        """Hover reads '<method> Fishing spot': right-click, save the top menu row's word as
        the method's menu template (so unclear hovers can use the menu later), click it."""
        self.inp.right_click(x, y)
        self.sleep(random.uniform(0.25, 0.35))
        frame = self.grab()
        m = menu.find(frame, (x, y))
        if not m or not menu.rows(m):
            self.inp.move(x, max(R.VIEWPORT.y + 30, y - 90))
            self.sleep(0.2)
            return False
        row = menu.rows(m)[0]
        menu.save_action_template(frame, row, MENU_TEMPLATE[self.method])
        self.log.info("Learned the '%s' menu option", self.method.capitalize())
        rx, ry = row.center
        self.inp.click(rx + random.randint(-10, 10), ry + random.randint(-2, 2))
        return True

    def _use_via_menu(self, x, y):
        self.inp.right_click(x, y)
        self.sleep(random.uniform(0.25, 0.35))
        frame = self.grab()
        m = menu.find(frame, (x, y))
        name = MENU_TEMPLATE[self.method]
        if m and not _menu_available(name):
            # first time: this spot offers the method as its 2nd option - learn that row's word
            offers = OFFERS.get(self.spot_action, ())
            rows = menu.rows(m)
            if self.method in offers and len(rows) > offers.index(self.method):
                menu.save_action_template(frame, rows[offers.index(self.method)], name)
                self.log.info("Learned the '%s' menu option", self.method.capitalize())
        opt = menu.find_option(frame, m, name) if m and _menu_available(name) else None
        if not opt:
            self.inp.move(x, max(R.VIEWPORT.y + 30, y - 90))   # leaving the menu closes it
            self.sleep(0.2)
            return False
        rx, ry = opt[1].center
        self.inp.click(rx + random.randint(-10, 10), ry + random.randint(-2, 2))
        return True

    def no_spot_found(self):
        self.empty_rounds += 1
        self.state = "looking for a fishing spot"
        if self.empty_rounds <= SCANS_BEFORE_MOVING:
            self.scan_camera()
            return
        if self.walker and self.fish_spot and self.empty_rounds == SCANS_BEFORE_MOVING + 1:
            self.log.info("No fishing spot in view - walking back to '%s'", self.fish_spot)
            self.walk_to_spot(self.fish_spot)
            actions.reset_camera(self.ctx)
            return
        if self.empty_rounds >= EMPTY_ROUNDS_BEFORE_GIVING_UP:
            raise StopBot("no fishing spot found after a long search")
        self.state = "waiting for a fishing spot"
        self.sleep(WAIT_FOR_SPOT_S)
        self.scan_camera()

    def scan_camera(self, what="fishing spot"):
        self.log.info("No %s in view - rotating camera", what)
        self.inp.move(260, 300)
        actions.rotate_camera(self.ctx)   # exact quarter turn via the game's camera, else arrow key

    # ---- fishing ---------------------------------------------------------------------
    def fish(self):
        """Wait while we walk over and fish, until idle (spot moved/gone), full, or stalled."""
        occ = prev = inventory.occupied(self.grab())
        before = last = catch_count(occ, self.keep_slots)
        self.activity.reset()
        start = last_catch = time.monotonic()
        started = False
        idle_reads = 0
        while True:
            now = time.monotonic()
            if now - start > FISH_TIMEOUT_S:
                break
            frame = self.grab()
            self.activity.update(frame)
            occ = inventory.occupied(frame)
            n = catch_count(occ, self.keep_slots)
            if n > last:
                self.logs_cut += n - last
                self.log.info("+%d fish - %s", n - last, self.stats())
                if self.learn_catches:
                    self.learn_catch([i for i, o in enumerate(occ)
                                      if o and not prev[i] and i not in self.keep_slots])
                last, last_catch, started = n, now, True
            prev = occ
            if emptied_kept(occ, self.keep_slots):
                break                           # out of bait: the main loop stops us
            if sum(occ) >= self.drop_at:
                break
            busy = self.busy_from_game()
            if busy is None:                    # no game data: judge by the picture moving
                busy, settled = self.activity.active, self.activity.filled
            else:
                idle_reads = 0 if busy else idle_reads + 1
                settled = idle_reads >= GS_IDLE_READS
            if busy:
                started = True
            elif settled and (started or now - start > NEVER_STARTED_S):
                break                           # was fishing and now idle, or never started
            if now - last_catch > NO_CATCH_S:
                self.log.info("No catch for %d s - looking for the spot again", NO_CATCH_S)
                break
            if actions.dismiss_dialog(self.ctx):   # level-up stops fishing
                self.level_up = True
                self.after_dialog()
                break
            self.sleep(0.2)
        self.sleep(SERVER_TICK)
        n = catch_count(inventory.occupied(self.grab()), self.keep_slots)
        if n > last:
            self.logs_cut += n - last
            last = n
        if last > before:
            self.failed_clicks = 0
            self.last_catch_at = time.monotonic()
            return
        self.failed_clicks += 1
        spot = getattr(self, "last_spot", None)
        if spot is not None:                    # (out of reach / across the water): another one next
            if not hasattr(self, "dud_spots"):
                self.dud_spots = {}
            self.dud_spots[spot] = time.monotonic() + DUD_SPOT_S
            self.last_spot = None
        since = time.monotonic() - getattr(self, "last_catch_at", self.started)
        if self.failed_clicks >= MAX_FAILED_CLICKS and since > NO_CATCH_STOP_S:
            raise StopBot(f"{self.failed_clicks} tries without a catch - right tool ({TOOLS[self.method]})"
                          f" and level ({METHOD_LEVELS[self.method]}+)?")

    def after_dialog(self):
        """The game stops fishing with a dialog, not a chat line: "You don't have any feathers
        left." / "You need a fly fishing rod to lure these fish." - get the tools again."""
        from lumberjack.ui import widgets
        said = (widgets.last_dialog or "").lower()
        if "need a fishing level" in said:          # (not "...advanced a Fishing level!")
            raise StopBot(widgets.last_dialog)
        if ("don't have any" in said or "you need a" in said) and self.spawn_tools:
            self.log.info("The game says the %s are missing - getting them again", TOOLS[self.method])
            widgets.last_dialog = ""
            self.ensure_tools()
            self.failed_clicks = 0

    # ---- full inventory --------------------------------------------------------------
    def handle_full_inventory(self):
        if self.when_full == "bank":
            self.state = "banking"
            if not self.bank_trip():
                raise StopBot("couldn't bank - stopped with the fish kept")
        else:
            self.state = "dropping"
            self.log.info("Inventory full - dropping")
            actions.drop_all(self.ctx, keep=self.keep_slots)
            self.log.info("Dropped. %s", self.stats())
        if self.auto and self.check_level():
            self.ensure_tools()

    def _bank_target(self):
        from lumberjack.nav.spots import bank_spots
        spots = dict(bank_spots(self.walker.map))
        if self.bank_spot in spots:
            return self.bank_spot, spots[self.bank_spot]
        return next(iter(spots.items()), (None, None))

    def remember_bank_icon(self):
        from lumberjack.nav.icons import IconWalker
        self.bank_anchor = IconWalker(self.ctx).bank_offset()
        if self.bank_anchor is None:
            raise StopBot("banking without a map needs the bank's $ icon on the minimap from the fishing "
                          "spot (e.g. Draynor) - or pick a map with a bank spot under Route")
        self.log.info("Bank icon on the minimap at (%+.0f, %+.0f) - banking by the minimap", *self.bank_anchor)

    def icon_bank_trip(self):
        """No map: walk to the minimap's bank icon, bank, walk back until the icon sits where
        it was at the fishing spot."""
        from lumberjack import bank
        from lumberjack.nav.icons import IconWalker
        iw = IconWalker(self.ctx)
        self.state = "walking to the bank"
        self.log.info("Inventory full - walking to the bank")
        if not iw.to_bank():
            return False
        self.state = "banking"
        if not bank.open_bank(self.ctx):
            return False
        ok = bank.deposit_all(self.ctx, keep_slots=self.keep_slots)
        bank.close(self.ctx)
        if not ok:
            return False
        self.banked += 1
        self.log.info("Banked (trip %d). %s", self.banked, self.stats())
        self.state = "walking back"
        if not iw.back_to(self.bank_anchor):
            self.log.warning("Couldn't get all the way back to the fishing spot")
        self.spot_action = None
        actions.reset_camera(self.ctx)
        return True

    def bank_trip(self):
        """Walk to the bank, deposit everything but the kept slots, walk back."""
        from lumberjack import bank
        if not self.walker:
            if self.gs:                    # no map: the nearest booth from the game's data
                self.state = "banking"
                if not bank.gs_bank_trip(self.ctx, keep_slots=self.keep_slots):
                    return False
                self.banked += 1
                self.log.info("Banked (trip %d). %s", self.banked, self.stats())
                self.spot_action = None
                actions.reset_camera(self.ctx)
                return True
            if self.bank_anchor is not None:
                return self.icon_bank_trip()
            self.log.warning("Banking needs a map with a bank spot, or the bank icon on the minimap")
            return False
        name, spot = self._bank_target()
        if not spot:
            self.log.warning("No bank spot saved on map '%s'", self.walker.map.name)
            return False
        here = self.walker.where()
        self.log.info("Inventory full - walking to '%s'", name)
        self.state = f"walking to {name}"
        if not self.walker.walk_to(spot["x"], spot["y"]):
            return False
        self.state = "banking"
        if not bank.open_bank(self.ctx):
            return False
        ok = bank.deposit_all(self.ctx, keep_slots=self.keep_slots)
        bank.close(self.ctx)
        if not ok:
            return False
        self.banked += 1
        self.log.info("Banked (trip %d). %s", self.banked, self.stats())
        target = self.spot_xy(self.fish_spot) or ((here.x, here.y) if here else None)
        if target:
            self.state = "walking back"
            self.walker.walk_to(*target)
        actions.reset_camera(self.ctx)
        return True


def learn_tool(ctx, slot, tool, stack=False):
    """Remember a tool just spawned into `slot`: its backpack icon (not for stacks - the
    count drawn on them changes) and its orange hover name, so it's found next time."""
    import logging
    from pathlib import Path

    import cv2
    log = logging.getLogger("fishing")
    frame = ctx.grab()
    icons = Path(actions.__file__).resolve().parent / "assets" / "templates" / "items"
    if not stack and not (icons / f"{tool}.png").exists():
        icons.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(icons / f"{tool}.png"), R.INV_SLOTS[slot].crop(frame))
        actions._ICONS = None
        log.info("Saved the %s icon", tool.replace("_", " "))
    if mouseover.available(tool):
        return
    ctx.inp.move(*R.INV_SLOTS[slot].center, steps=2)
    ctx.sleep(0.3)
    strip = R.MOUSEOVER_TEXT.crop(ctx.grab())
    ctx.inp.move(260, 300)
    if mouseover.action_ok(strip, "use") and mouseover.strip_mask(strip, "orange").any():
        from lumberjack.tools.calibrate_name import save_name
        save_name(strip, tool, "orange")
        log.info("Learned the name '%s'", tool.replace("_", " "))


# ---- calibration helpers (call from the control panel's manual runner) --------------------
def learn_hover(ctx, action):
    """Hover fishing-spot candidates; save the white action word as mouseover/<action>.png
    and the yellow name as mouseover/fishing_spot.png. Stand right next to a spot whose
    left-click option is `action` (e.g. 'net' at Draynor). Returns True on success."""
    import logging
    log = logging.getLogger("calibrate")
    seen = {}
    cands = fishing_spots.find_fishing_spots(ctx.grab())[:8]
    for c in cands:
        for dx, dy in HOVER_OFFSETS:
            ctx.inp.move(c.x + dx, c.y + dy)
            ctx.sleep(0.25)
            frame = ctx.grab()
            if mouseover.mask(frame, TARGET_COLOR).sum() // 255 < 20:
                k = hover_kind(R.MOUSEOVER_TEXT.crop(frame))
                seen[k] = seen.get(k, 0) + 1
                continue    # no yellow (NPC) name showing
            if mouseover.available(TARGET):
                # learning another action word: the yellow name must be the known target
                rec, prec = mouseover.word_score(frame, TARGET, TARGET_COLOR)
                if rec < TARGET_MIN_RECALL or prec < TARGET_MIN_PRECISION:
                    continue
                shape = mouseover.save_word_templates(frame, action, "_tmp_target", TARGET_COLOR)
                _discard("_tmp_target")
            else:
                shape = mouseover.save_word_templates(frame, action, TARGET, TARGET_COLOR)
            log.info("Learned '%s' + '%s' from hover at (%d, %d) %s", action, TARGET, c.x + dx, c.y + dy, shape)
            ctx.inp.move(260, 300)
            return True
    log.warning("No fishing spot hover found (%d ripple(s) tried; hover showed %s) - stand next to a spot "
                "(ripples in view) and retry", len(cands),
                ", ".join(f"{n}x {k}" for k, n in seen.items()) or "-")
    return False


def learn_menu(ctx, options):
    """Right-click a verified fishing spot and save its rows as menu templates, in order:
    e.g. learn_menu(ctx, ['net', 'bait']) at Draynor, ['lure', 'bait'] at a river spot,
    ['cage', 'harpoon'] / ['net', 'harpoon'] at the sea. Needs the hover templates."""
    import logging
    log = logging.getLogger("calibrate")
    for c in fishing_spots.find_fishing_spots(ctx.grab())[:8]:
        for dx, dy in HOVER_OFFSETS:
            x, y = c.x + dx, c.y + dy
            ctx.inp.move(x, y)
            ctx.sleep(0.25)
            if not hover_action(ctx.grab()):
                continue
            ctx.inp.right_click(x, y)
            ctx.sleep(0.35)
            frame = ctx.grab()
            m = menu.find(frame, (x, y))
            if not m:
                continue
            rows = menu.rows(m)
            for name, row in zip(options, rows):
                menu.save_action_template(frame, row, name)
            ctx.inp.move(x, max(R.VIEWPORT.y + 30, y - 90))
            log.info("Saved menu options %s", ", ".join(options[:len(rows)]))
            return True
    log.warning("Couldn't open a fishing spot's menu - calibrate the hover text first")
    return False


TARGET_MIN_RECALL, TARGET_MIN_PRECISION = 0.93, 0.90   # same bar as mouseover._ok


def _discard(name):
    p = mouseover.TEMPLATES / f"{name}.png"
    if p.exists():
        p.unlink()
    mouseover._cache.pop(name, None)
    mouseover._variants_cache.pop(name, None)
