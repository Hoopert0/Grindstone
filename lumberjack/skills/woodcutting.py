"""Woodcutting v1: power-chop normal trees and drop the logs.

    FIND_TREE --(no tree)--> SCAN_CAMERA --(4 tries)--> give up
        |  hover says "Chop down Tree"
        v
      CHOP  --(idle again / timeout)--> FIND_TREE
        |  inventory full
        v
    DROP_LOGS --> FIND_TREE
"""
import json
import logging
import random
import time

import win32api

from lumberjack import actions
from lumberjack.core.input import AgentInput
from lumberjack.core.window import GameWindow
from lumberjack.ui import inventory, mouseover
from lumberjack.vision import trees
from lumberjack.vision.activity import ActivityMonitor

log = logging.getLogger("woodcutting")

STOP_KEY = 0x7B  # F12, checked globally - press it any time to stop the bot
SERVER_TICK = 0.6

TREE_LEVELS = {"tree": 1, "oak": 15, "willow": 30, "maple": 45, "yew": 60, "magic": 75}
TREE_LOGS = {"tree": "logs", "oak": "oak_logs", "willow": "willow_logs", "maple": "maple_logs",
             "yew": "yew_logs", "magic": "magic_logs"}
# Firemaking level needed to light each tree's logs
BURN_LEVELS = {"tree": 1, "oak": 15, "willow": 30, "maple": 45, "yew": 60, "magic": 75}
# normal trees fall after one log; the rest give several, so allow a longer chop
CHOP_TIMEOUT = {"tree": 45}
DEFAULT_CHOP_TIMEOUT = 180
BLOCK_AFTER_FAILS = 3       # clicks in a row on a type that yield nothing (level too low?) ...
BLOCK_FOR_S = 600           # ... before we ignore that type for a while
# tree names in the game -> the tree types the panel offers
TREE_BY_NAME = {"tree": "tree", "dead tree": "tree", "evergreen": "tree", "oak": "oak", "willow": "willow",
                "maple tree": "maple", "yew": "yew", "magic tree": "magic"}
GS_RADIUS = 18              # tiles searched for trees in the game's scene data
GS_IDLE_READS = 7           # ~1 s of "not animating, not walking" = stopped chopping
RESPAWN_PATIENCE_S = 240    # every tree here felled (other players too) this long -> stop
TAKEN_CHECK_S = 0.6         # how often a walk/chop checks the tree is still standing
MAX_KEPT = 4                # "keep what I'm carrying" refuses a backpack with more than this
EMPTY_SCANS_BEFORE_MOVING = 6


class StopBot(Exception):
    pass


