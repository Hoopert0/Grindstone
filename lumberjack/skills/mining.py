"""Mining v1: mine ore rocks, then drop or bank the ore. Mirrors woodcutting.

    FIND_ROCK --(none)--> SCAN_CAMERA --(still none)--> next saved rock spot / give up
        |  hover says "Mine Rocks"
        v
      MINE  --(ore gained / idle again / timeout)--> FIND_ROCK
        |  inventory full
        v
    DROP / BANK --> FIND_ROCK

In the 530 client every ore rock (full or depleted) hovers as "Mine Rocks", so the hover
only confirms "a rock"; the ore type comes from the speck colours (vision/rocks.py). If a
server does name rocks per ore, point ORE_TARGETS at per-ore templates and the hover
verifies the type too. Each rock gives one ore and then depletes.

Saved spots: a mining spot is a map spot with kind "rocks" whose ore names are stored in
the usual "trees" list (so nav.spots.save_spot(wm, name, x, y, ["copper", "tin"], kind="rocks")
works unchanged); a "rocks" list is accepted too. Woodcutting ignores these spots because
their names never match a tree type.
"""
import json
import math
import random
import time
from pathlib import Path
from types import SimpleNamespace

from lumberjack import actions
from lumberjack.core import regions as R
from lumberjack.skills.base import BotBase, StopBot
from lumberjack.ui import inventory, mouseover
from lumberjack.vision import rocks
from lumberjack.vision.activity import ActivityMonitor

SERVER_TICK = 0.6

# ---- hover-text templates (lumberjack/assets/templates/mouseover/<name>.png) ----------
MINE_ACTION = "mine"            # white "Mine"
ROCK_TARGET = "rocks"           # cyan "Rocks"
# ore -> template its hover names. All "Rocks" in 2009scape; change per ore if a server
# names them e.g. "Copper ore rocks" (then capture "copper_rocks" etc.)
ORE_TARGETS = {ore: ROCK_TARGET for ore in rocks.ORE_SPECKS}

# Mining level needed (2009)
ORE_LEVELS = {"clay": 1, "copper": 1, "tin": 1, "iron": 15, "silver": 20, "coal": 30,
              "gold": 40, "mithril": 55, "adamant": 70, "rune": 85}
# seconds to wait for one ore (slow ores take a while at low level)
MINE_TIMEOUT = {"clay": 30, "copper": 30, "tin": 30, "iron": 40, "silver": 60, "coal": 75,
                "gold": 75, "mithril": 120, "adamant": 150, "rune": 240}
DEFAULT_MINE_TIMEOUT = 60
# what auto mode chooses between (clay/silver/gold are side ores; ties go to the first)
AUTO_ORES = ("rune", "adamant", "mithril", "coal", "iron", "copper", "tin")
IDLE_GIVE_UP_S = 4              # clicked but never started moving/swinging
BLOCK_AFTER_FAILS = 3           # clicks in a row on an ore type that yield nothing ...
BLOCK_FOR_S = 600               # ... before we ignore that type for a while
BAD_SPOT_S = 15                 # ignore a screen spot this long after it gave nothing
EMPTY_SCANS_BEFORE_MOVING = 6
MAX_CANDIDATES = 8
GS_RADIUS = 15                  # tiles searched for rocks in the game's scene data
GS_IDLE_READS = 7               # ~1 s of "not animating, not walking" = stopped mining
EMPTY_AFTER_FAILS = 2           # a rock id that gave nothing this often is treated as empty
ROCK_ORES = Path(__file__).resolve().parents[1] / "assets" / "templates" / "rock_ores.json"


# ---- which rock (loc id) holds which ore: learned while mining, synced with the templates ---
def load_rock_ores():
    try:
        return {int(k): v for k, v in json.loads(ROCK_ORES.read_text()).items()}
    except (OSError, ValueError):
        return {}


def save_rock_ores(mapping):
    ROCK_ORES.parent.mkdir(parents=True, exist_ok=True)
    ROCK_ORES.write_text(json.dumps({str(k): v for k, v in sorted(mapping.items())}, indent=1))


def ore_of_item(item_key):
    """'copper_ore' -> 'copper', 'clay' -> 'clay', 'coal' -> 'coal'; None for anything else."""
    if not item_key:
        return None
    if item_key.endswith("_ore"):
        ore = item_key[:-4]
        return "adamant" if ore == "adamantite" else "rune" if ore == "runite" else ore
    return item_key if item_key in ("clay", "coal") else None