class Woodcutter:
    def __init__(self, max_minutes=None, trees_allowed=("tree",), drop_at=28, max_logs=None,
                 stop_event=None, pause_event=None, map_name=None, spot_names=None,
                 start_mode="here", start_spot=None, chop_spot=None,
                 when_full="drop", bank_spot=None, keep_carried=True, auto_trees=False,
                 spawn_tools=True, clear_at_start=False):
        self.win = GameWindow()
        self.inp = AgentInput()
        self.ctx = actions.Ctx(self.win, self.inp, self.sleep, grabber=self.grab)
        self.activity = ActivityMonitor()
        self.start_mode = start_mode   # "here" | "teleport" | "spot" (we're standing at start_spot)
        self.start_spot = start_spot
        self.chop_spot = chop_spot     # walk here before chopping
        self.when_full = when_full     # "drop" | "bank" | "burn"
        self.bank_spot = bank_spot     # None = the map's first bank spot
        self.keep_slots = set()        # inventory slots never dropped/deposited (axe, tinderbox)
        self.keep_carried = keep_carried  # keep whatever is in the backpack at Start
        self.clear_at_start = clear_at_start   # drop logs/ore/fish/loot first (tools stay)
        self.auto_trees = auto_trees      # pick trees (and spot) from the Stats-tab levels
        self.spawn_tools = spawn_tools    # ::item a missing axe/knife/tinderbox (singleplayer admin)
        self.no_start_streak = 0          # chop clicks in a row where nothing happened
        self.axe_spawned = False
        self.levels = {}
        self.empty_scans = 0
        self.tinder_slot = None
        self.banked = 0
        self.burned = 0
        self.fletched = 0
        self.knife_slot = None
        # navigation (optional): walk between saved tree spots when the trees run out
        self.walker = None
        self.spot_names = spot_names   # None = every spot on the map that lists our trees
        self.spot_turn = 0
        if map_name:
            from lumberjack.nav.walker import Walker
            from lumberjack.nav.worldmap import WorldMap
            self.walker = Walker(self.win, self.inp, WorldMap.load(map_name), sleep=self.sleep)
        if isinstance(trees_allowed, str):
            trees_allowed = [trees_allowed]
        # best (highest level) first: with oak+tree allowed, an oak in view wins
        self.trees = sorted(trees_allowed, key=lambda t: -TREE_LEVELS.get(t, 0))
        self.blocked = {}      # tree type -> time it's allowed again (e.g. level too low)
        self.gs = None         # the game's own data (core.gamestate), set in run() when readable
        self.fail_streak = {}  # tree type -> consecutive clicks that gave no logs
        self.drop_at = drop_at
        self.max_logs = max_logs
        self.deadline = time.monotonic() + max_minutes * 60 if max_minutes else None
        self.stop_event = stop_event      # threading.Event set by the control panel
        self.pause_event = pause_event    # while set, the bot idles
        self.logs_cut = 0
        self.started = time.monotonic()
        self.state = "starting"
        self.stop_reason = None   # shown in the panel when the run ends
        from lumberjack.xp import XpTracker
        self.xp = XpTracker()
        self.bad_spots = []  # (x, y, expiry) screen spots whose hover text wasn't a tree
        self.last_tree = None  # the loc we clicked last (game data)
        self.dry_since = None  # since when no tree here was standing (respawn wait)
        self.seen = None       # what the last game-data look found
        self.taken = 0         # trees felled by someone else before we got a log

    # ---- helpers ---------------------------------------------------------------------
    def check_stop(self):
        if win32api.GetAsyncKeyState(STOP_KEY) & 0x8000:
            raise StopBot("F12 pressed")
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopBot("stopped from control panel")
        if self.deadline and time.monotonic() > self.deadline:
            raise StopBot("time limit reached")
        if self.max_logs and self.logs_cut >= self.max_logs:
            raise StopBot(f"reached {self.max_logs} logs")
        from lumberjack.skills.watch import plan_checks
        plan_checks(self, StopBot)      # plan conditions: target level, stall guard, logged out
        if self.pause_event is not None and self.pause_event.is_set():
            before, self.state = self.state, "paused"
            from lumberjack.skills.watch import keep_awake
            while self.pause_event.is_set():
                if self.stop_event is not None and self.stop_event.is_set():
                    raise StopBot("stopped from control panel")
                keep_awake(self)             # paused for long: still logged in when resumed
                time.sleep(0.1)
            self.state = before

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check_stop()
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))

    def grab(self):
        from lumberjack.core.window import GameMinimized
        self.check_stop()
        warned = False
        while True:
            try:
                return self.win.grab()
            except GameMinimized:
                if not warned:   # wait it out instead of crashing (and keep F12 / Stop working)
                    log.warning("The game window is minimized - waiting until it's back")
                    warned = True
                before, self.state = self.state, "game minimized"
                time.sleep(1.0)
                self.check_stop()
                self.state = before

    def stats(self):
        hrs = (time.monotonic() - self.started) / 3600
        return f"{self.logs_cut} logs ({self.logs_cut / hrs:.0f}/hr)" if hrs > 0 else ""

    # ---- states ----------------------------------------------------------------------
    def run(self):
        log.info("Woodcutter started - press F12 to stop")
        try:
            # moving off any open right-click menu closes it (it could hide the inventory)
            self.inp.move(260, 300)
            self.sleep(0.3)
            from lumberjack import bank
            if bank.is_open(self.grab()):
                bank.close(self.ctx)
            if self.clear_at_start:
                self.state = "dropping materials"
                actions.clear_materials(self.ctx, food=True, log=log)
            if self.keep_carried:
                carried = actions.carried_keepers(self.ctx)   # logs never count as keepers
                from lumberjack.core import gamestate
                if len(carried) > MAX_KEPT and gamestate.shared() is None:   # (by name: any number is fine)
                    raise StopBot(f"you're carrying {len(carried)} non-log items - bank everything except "
                                  f"what the bot should keep (axe, tinderbox), or untick 'Keep what I'm carrying'")
                self.keep_slots = carried
                if carried:
                    log.info("Keeping the %d item(s) you're carrying (slots %s)",
                             len(carried), ", ".join(str(s + 1) for s in sorted(carried)))
            # standard camera: tilted top-down (what the tree finder is tuned on). The
            # compass can't be clicked in this client; navigation reads the needle instead.
            self.state = "setting camera"
            actions.reset_camera(self.ctx)
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs:
                log.info("Reading the game's own data: trees by name, chopping from the animation")
            if self.walker:
                from lumberjack.nav.learner import MapLearner
                self.learner = MapLearner(self.walker, self.win)
                self.learner.start()
            if self.auto_trees:
                self.pick_trees(walk=False)   # go_to_start walks to the chosen spot
            else:
                self.track_xp()               # starting XP for the panel's counters
            self.go_to_start()
            if self.gs and self.spawn_tools:     # an axe up front, not after four failed clicks
                from lumberjack import items
                from lumberjack.core import gamestate
                lv = (gamestate.skill("woodcutting") or {}).get("base", 1)
                slot = items.ensure_best(self.ctx, items.AXE_ORDER, items.best_axe(lv), log=log, gs=self.gs)
                if slot is not None and slot >= 0:
                    self.keep_slots = set(self.keep_slots) | {slot}
                    self.axe_spawned = True
            if self.when_full == "burn":
                self.ensure_tinderbox()
            if self.when_full == "fletch":
                self.ensure_knife()
            while True:
                actions.dismiss_dialog(self.ctx)
                frame = self.grab()
                if inventory.count(frame) >= self.drop_at:
                    self.handle_full_inventory()
                    continue
                self.state = "finding tree"
                if not self.active_trees():
                    log.warning("None of the selected trees can be chopped right now (%s) - waiting",
                                ", ".join(self.trees))
                    self.state = "waiting"
                    self.sleep(30)
                    continue
                self.seen = None
                kind = self.find_and_click_tree()
                if not kind and self.gs and self.seen is not None:
                    if not self.seen["trees"]:   # none standing: felled - they grow back
                        self.wait_for_respawn()
                        continue
                    log.info("Couldn't click any of the %d tree(s) - turning the camera", self.seen["trees"])
                self.dry_since = None
                if not kind:
                    self.empty_scans += 1
                    self.state = "scanning"
                    # sweeps that only find unwanted trees (e.g. oaks when we want trees) still
                    # "see" something - so after a few fruitless rounds, move on regardless
                    if self.empty_scans < EMPTY_SCANS_BEFORE_MOVING and self.scan_camera(tries=3 if self.walker else 4):
                        continue
                    if self.walker and self.walk_to_next_spot():
                        self.empty_scans = 0
                        continue
                    if self.empty_scans < EMPTY_SCANS_BEFORE_MOVING * 3:
                        continue
                    self.stop_reason = ("couldn't find any " + "/".join(self.trees) + " trees here or at the "
                                        "saved spots - pick a map with tree spots, or stand near the trees")
                    log.warning("Stopping: %s", self.stop_reason)
                    return
                self.empty_scans = 0
                self.state = f"chopping {kind}"
                self.chop(kind)
        except StopBot as e:
            self.stop_reason = str(e)
            log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            if getattr(self, "learner", None):
                self.learner.stop()   # saves what it learned
            self.inp.close()  # free the input agent for other tools

    def go_to_start(self):
        """Apply the start option, then walk to the chopping spot if one is set."""
        if self.start_mode == "teleport":
            self.state = "teleporting"
            if not actions.home_teleport(self.ctx):
                raise StopBot("Home Teleport failed")
            actions.reset_camera(self.ctx)
        if not self.walker:
            return
        spots = self.walker.map.spots
        if self.start_mode == "spot" and self.start_spot in spots:
            # "I'm standing at X": a hint so the first position fix searches there
            from lumberjack.nav.localizer import Fix
            s = spots[self.start_spot]
            self.walker.loc.last = Fix(s["x"], s["y"], 0.0, 1.0, local=True)
            log.info("Starting at '%s'", self.start_spot)
        if self.chop_spot and self.chop_spot in spots:
            s = spots[self.chop_spot]
            self.state = f"walking to {self.chop_spot}"
            log.info("Walking to '%s'", self.chop_spot)
            if not self.walker.walk_to(s["x"], s["y"]):
                raise StopBot(f"couldn't reach '{self.chop_spot}'")

    def _hover_is_tree(self, x, y):
        """Hover (x, y); return which allowed tree type the hover text names, or None."""
        self.inp.move(x, y)
        self.sleep(random.uniform(0.12, 0.2))
        return mouseover.which(self.grab(), "chop_down", self.active_trees())

    def active_trees(self):
        now = time.monotonic()
        for t, until in list(self.blocked.items()):
            if until <= now:
                del self.blocked[t]
                self.fail_streak.pop(t, None)
                log.info("Trying %s again", t)
        return [t for t in self.trees if t not in self.blocked and (self.gs or mouseover.available(t))]

    # ---- trees from the game's scene data ----------------------------------------------
    def _gs_call(self, fn, *a):
        from lumberjack.core.gamestate import tolerant_call
        return tolerant_call(self, fn, *a, logger=log)

    def wanted_trees_gs(self):
        """[(type, loc)] choppable trees we may cut, best type first, then nearest."""
        active = self.active_trees()
        out = []
        for loc in self.gs.locs(GS_RADIUS):
            kind = TREE_BY_NAME.get(loc["name"].lower())
            if kind in active and "Chop down" in loc["ops"]:
                out.append((kind, loc))
        out.sort(key=lambda kl: (active.index(kl[0]), kl[1]["dist"]))
        return out

    def find_and_click_tree_gs(self, walked=False):
        from lumberjack.core import interact
        trees_ = self.wanted_trees_gs()
        visible = [(k, l) for k, l in trees_ if interact.on_screen(*l["screen"]) or interact.on_screen(*l["body"])]
        if not walked:
            self.seen = {"trees": len(trees_), "visible": len(visible)}
        for kind, loc in visible[:4]:
            if interact.use_option(self.ctx, self.gs, interact.points_for(loc), "Chop down", loc["name"]):
                log.info("Chopping a %s %d tile(s) away", loc["name"].lower(), loc["dist"])
                self.last_tree = loc
                return kind
        if trees_ and not visible and not walked:
            kind, loc = trees_[0]
            self.state = f"walking to a {kind}"
            log.info("Nearest %s is %d tiles away - walking toward it", loc["name"].lower(), loc["dist"])
            interact.walk_toward(self.ctx, self.gs.player()["tile"], loc["tile"])
            self.wait_until_still_gs()
            return self.find_and_click_tree_gs(walked=True)
        return None

    def wait_until_still_gs(self, timeout=12.0):
        end = time.monotonic() + timeout
        self.sleep(0.6)
        while time.monotonic() < end and self.gs.player().get("moving"):
            self.sleep(0.2)

    def tree_gone(self, loc):
        """True when `loc` (a tree we clicked) no longer stands there: felled (a stump)."""
        here = [l for l in self.gs.locs(GS_RADIUS) if l["tile"] == loc["tile"]]
        return bool(here) and not any(l["id"] == loc["id"] and "Chop down" in (l.get("ops") or [])
                                      for l in here)

    def wait_for_respawn(self):
        """No tree we want stands within reach (felled - other players too): wait for them to
        grow back - up to RESPAWN_PATIENCE_S, then stop so the plan can move on."""
        now = time.monotonic()
        if self.dry_since is None:
            self.dry_since = now
            log.info("No %s tree standing in reach (felled - other players too?) - waiting for them to grow back",
                     "/".join(self.active_trees()) or "")
        elif now - self.dry_since > RESPAWN_PATIENCE_S:
            raise StopBot(f"no {'/'.join(self.trees)} tree standing here for {RESPAWN_PATIENCE_S // 60} min")
        self.state = "waiting for trees to grow back"
        self.sleep(random.uniform(1.2, 2.4))

    def busy_from_game(self):
        """True while we're animating (chopping) or walking; None without game data."""
        if not self.gs:
            return None
        pl = self._gs_call(self.gs.player)
        return None if pl is None else (pl.get("anim", -1) != -1 or bool(pl.get("moving")))

    def find_and_click_tree(self):
        """Hover candidates; click the best allowed tree type in view. Returns its type or None."""
        if self.gs:
            kind = self._gs_call(self.find_and_click_tree_gs)
            if self.gs:
                return kind
        frame = self.grab()
        now = time.monotonic()
        self.bad_spots = [b for b in self.bad_spots if b[2] > now]
        active = self.active_trees()
        if not active:
            return None
        best = None  # (rank, x, y, type, dist) - fallback if the top type isn't in view
        for cand in trees.find_trees(frame)[:6]:
            if any(abs(cand.x - bx) < 20 and abs(cand.y - by) < 20 for bx, by, _ in self.bad_spots):
                continue
            bx, by, bw, bh = cand.bbox
            # centroid first, then a couple of other points on the canopy
            for px, py in [(cand.x, cand.y), (bx + bw // 2, by + bh // 3), (bx + bw // 3, by + bh // 2)]:
                px += random.randint(-3, 3)
                py += random.randint(-3, 3)
                kind = self._hover_is_tree(px, py)
                if not kind:
                    continue
                rank = active.index(kind)
                if rank == 0:  # the best type we're allowed - take it
                    self.inp.click()
                    log.info("Clicked %s at (%d, %d), %.0f px away", kind, px, py, cand.dist)
                    return kind
                if best is None or rank < best[0]:
                    best = (rank, px, py, kind, cand.dist)
                break
            else:
                self.bad_spots.append((cand.x, cand.y, now + 10))
        if best:
            _, px, py, kind, dist = best
            if self._hover_is_tree(px, py) == kind:  # re-verify: we've moved the mouse since
                self.inp.click()
                log.info("Clicked %s at (%d, %d), %.0f px away", kind, px, py, dist)
                return kind
        return None

    def chop(self, kind="tree"):
        """Wait while we walk to the tree and chop it, until we go idle."""
        timeout = CHOP_TIMEOUT.get(kind, DEFAULT_CHOP_TIMEOUT)
        before = inventory.count(self.grab())
        self.activity.reset()
        start = time.monotonic()
        started_moving = False
        idle_reads = 0
        next_look = start + TAKEN_CHECK_S
        felled = False
        while time.monotonic() - start < timeout:
            frame = self.grab()
            if self.gs and self.last_tree and time.monotonic() >= next_look:
                next_look = time.monotonic() + TAKEN_CHECK_S
                if self._gs_call(self.tree_gone, self.last_tree):
                    felled = True                # it fell (to us or someone else): next tree
                    break
            self.activity.update(frame)
            busy = self.busy_from_game()
            if busy is None:                    # no game data: judge by the picture moving
                busy, settled = self.activity.active, self.activity.filled
            else:
                idle_reads = 0 if busy else idle_reads + 1
                settled = idle_reads >= GS_IDLE_READS
            if busy:
                started_moving = True
            elif settled and (started_moving or time.monotonic() - start > 4):
                break  # was busy and now idle (tree fell), or never started
            if inventory.count(frame) >= self.drop_at:
                break
            self.sleep(0.15)
        self.sleep(SERVER_TICK)  # let the last log land in the inventory
        got = inventory.count(self.grab()) - before
        if got > 0:
            self.logs_cut += got
            self.fail_streak[kind] = 0
            self.no_start_streak = 0
            log.info("+%d %s log(s) - %s", got, kind, self.stats())
            return
        if felled:
            self.taken += 1
            log.info("That tree fell before we got a log (another player?) - next one")
            return
        if not started_moving:
            log.info("Nothing happened after clicking - retrying")
            self.no_start_streak += 1
            if self.no_start_streak >= 4:
                self.fix_missing_axe()
        # no logs: maybe a just-felled tree, maybe our level is too low ("You need a
        # Woodcutting level of 15...") - in which case we may not even walk over
        self.fail_streak[kind] = self.fail_streak.get(kind, 0) + 1
        if self.fail_streak[kind] >= BLOCK_AFTER_FAILS and len(self.trees) > 1:
            self.blocked[kind] = time.monotonic() + BLOCK_FOR_S
            log.warning("No %s logs after %d tries - level too low? Skipping %s for %d min",
                        kind, self.fail_streak[kind], kind, BLOCK_FOR_S // 60)

    def ensure_tinderbox(self):
        """Burn mode: make sure a tinderbox is in the backpack and remember its slot."""
        from lumberjack.skills import firemaking
        slot = firemaking.find_tinderbox(self.ctx)
        if slot is None:
            slot = self.spawn_tool("tinderbox", firemaking.find_tinderbox)
        if slot is None:
            bank_xy = return_xy = None
            if self.walker:
                _, spot = self._bank_target()
                here = self.walker.where()
                bank_xy = (spot["x"], spot["y"]) if spot else None
                return_xy = (here.x, here.y) if here else None
            self.state = "fetching tinderbox"
            slot = firemaking.fetch_tinderbox(self.ctx, self.walker, bank_xy, return_xy,
                                              keep_slots=self.keep_slots)
            actions.reset_camera(self.ctx)
        if slot is None:
            raise StopBot("no tinderbox (and couldn't get one from the bank)")
        self.keep_slots = set(self.keep_slots) | {slot}
        self.tinder_slot = slot
        log.info("Tinderbox in slot %d", slot)

    def burn_load(self):
        from lumberjack.skills import firemaking
        self.state = "firemaking"
        log.info("Inventory full - lighting fires")
        burned, gave_up = firemaking.burn_all(self.ctx, self.tinder_slot, self.keep_slots)
        self.burned += burned
        log.info("Burned %d logs (%d total)", burned, self.burned)
        actions.reset_camera(self.ctx)
        if gave_up:
            return False
        return True

    def pick_trees(self, walk=True):
        """Auto mode: read levels, choose the best tree we can cut (and, when burning, light),
        and a saved spot that has it. Re-run after every load so the bot moves up as it levels."""
        from lumberjack.ui import stats
        skills = ["woodcutting"] + (["firemaking"] if self.when_full == "burn" else []) \
            + (["fletching"] if self.when_full == "fletch" else [])
        self.state = "checking levels"
        levels = {}
        for s in skills:
            info = stats.read_skill(self.ctx, s) or stats.read_skill(self.ctx, s)   # one retry
            if info:
                levels[s] = info["level"]
                self.xp.update(s, info)
            elif s != "woodcutting":
                # never skip a limit just because the read failed: last known level, else 1
                levels[s] = self.levels.get(s, 1)
                log.warning("Couldn't read %s level - assuming %d", s, levels[s])
        if "woodcutting" not in levels:
            log.warning("Couldn't read levels from the Stats tab - keeping the current trees")
            return
        self.levels = levels
        ok = [t for t in TREE_LEVELS if TREE_LEVELS[t] <= levels["woodcutting"] and mouseover.available(t)]
        if "firemaking" in levels:
            ok = [t for t in ok if BURN_LEVELS[t] <= levels["firemaking"]]
        if "fletching" in levels:
            from lumberjack.skills.fletching import best_product
            ok = [t for t in ok if best_product(TREE_LOGS[t], levels["fletching"])]
        spots = self.walker.map.spots if self.walker else {}
        have = {t for s in spots.values() if s.get("kind", "trees") == "trees" for t in s.get("trees", [])}
        if spots:
            ok = [t for t in ok if t in have] or ok
        if not ok:
            return
        best = max(ok, key=lambda t: TREE_LEVELS[t])
        if self.trees != [best]:
            log.info("Levels: %s - chopping %s", ", ".join(f"{k} {v}" for k, v in levels.items()), best)
        self.trees = [best]
        self.blocked.pop(best, None)
        if spots:
            fits = [n for n, s in spots.items() if s.get("kind", "trees") == "trees" and best in s.get("trees", [])]
            if fits:
                self.spot_names = None   # any saved spot listing the chosen tree (incl. new ones)
                if self.chop_spot not in fits:
                    self.chop_spot = fits[0]
                    if self.walker and walk:
                        self.walk_to_spot_named(self.chop_spot)

    def walk_to_spot_named(self, name):
        s = self.walker.map.spots.get(name)
        if s:
            self.state = f"walking to {name}"
            log.info("Walking to '%s'", name)
            self.walker.walk_to(s["x"], s["y"])
            actions.reset_camera(self.ctx)

    def spawn_tool(self, key, find):
        """Self-correct by spawning a tool with ::item. Returns its slot or None."""
        from lumberjack import items
        if not self.spawn_tools:
            return None
        self.state = f"spawning {key.replace('_', ' ')}"
        log.info("No %s - spawning one", key.replace("_", " "))
        items.spawn(self.ctx, key)
        return find(self.ctx)

    def fix_missing_axe(self):
        """Clicks on trees keep doing nothing - most likely "You do not have an axe to use".
        Spawn the best axe for our Woodcutting level once (it works from the backpack)."""
        from lumberjack import items
        self.no_start_streak = 0
        if self.gs and self.spawn_tools:            # the game says what we carry: no guessing
            from lumberjack.core import gamestate
            lv = (gamestate.skill("woodcutting") or {}).get("base", 1)
            slot = items.ensure_best(self.ctx, items.AXE_ORDER, items.best_axe(lv), log=log, gs=self.gs)
            if slot is None:
                raise StopBot("no axe, and spawning one didn't work (backpack full?)")
            if slot >= 0:
                self.keep_slots = set(self.keep_slots) | {slot}
            return
        if self.axe_spawned or not self.spawn_tools:
            raise StopBot("chopping keeps failing - do you have an axe? (wield one or carry it, "
                          "or turn on 'Spawn missing tools')")
        before = {i for i, o in enumerate(inventory.occupied(self.grab())) if o}
        key = items.best_axe(self.levels.get("woodcutting", 1))
        log.warning("Chopping keeps failing - assuming no axe and spawning a %s", key.replace("_", " "))
        items.spawn(self.ctx, key)
        self.axe_spawned = True
        new = {i for i, o in enumerate(inventory.occupied(self.grab())) if o} - before
        self.keep_slots = set(self.keep_slots) | new   # never drop the new axe
        if not new:
            raise StopBot("tried to spawn an axe but nothing appeared - is the backpack full?")

    def ensure_knife(self):
        from lumberjack.skills import firemaking, fletching
        slot = fletching.find_knife(self.ctx)
        if slot is None:
            slot = self.spawn_tool("knife", fletching.find_knife)
        if slot is None and self.walker and self._bank_target()[1]:
            # self-correct: grab one from the bank
            _, spot = self._bank_target()
            here = self.walker.where()
            self.state = "fetching knife"
            slot = firemaking.fetch_tool(self.ctx, "knife", fletching.find_knife, self.walker,
                                         (spot["x"], spot["y"]), (here.x, here.y) if here else None,
                                         keep_slots=self.keep_slots)
            actions.reset_camera(self.ctx)
        if slot is None:
            raise StopBot("Fletch needs a knife - put one in your backpack or your bank "
                          "(spawn one with ::item 946)")
        self.keep_slots = set(self.keep_slots) | {slot}
        self.knife_slot = slot

    def fletch_load(self):
        """Fletch every log into the best product for our level; keep arrow shafts (they
        stack), drop bows and anything that couldn't be fletched."""
        from lumberjack.ui import stats
        self.state = "fletching"
        lv = stats.read_level(self.ctx, "fletching")
        level = lv[0] if lv else self.levels.get("fletching", 1)
        self.levels["fletching"] = level
        made = 0
        for _pass in range(2):   # a second pass picks up logs a level-up interrupted or a misread skipped
            made += self._fletch_pass(level)   # self.fletched is counted live per batch
        log.info("Fletched %d logs (%d total)", made, self.fletched)
        actions.drop_all(self.ctx, keep=self.keep_slots)   # bows + leftovers

    def _count_fletched(self, n):
        self.fletched += n

    def _fletch_pass(self, level):
        from lumberjack.skills import fletching
        made = 0
        for log_type in fletching.find_logs(self.ctx, self.keep_slots):
            product = fletching.best_product(log_type, level)
            if not product:
                log.info("Fletching %d is too low for %s", level, log_type.replace("_", " "))
                continue
            log.info("Fletching %s into %s", log_type.replace("_", " "), product.replace("_", " "))
            n = fletching.fletch_type(self.ctx, self.knife_slot, log_type, product, self.keep_slots,
                                      on_progress=self._count_fletched)
            made += n
            if product in fletching.STACKS and n:
                # the stack sits in some slot that isn't a log - keep it
                logs_left = {s for v in fletching.find_logs(self.ctx, self.keep_slots).values() for s in v}
                for s in fletching.log_slots(self.ctx, self.keep_slots):
                    if s not in logs_left:
                        self.keep_slots = set(self.keep_slots) | {s}
        return made

    def xp_skills(self):
        return ["woodcutting"] + {"burn": ["firemaking"], "fletch": ["fletching"]}.get(self.when_full, [])

    def track_xp(self):
        """Refresh the panel's XP numbers (auto mode does this while picking trees)."""
        from lumberjack.ui import stats
        self.state = "checking xp"
        for s in self.xp_skills():
            info = stats.read_skill(self.ctx, s)
            if info:
                self.xp.update(s, info)
                self.levels[s] = info["level"]

    def handle_full_inventory(self):
        self._handle_full_inventory()
        if self.auto_trees:
            self.pick_trees()
        else:
            self.track_xp()

    def _handle_full_inventory(self):
        if self.when_full == "fletch":
            self.fletch_load()
            return
        if self.when_full == "burn":
            if self.burn_load():
                return
            if self.walker and self._bank_target()[1]:
                log.warning("Banking the logs that wouldn't light")
                if self.bank_trip():
                    return
            self.state = "dropping"
            self.drop_logs()
            return
        if self.when_full == "bank":
            self.state = "banking"
            if self.bank_trip():
                return
            # usually a navigation hiccup: shuffle a few tiles and try once more
            log.warning("Banking failed - moving a little and retrying")
            from lumberjack.skills.firemaking import _step_aside
            _step_aside(self.ctx)
            if self.bank_trip():
                return
            raise StopBot("couldn't get to the bank - stopped with the logs kept")
        self.state = "dropping"
        self.drop_logs()

    def drop_logs(self):
        log.info("Inventory full - dropping")
        actions.drop_all(self.ctx, keep=self.keep_slots)
        log.info("Dropped. %s", self.stats())

    def _bank_target(self):
        from lumberjack.nav.spots import bank_spots
        spots = dict(bank_spots(self.walker.map))
        if self.bank_spot in spots:
            return self.bank_spot, spots[self.bank_spot]
        return next(iter(spots.items()), (None, None))

    def bank_trip(self):
        """Walk to the bank, deposit everything (except kept slots), walk back."""
        from lumberjack import bank
        if not self.walker:
            if self.gs:                    # no map: the nearest booth from the game's data
                self.state = "banking"
                if not bank.gs_bank_trip(self.ctx, keep_slots=self.keep_slots):
                    return False
                self.banked += 1
                log.info("Banked (trip %d). %s", self.banked, self.stats())
                actions.reset_camera(self.ctx)
                return True
            log.warning("Banking needs a map with a bank spot")
            return False
        name, spot = self._bank_target()
        if not spot:
            log.warning("No bank spot saved on map '%s'", self.walker.map.name)
            return False
        here = self.walker.where()
        log.info("Inventory full - walking to '%s'", name)
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
        log.info("Banked (trip %d). %s", self.banked, self.stats())
        # back to the trees: the configured chop spot, else where we were chopping
        target = None
        if self.chop_spot and self.chop_spot in self.walker.map.spots:
            s = self.walker.map.spots[self.chop_spot]
            target = (s["x"], s["y"])
        elif here:
            target = (here.x, here.y)
        if target:
            self.state = "walking back"
            self.walker.walk_to(*target)
        actions.reset_camera(self.ctx)
        return True

    def walk_to_next_spot(self):
        """Walk to the next saved spot (round-robin) that has the trees we want."""
        import math
        from lumberjack.nav.spots import spots_for
        from lumberjack.nav.worldmap import MAPS
        wm = self.walker.map
        try:  # pick up spots saved from the control panel while we were running
            wm.spots = json.loads((MAPS / f"{wm.name}.json").read_text()).get("spots", {})
        except (OSError, ValueError):
            pass
        spots = spots_for(wm, self.active_trees() or self.trees)
        if self.spot_names is not None:
            spots = [(n, s) for n, s in spots if n in self.spot_names]
        if not spots:
            log.warning("No saved spots for %s on map '%s'", "/".join(self.trees), wm.name)
            return False
        here = self.walker.where()
        for _ in range(len(spots)):
            name, s = spots[self.spot_turn % len(spots)]
            self.spot_turn += 1
            if here and math.hypot(s["x"] - here.x, s["y"] - here.y) < 25 and len(spots) > 1:
                continue  # that's where we are - try the next one
            self.state = f"walking to {name}"
            log.info("Out of trees here - walking to '%s'", name)
            if self.walker.walk_to(s["x"], s["y"]):
                return True
            log.warning("Couldn't reach '%s'", name)
        return False

    def scan_camera(self, tries=4):
        """Rotate the camera in ~quarter turns looking for a tree."""
        for k in range(tries):
            log.info("No choppable %s in view - rotating camera (%d/%d)",
                     "/".join(self.active_trees()) or "tree", k + 1, tries)
            actions.rotate_camera(self.ctx)   # exact quarter turn via the game's camera, else arrow key
            if self.gs:
                from lumberjack.core import interact
                if any(interact.on_screen(*l["screen"]) for _, l in (self._gs_call(self.wanted_trees_gs) or [])):
                    return True
            elif trees.find_trees(self.grab()):
                return True
        return False