def rank_rocks_gs(locs, mapping, wanted, guess=lambda loc: None, allow_unknown=True, empty_ids=()):
    """[(ore, known, loc)] minable rocks worth trying, best ore first, then nearest. Rock ids
    with a learned ore are trusted; unknown ids use `guess` (the screen's ore colours)."""
    out = []
    for loc in locs:
        if "Mine" not in loc["ops"] or loc["id"] in empty_ids:
            continue
        ore = mapping.get(loc["id"])
        if ore == "empty":
            continue
        known = ore is not None
        if not known:
            ore = guess(loc)
        if ore in wanted:
            out.append(((wanted.index(ore), loc["dist"]), (ore, known, loc)))
        elif ore is None and allow_unknown and wanted:
            out.append(((len(wanted), loc["dist"]), (wanted[0], False, loc)))
    out.sort(key=lambda t: t[0])
    return [x for _, x in out]


# ---- pure helpers (offline-testable) ----------------------------------------------------
def order_ores(ores):
    """Best (highest level) first."""
    return sorted(ores, key=lambda o: -ORE_LEVELS.get(o, 0))


def best_ore(level, allowed=None, have=None):
    """Highest-level ore we can mine at `level`, from `allowed` (default all), preferring
    ores listed in `have` (ores with a saved spot) when that leaves any. None if none fit."""
    pool = [o for o in (allowed or ORE_LEVELS) if ORE_LEVELS.get(o, 99) <= level]
    if have:
        pool = [o for o in pool if o in have] or pool
    return max(pool, key=lambda o: ORE_LEVELS[o]) if pool else None


def spot_ores(spot):
    return list(spot.get("rocks") or spot.get("trees") or [])


def rock_spots(spots, ores):
    """(name, spot) for saved mining spots listing one of `ores` (or listing none)."""
    out = []
    for name, s in spots.items():
        if s.get("kind") != "rocks":
            continue
        listed = spot_ores(s)
        if not listed or set(listed) & set(ores):
            out.append((name, s))
    return out


def rank_candidates(cands, wanted, allow_unknown=True):
    """Order rock candidates: wanted ores (best ore first, then nearest), then rocks whose
    ore couldn't be told (if allowed). Rocks showing an unwanted ore are dropped."""
    out = []
    for c in cands:
        ore = getattr(c, "ore", None)
        if ore in wanted:
            out.append(((wanted.index(ore), c.dist), c))
        elif ore is None and allow_unknown:
            out.append(((len(wanted), c.dist), c))
    out.sort(key=lambda t: t[0])
    return [c for _, c in out]


class Miner(BotBase):
    name = "mining"

    def __init__(self, ores_allowed=("copper", "tin"), when_full="drop", drop_at=28,
                 mine_spot=None, bank_spot=None, spot_names=None, auto_ores=False,
                 allow_unknown=True, spawn_tools=True, **kw):
        super().__init__(**kw)
        self.activity = ActivityMonitor()
        if isinstance(ores_allowed, str):
            ores_allowed = [ores_allowed]
        self.ores = order_ores(ores_allowed)
        self.when_full = when_full       # "drop" | "bank"
        self.drop_at = drop_at
        self.mine_spot = mine_spot       # walk here before mining / after banking
        self.bank_spot = bank_spot       # None = the map's first bank spot
        self.spot_names = spot_names     # None = every rock spot listing our ores
        self.auto_ores = auto_ores       # pick the ore (and spot) from the Stats-tab level
        self.allow_unknown = allow_unknown   # also try rocks whose specks we can't classify
        self.spawn_tools = spawn_tools       # ::item a pickaxe if we have none (game data)
        self.levels = {}
        self.mined = {}                  # ore -> count (self.logs_cut is the total)
        self.blocked = {}                # ore -> time it's allowed again
        self.fail_streak = {}
        self.bad_spots = []              # (x, y, expiry)
        self.empty_scans = 0
        self.spot_turn = 0
        self.learner = None
        self.gs = None                   # the game's own data (core.gamestate), when readable
        self.rock_ores = load_rock_ores()
        self.rock_fails = {}             # loc id -> clicks that gave nothing (this run)
        self.last_rock = None            # the loc we clicked last

    def stats(self):
        hrs = (time.monotonic() - self.started) / 3600
        rate = f" ({self.logs_cut / hrs:.0f}/hr)" if hrs > 0 else ""
        bank = f", {self.banked} bank trips" if self.banked else ""
        return f"{self.logs_cut} ore{rate}{bank}"

    # ---- main loop --------------------------------------------------------------------
    def run(self):
        self.log.info("Miner started - press F12 to stop")
        try:
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs:
                self.log.info("Reading the game's own data: rocks from the scene, ore learned per rock")
            else:
                self.check_templates()
            self.prepare()
            if self.gs and self.spawn_tools:
                from lumberjack import items
                from lumberjack.core import gamestate
                lv = (gamestate.skill("mining") or {}).get("base", 1)
                slot = items.ensure(self.ctx, lambda k: k.endswith("pickaxe"), items.best_pickaxe(lv), log=self.log)
                if slot is not None and slot >= 0:
                    self.keep_slots = set(self.keep_slots) | {slot}
            if self.walker:
                from lumberjack.nav.learner import MapLearner
                self.learner = MapLearner(self.walker, self.win)
                self.learner.start()
            if self.auto_ores:
                self.pick_ores(walk=False)
            if self.mine_spot and self.walker:
                if not self.walk_to_spot(self.mine_spot):
                    raise StopBot(f"couldn't reach '{self.mine_spot}'")
                actions.reset_camera(self.ctx)
            while True:
                actions.dismiss_dialog(self.ctx)
                if inventory.count(self.grab()) >= self.drop_at:
                    self.handle_full_inventory()
                    continue
                self.state = "finding rock"
                if not self.active_ores():
                    self.log.warning("None of the selected ores can be mined right now (%s) - waiting",
                                     ", ".join(self.ores))
                    self.state = "waiting"
                    self.sleep(30)
                    continue
                hit = self.find_and_click_rock()
                if not hit:
                    self.empty_scans += 1
                    self.state = "scanning"
                    if self.empty_scans < EMPTY_SCANS_BEFORE_MOVING and self.scan_camera(3 if self.walker else 4):
                        continue
                    if self.walker and self.walk_to_next_spot():
                        self.empty_scans = 0
                        continue
                    if self.empty_scans < EMPTY_SCANS_BEFORE_MOVING * 3:
                        self.state = "waiting for respawn"
                        self.sleep(random.uniform(2, 4))   # rocks respawn - give them a moment
                        continue
                    self.log.warning("No minable rocks found for a while - stopping")
                    return
                self.empty_scans = 0
                ore, cand, known = hit
                self.state = f"mining {ore}"
                self.mine(ore, cand, known)
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            if self.learner:
                self.learner.stop()
            self.inp.close()

    def check_templates(self):
        missing = [n for n in {MINE_ACTION, *(ORE_TARGETS.get(o, ROCK_TARGET) for o in self.ores)}
                   if not mouseover.available(n)]
        if missing:
            raise StopBot(f"hover-text template(s) {', '.join(sorted(missing))} not captured yet - "
                          f"hover a rock and save 'Mine Rocks' as '{MINE_ACTION}' + '{ROCK_TARGET}'")

    # ---- finding / clicking -----------------------------------------------------------
    def active_ores(self):
        now = time.monotonic()
        for o, until in list(self.blocked.items()):
            if until <= now:
                del self.blocked[o]
                self.fail_streak.pop(o, None)
                self.log.info("Trying %s again", o)
        return [o for o in self.ores if o not in self.blocked]

    def _targets(self, ores):
        seen = []
        for o in ores:
            t = ORE_TARGETS.get(o, ROCK_TARGET)
            if t not in seen and mouseover.available(t):
                seen.append(t)
        return seen

    def _hover(self, x, y, targets):
        self.inp.move(x, y)
        self.sleep(random.uniform(0.12, 0.2))
        return mouseover.which(self.grab(), MINE_ACTION, targets)

    # ---- rocks from the game's scene data ---------------------------------------------
    def _gs_call(self, fn, *a):
        from lumberjack.core.gamestate import tolerant_call
        return tolerant_call(self, fn, *a, logger=self.log)

    def _guess_ore(self, frame):
        """The screen's ore colours for a rock the game data hasn't taught us yet."""
        pix = rocks.find_rocks(frame)

        def guess(loc):
            x, y = loc["screen"]
            near = [c for c in pix if abs(c.x - x) < 28 and abs(c.y - y) < 28]
            return min(near, key=lambda c: abs(c.x - x) + abs(c.y - y)).ore if near else None
        return guess

    def find_and_click_rock_gs(self, walked=False):
        from lumberjack.core import interact
        empty = {i for i, n in self.rock_fails.items() if n >= EMPTY_AFTER_FAILS}
        ranked = rank_rocks_gs(self.gs.locs(GS_RADIUS), self.rock_ores, self.active_ores(),
                               self._guess_ore(self.grab()), self.allow_unknown, empty)
        visible = [r for r in ranked if interact.on_screen(*r[2]["screen"])]
        for ore, known, loc in visible[:4]:
            pt = interact.use_option(self.ctx, self.gs, interact.points_for(loc), "Mine", loc["name"])
            if pt:
                self.last_rock = loc
                self.log.info("Mining %s rock %d tile(s) away%s", ore if known else "an unknown", loc["dist"],
                              "" if known else " (learning which ore it holds)")
                return ore, SimpleNamespace(x=pt[0], y=pt[1], dist=loc["dist"]), known
        if ranked and not visible and not walked:
            loc = ranked[0][2]
            self.state = "walking to a rock"
            self.log.info("Nearest rock is %d tiles away - walking toward it", loc["dist"])
            interact.walk_toward(self.ctx, self.gs.player()["tile"], loc["tile"])
            end = time.monotonic() + 12
            self.sleep(0.6)
            while time.monotonic() < end and self.gs.player().get("moving"):
                self.sleep(0.2)
            return self.find_and_click_rock_gs(walked=True)
        return None

    def _ore_counts(self):
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        out = {}
        for s in inv:
            ore = ore_of_item(s.get("key"))
            if ore:
                out[ore] = out.get(ore, 0) + max(1, s["count"])
        return out

    def learn_rock(self, before_ores, got):
        """After a mine: remember which ore this rock id gave - and that the rock now standing
        on its tile (a different id: the empty rock) is empty."""
        loc = self.last_rock
        if not (self.gs and loc):
            return None
        if got <= 0:
            self.rock_fails[loc["id"]] = self.rock_fails.get(loc["id"], 0) + 1
            return None
        after = self._ore_counts()
        gained = [o for o, n in after.items() if n > before_ores.get(o, 0)]
        changed = False
        if len(gained) == 1 and self.rock_ores.get(loc["id"]) != gained[0]:
            self.rock_ores[loc["id"]] = gained[0]
            self.log.info("Learned: rock %d holds %s", loc["id"], gained[0])
            changed = True
        for now in self.gs.locs(2):
            if now["tile"] == loc["tile"] and now["id"] != loc["id"] and now["id"] not in self.rock_ores:
                self.rock_ores[now["id"]] = "empty"
                changed = True
        if changed:
            save_rock_ores(self.rock_ores)
        return gained[0] if len(gained) == 1 else None

    def busy_from_game(self):
        if not self.gs:
            return None
        pl = self._gs_call(self.gs.player)
        return None if pl is None else (pl.get("anim", -1) != -1 or bool(pl.get("moving")))

    def find_and_click_rock(self):
        """Hover ranked candidates; click the first verified rock.
        Returns (ore, candidate, ore_known) or None."""
        if self.gs:
            hit = self._gs_call(self.find_and_click_rock_gs)
            if self.gs:
                return hit
        frame = self.grab()
        now = time.monotonic()
        self.bad_spots = [b for b in self.bad_spots if b[2] > now]
        wanted = self.active_ores()
        if not wanted:
            return None
        targets = self._targets(wanted)
        cands = rank_candidates(rocks.find_rocks(frame), wanted, self.allow_unknown)
        for cand in cands[:MAX_CANDIDATES]:
            if any(abs(cand.x - bx) < 15 and abs(cand.y - by) < 15 for bx, by, _ in self.bad_spots):
                continue
            bx, by, bw, bh = cand.bbox
            for px, py in [(cand.x, cand.y), (bx + bw // 2, by + bh // 3)]:
                px += random.randint(-2, 2)
                py += random.randint(-2, 2)
                t = self._hover(px, py, targets)
                if not t:
                    continue
                ore, known = self._ore_for(cand, t, wanted)
                if ore is None:
                    break   # hover names an ore we don't want
                self.inp.click()
                self.log.info("Clicked %s rock at (%d, %d), %.0f px away", ore if known else "unknown",
                              px, py, cand.dist)
                return ore, cand, known
            else:
                self.bad_spots.append((cand.x, cand.y, now + BAD_SPOT_S))
        return None

    def _ore_for(self, cand, target, wanted):
        """(ore, known) from the matched hover template and the speck classification."""
        named = [o for o in wanted if ORE_TARGETS.get(o, ROCK_TARGET) == target]
        if len(named) == 1 and target != ROCK_TARGET:
            return named[0], True                  # per-ore hover text: authoritative
        if cand.ore in named:
            return cand.ore, True
        if cand.ore is None and self.allow_unknown:
            return (named or wanted)[0], False     # unclassified - assume the best wanted ore
        return None, False

    # ---- mining -----------------------------------------------------------------------
    def mine(self, ore, cand, known=True):
        """Wait while we walk over and swing, until an ore lands, we go idle, or timeout."""
        timeout = MINE_TIMEOUT.get(ore, DEFAULT_MINE_TIMEOUT)
        before = inventory.count(self.grab())
        ores_before = self._ore_counts() if self.gs else {}
        self.activity.reset()
        start = time.monotonic()
        started = False
        idle_reads = 0
        while time.monotonic() - start < timeout:
            frame = self.grab()
            if inventory.count(frame) > before:
                break                              # one ore per rock - it's depleted now
            self.activity.update(frame)
            busy = self.busy_from_game()
            if busy is None:                       # no game data: judge by the picture moving
                busy, settled = self.activity.active, self.activity.filled
            else:
                idle_reads = 0 if busy else idle_reads + 1
                settled = idle_reads >= GS_IDLE_READS
            if busy:
                started = True
            elif settled and (started or time.monotonic() - start > IDLE_GIVE_UP_S):
                break                              # stopped (rock emptied / "no ore") or never began
            self.sleep(0.15)
        self.sleep(SERVER_TICK)
        got = inventory.count(self.grab()) - before
        if self.gs:
            learned = self._gs_call(self.learn_rock, ores_before, got)
            if learned:
                ore, known = learned, True
        if got > 0:
            self.logs_cut += got
            self.mined[ore] = self.mined.get(ore, 0) + got
            self.fail_streak[ore] = 0
            self.log.info("+%d %s - %s", got, ore if known else "ore", self.stats())
            return True
        # nothing: depleted before we got there, or level too low ("You need a Mining level...")
        self.bad_spots.append((cand.x, cand.y, time.monotonic() + BAD_SPOT_S))
        if not started:
            self.log.info("Nothing happened after clicking - trying another rock")
        if known:
            self.fail_streak[ore] = self.fail_streak.get(ore, 0) + 1
            if self.fail_streak[ore] >= BLOCK_AFTER_FAILS and len(self.ores) > 1:
                self.blocked[ore] = time.monotonic() + BLOCK_FOR_S
                self.log.warning("No %s after %d tries - level too low? Skipping it for %d min",
                                 ore, self.fail_streak[ore], BLOCK_FOR_S // 60)
        return False

    def scan_camera(self, tries=4):
        """Rotate the camera in ~quarter turns looking for a rock worth hovering."""
        for k in range(tries):
            self.log.info("No minable %s in view - rotating camera (%d/%d)",
                          "/".join(self.active_ores()) or "rock", k + 1, tries)
            actions.rotate_camera(self.ctx)   # exact quarter turn via the game's camera, else arrow key
            if rank_candidates(rocks.find_rocks(self.grab()), self.active_ores(), self.allow_unknown):
                return True
        return False

    # ---- levels -----------------------------------------------------------------------
    def pick_ores(self, walk=True):
        """Auto mode: mine the best ore our Mining level allows (and that has a saved spot)."""
        from lumberjack.ui import stats
        self.state = "checking levels"
        lv = stats.read_level(self.ctx, "mining")
        if not lv:
            self.log.warning("Couldn't read the Mining level - keeping %s", ", ".join(self.ores))
            return
        self.levels = {"mining": lv[0]}
        spots = self.walker.map.spots if self.walker else {}
        have = {o for _, s in rock_spots(spots, ORE_LEVELS) for o in spot_ores(s)}
        best = best_ore(lv[0], allowed=AUTO_ORES, have=have)
        if not best:
            return
        if self.ores != [best]:
            self.log.info("Mining level %d - mining %s", lv[0], best)
        self.ores = [best]
        self.blocked.pop(best, None)
        fits = [n for n, s in rock_spots(spots, [best]) if best in spot_ores(s)]
        if fits:
            self.spot_names = None
            if self.mine_spot not in fits:
                self.mine_spot = fits[0]
                if walk:
                    self.walk_to_spot(self.mine_spot)
                    actions.reset_camera(self.ctx)

    # ---- full inventory ---------------------------------------------------------------
    def handle_full_inventory(self):
        if self.when_full == "bank":
            self.state = "banking"
            if not self.bank_trip():
                self.log.warning("Banking failed - moving a little and retrying")
                from lumberjack.skills.firemaking import _step_aside
                _step_aside(self.ctx)
                if not self.bank_trip():
                    raise StopBot("couldn't get to the bank - stopped with the ore kept")
        else:
            self.state = "dropping"
            self.log.info("Inventory full - dropping")
            actions.drop_all(self.ctx, keep=self.keep_slots)
            self.log.info("Dropped. %s", self.stats())
        if self.auto_ores:
            self.pick_ores()

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
                self.log.info("Banked (trip %d). %s", self.banked, self.stats())
                actions.reset_camera(self.ctx)
                return True
            self.log.warning("Banking needs a map with a bank spot")
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
        target = self.spot_xy(self.mine_spot) or ((here.x, here.y) if here else None)
        if target:
            self.state = "walking back"
            self.walker.walk_to(*target)
        actions.reset_camera(self.ctx)
        return True

    # ---- moving between spots ---------------------------------------------------------
    def walk_to_next_spot(self):
        """Walk to the next saved rock spot (round-robin) that lists the ores we want."""
        from lumberjack.nav.worldmap import MAPS
        wm = self.walker.map
        try:  # pick up spots saved from the control panel while we were running
            wm.spots = json.loads((MAPS / f"{wm.name}.json").read_text()).get("spots", {})
        except (OSError, ValueError):
            pass
        spots = rock_spots(wm.spots, self.active_ores() or self.ores)
        if self.spot_names is not None:
            spots = [(n, s) for n, s in spots if n in self.spot_names]
        if not spots:
            self.log.warning("No saved rock spots for %s on map '%s'", "/".join(self.ores), wm.name)
            return False
        here = self.walker.where()
        for _ in range(len(spots)):
            name, s = spots[self.spot_turn % len(spots)]
            self.spot_turn += 1
            if here and math.hypot(s["x"] - here.x, s["y"] - here.y) < 25 and len(spots) > 1:
                continue
            self.state = f"walking to {name}"
            self.log.info("Out of rocks here - walking to '%s'", name)
            if self.walker.walk_to(s["x"], s["y"]):
                actions.reset_camera(self.ctx)
                return True
            self.log.warning("Couldn't reach '%s'", name)
        return False


# ---- calibration helpers (call from the control panel's manual runner) --------------------
def learn_hover(ctx):
    """Hover rock candidates until the hover text reads "Mine Rocks"; save the white verb as
    mouseover/mine.png and the cyan name as mouseover/rocks.png. Stand right next to a rock
    (full or empty - they all hover the same). Returns True on success."""
    import logging
    log = logging.getLogger("calibrate")
    for c in rocks.find_rocks(ctx.grab())[:MAX_CANDIDATES]:
        bx, by, bw, bh = c.bbox
        for px, py in [(c.x, c.y), (bx + bw // 2, by + bh // 3), (bx + bw // 2, by + 2 * bh // 3)]:
            ctx.inp.move(px, py)
            ctx.sleep(0.25)
            frame = ctx.grab()
            strip = R.MOUSEOVER_TEXT.crop(frame)
            if mouseover.strip_mask(strip, "cyan").sum() // 255 < 15:
                continue    # no object name showing
            if mouseover.available(MINE_ACTION) and not mouseover.action_ok(strip, MINE_ACTION):
                continue    # some other object ("Walk here", a tree...)
            shape = mouseover.save_word_templates(frame, MINE_ACTION, ROCK_TARGET, "cyan")
            log.info("Learned '%s' + '%s' from hover at (%d, %d) %s", MINE_ACTION, ROCK_TARGET, px, py, shape)
            ctx.inp.move(260, 300)
            return True
    log.warning("No rock hover found - stand right next to a rock (camera reset) and retry")
    return False


def describe_view(frame):
    """One log line of what rock vision sees - for tuning vision/rocks.py at a mine."""
    cands = rocks.find_rocks(frame)
    if not cands:
        return "No rocks found in view."
    parts = [f"{c.ore or 'empty'} at ({c.x}, {c.y})" for c in cands[:8]]
    return f"{len(cands)} rock(s): " + ", ".join(parts)
