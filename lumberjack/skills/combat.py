"""Combat v1: melee-train Attack / Strength / Defence on chickens, cows, goblins...

    dismiss dialog (level up -> maybe switch the trained stat)
    HP low?  -> eat (out of food -> stop)
    fighting (health bar / hitsplat next to us)? -> wait it out
    else: NPC dots on the minimap -> screen points -> hover -> "Attack <target>" -> click
          -> wait until the fight ends (target's bar gone / we go idle)
    nothing in view -> walk toward an NPC dot on the minimap -> saved combat spot -> wait

Hover text is the safety check: we only click when it reads "Attack <one of targets>".

Templates needed (assets/templates/mouseover/, see vision/npcs.calibrate_npc):
    attack.png                       white verb in "Attack Chicken (level-1)"
    <target>.png  e.g. chicken.png   yellow NPC name (without the level suffix)
    eat.png + <food>.png             only if foods are used ("Eat Shrimps", food in orange)
    take.png + <item>.png            only with loot ("Take Bones", ground item in orange)
    bury.png + bones.png             only with bury_bones
"""
import logging
import math
import random
import time

import cv2
import numpy as np

from lumberjack import actions
from lumberjack.core import regions as R
from lumberjack.skills.base import BotBase, StopBot
from lumberjack.ui import inventory, mouseover
from lumberjack.vision import health, npcs
from lumberjack.vision.activity import ActivityMonitor

log = logging.getLogger("combat")

MELEE = ("attack", "strength", "defence")

# Combat tab (fixed mode): the attack-style buttons, 2 x 2.          CALIBRATE
STYLE_BUTTONS = [(594, 258), (684, 258), (594, 310), (684, 310)]
# which button trains which stat. 4-style weapons (swords, axes, scimitars):
#   0 accurate=Attack, 1 aggressive=Strength, 2 2nd aggressive/controlled, 3 defensive=Defence
# 3-style (unarmed, many maces/daggers?): Defence is button 2 - pass style_slots to override
DEFAULT_STYLE_SLOTS = {"attack": 0, "strength": 1, "defence": 3}

SERVER_TICK = 0.6
EAT_DELAY = 1.9                 # eating takes 3 ticks before the next action
ENGAGE_TIMEOUT = 8.0            # clicked but no fight started within this -> wrong target / blocked
FIGHT_TIMEOUT = 120.0
COMBAT_GONE_S = 3.0             # no bars/splats for this long (and idle) -> fight over
KILL_FRAC = 0.15                # target bar seen at or below this before vanishing = a kill
DEFAULT_FOOD = "lobster"         # food spawned when none is picked (game data + spawn missing tools)
FOOD_SPAWN = 10                 # food carried after a top-up (spawn missing tools)
FOOD_MIN = 5                    # fewer than this carried -> top up to FOOD_SPAWN
DEATH_WAIT = 5.0                # wait this long at most for a dying target to vanish
LOOT_SETTLE = 1.2               # after a kill, drops appear a tick or two later
MAX_CANDIDATES = 5
BAD_SPOT_S = 8                  # ignore a screen spot this long after it hovered wrong
LEVEL_CHECK_KILLS = 15          # re-read levels this often (also after any level-up dialog)
EMPTY_ROUNDS_BEFORE_WALK = 2
APPROACH_WALKS = 3              # minimap walks toward NPC dots before trying saved spots
GIVE_UP_ROUNDS = 60             # ~5+ minutes with nothing to attack -> stop
SKIP_TARGET_S = 60              # a monster we couldn't fight (unreachable, taken) is left this long
MM_PER_TILE = 4                 # minimap px per game tile
TAKE_ACTION = "take"            # white "Take" before an orange ground-item name
LOOT_PICKS = 6                  # items picked up from one pile at most
PICKUP_WAIT = 3.0               # walk onto the tile + pick up
LOOT_RADIUS = 8                 # tiles around us searched for drops (game data)


def choose_stat(levels, current=None, spread=2, caps=None, order=MELEE):
    """Which melee stat to train next.

    Keep the current one until it is `spread` levels above the lowest of the others, then
    switch to the lowest (ties broken by `order`). Stats at/above their cap (caps =
    {"attack": 40}) are skipped. None if nothing is trainable / no levels known."""
    caps = caps or {}
    ok = [s for s in order if s in levels and levels[s] < caps.get(s, 99)]
    if not ok:
        return None
    lowest = min(ok, key=lambda s: (levels[s], order.index(s)))
    if current in ok:
        others = [levels[s] for s in ok if s != current]
        if not others or levels[current] < min(others) + spread:
            return current
    return lowest


def loot_points(kill_point=None, project=None, view=None):
    """Screen points to hover for drops, best first: where the target died, our own tile,
    then the 8 tiles around us (melee targets die next to us)."""
    project = project or npcs.project
    view = view or npcs.in_view
    tiles = [(0, 0)] + [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dx, dy) != (0, 0)]
    pts = [kill_point] if kill_point else []
    # project() aims at an NPC's body; ground items lie on the tile itself
    pts += [(x, y + npcs.AIM_UP) for x, y in (project(dx * MM_PER_TILE, dy * MM_PER_TILE) for dx, dy in tiles)]
    out = []
    for p in pts:
        if view(*p, margin=4) and all(abs(p[0] - q[0]) > 8 or abs(p[1] - q[1]) > 8 for q in out):
            out.append(p)
    return out


def should_eat(hp, eat_below):
    return hp is not None and hp < eat_below


def fight_result(engaged, min_target_frac):
    """'kill' (saw the target's bar run out), 'ended' (fought, outcome unclear), or
    'no_engage' (never got into combat)."""
    if not engaged:
        return "no_engage"
    return "kill" if min_target_frac is not None and min_target_frac <= KILL_FRAC else "ended"


class Fighter(BotBase):
    name = "combat"

    def __init__(self, targets=("chicken",), foods=(), eat_below=0.5, train="auto",
                 style_slots=None, spread=2, level_caps=None, bury_bones=False, loot=(),
                 fight_spot=None, fight_spots=None, spawn_tools=True, **kw):
        kw.pop("keep_carried", None)          # food etc. is fine - we never drop or bank
        super().__init__(keep_carried=False, **kw)
        self.targets = [targets] if isinstance(targets, str) else list(targets)
        self.foods = [foods] if isinstance(foods, str) else list(foods)
        self.eat_below = eat_below
        self.train = train                    # "auto" | "attack" | "strength" | "defence" | None
        self.style_slots = {**DEFAULT_STYLE_SLOTS, **(style_slots or {})}
        self.spread = spread
        self.level_caps = level_caps or {}
        self.bury_bones = bury_bones
        self.loot_items = list(loot)
        self.fight_spot = fight_spot          # walk here at start (a saved spot name)
        self.fight_spots = fight_spots        # spots to roam between (None = kind "combat")
        self.spawn_tools = spawn_tools        # ::item food when none is carried (game data)
        self.activity = ActivityMonitor()
        self.levels = {}
        self.max_hp = None
        self.training = None
        self.kills = 0
        self.eaten = 0
        self.food_slots = None                # None = not scanned yet
        self.bad_spots = []                   # (x, y, expiry)
        self.prev_frame = None
        self.empty_rounds = 0
        self.approach_walks = 0
        self.spot_turn = 0
        self.kills_at_check = 0
        self.last_click = None
        self.kill_point = None                # where the target's bar was last seen
        self.gs = None                        # the game's own data (core.gamestate), when readable
        self.target_index = None              # NpcList index of the monster we attacked
        self.kill_tile = None                 # world tile where it last stood (drops land there)
        self.looted = 0

    # ---- bookkeeping -----------------------------------------------------------------
    def progress(self):
        return self.kills

    def stats(self):
        hrs = (time.monotonic() - self.started) / 3600
        rate = f" ({self.kills / hrs:.0f}/hr)" if hrs > 0.01 else ""
        return f"{self.kills} kills{rate}, ate {self.eaten}"

    def _count_kill(self):
        self.kills += 1
        self.logs_cut = self.kills            # the panel shows logs_cut / per-hour
        log.info("Kill #%d - %s", self.kills, self.stats())

    def hp(self, frame):
        if self.gs:
            from lumberjack.core import gamestate
            g = gamestate.skill("hitpoints")
            if g and g["base"]:
                return g["level"] / g["base"]        # current HP / max HP, straight from the game
        return health.hp_fraction(frame, self.max_hp)

    # ---- main loop -------------------------------------------------------------------
    def run(self):
        log.info("Combat started (targets: %s) - press F12 to stop", ", ".join(self.targets))
        learner = None
        try:
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs:
                log.info("Reading the game's own data: monsters by name, HP, who's fighting whom")
            else:
                missing = [t for t in ["attack"] + self.targets if not mouseover.available(t)]
                if missing:
                    raise StopBot(f"no hover-text template for {', '.join(missing)} - calibrate first")
            self.prepare()
            if self.walker:
                from lumberjack.nav.learner import MapLearner
                learner = MapLearner(self.walker, self.win)
                learner.start()
            if self.train == "ranged":
                self.equip_ranged()
            else:
                self.equip_melee()
            self.check_levels(force_style=True)
            if self.fight_spot and self.walker:
                if not self.walk_to_spot(self.fight_spot):
                    raise StopBot(f"couldn't reach '{self.fight_spot}'")
                actions.reset_camera(self.ctx)
            if not self.foods and self.gs and self.spawn_tools:
                # nothing picked: lobsters are spawned, and any cooked fish we carry is eaten too
                from lumberjack.core.backpack import COOKED
                self.foods = [DEFAULT_FOOD] + sorted(COOKED - {DEFAULT_FOOD})
                log.info("No food picked - using %s (spawned) and any cooked fish carried",
                         DEFAULT_FOOD.replace("_", " "))
            if self.foods and self.gs and self.spawn_tools:
                self.top_up_food()                # a fresh account's 2 shrimps + bread aren't enough
            if self.foods:
                self.scan_food()
            while True:
                if actions.dismiss_dialog(self.ctx):
                    self.check_levels()           # level up: maybe rotate the trained stat
                    continue
                frame = self.grab()
                self.ensure_hp(frame)
                attacker = self._gs_call(self.attacker_gs) if self.gs else None
                if attacker is not None:           # something is already fighting us
                    self.target_index = attacker
                    self.state = "fighting"
                    self.fight(engaged=True)
                    self.under_attack_since = None
                    continue
                if self.gs and self.waiting_out_combat():
                    continue
                if not self.gs and health.in_combat(frame):
                    self.state = "fighting"
                    self.fight(engaged=True)
                    continue
                if self.kills - self.kills_at_check >= LEVEL_CHECK_KILLS:
                    self.check_levels()
                self.state = "finding target"
                target = self.attack_nearest(frame)
                if target:
                    self.empty_rounds = self.approach_walks = 0
                    self.state = f"fighting {target}"
                    self.fight(engaged=False)
                    continue
                self.no_target()
        except StopBot as e:
            self.stop_reason = str(e)
            log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            if learner:
                learner.stop()
            self.inp.close()

    # ---- HP / food -------------------------------------------------------------------
    def ensure_hp(self, frame):
        hp = self.hp(frame)
        if not should_eat(hp, self.eat_below):
            return
        if self.foods and self.eat():
            return
        if self.foods and self.restock_food() and self.eat():
            return
        raise StopBot(f"HP low ({hp:.0%}) and no food left" if self.foods else
                      f"HP low ({hp:.0%}) and no food configured")

    def restock_food(self):
        """Out of food: spawn more (game data + 'Spawn missing tools'). True if some arrived."""
        if not (self.gs and self.spawn_tools):
            return False
        self.food_slots = None              # rescan on the next eat()
        return self.top_up_food() > 0

    def top_up_food(self):
        """Carry at least FOOD_MIN food: spawn up to FOOD_SPAWN, dropping loot and the unused
        starter kit first if the backpack is too full. Returns how many were spawned."""
        from lumberjack import items
        from lumberjack.core import backpack
        spawnable = [f for f in self.foods if f in items.BY_KEY]
        inv = backpack.slots()
        if not spawnable or inv is None:
            return 0
        have = sum(1 for s in inv if s["key"] in self.foods)
        if have >= FOOD_MIN:
            return 0
        want = FOOD_SPAWN - have
        free = sum(1 for s in inv if s["id"] < 0)
        if free < want:
            junk = [i for i, s in enumerate(inv) if s["id"] >= 0 and s["key"] not in self.foods
                    and (backpack.is_product(s["key"]) or s["key"] in backpack.STARTER)]
            if junk:
                log.info("Making room for food - dropping %d item(s)", len(junk))
                actions.drop_known(self.ctx, self.gs, junk[:want - free])
        log.info("Carrying %d food - spawning %s", have, spawnable[0].replace("_", " "))
        got = items.fill(self.ctx, spawnable[0], want)
        self.inp.move(260, 300)
        return got

    def scan_food(self):
        """Remember which slots hold food (by name from the game, else by hovering once)."""
        from lumberjack.core import backpack
        inv = backpack.slots()
        if inv is not None:
            self.food_slots = [i for i, s in enumerate(inv) if s["key"] in self.foods]
            log.info("Food in %d slot(s)", len(self.food_slots))
            return
        if not mouseover.available("eat"):
            log.warning("No 'eat' hover template - can't recognise food (calibrate 'Eat <food>')")
            self.food_slots = []
            return
        actions.open_tab(self.ctx, "inventory")
        slots = []
        for i, occ in enumerate(inventory.occupied(self.grab())):
            if not occ:
                continue
            self.inp.move(*R.INV_SLOTS[i].center, steps=2)
            self.sleep(0.12)
            if mouseover.which(self.grab(), "eat", self.foods, "orange"):
                slots.append(i)
        self.inp.move(260, 300)
        self.food_slots = slots
        log.info("Food in %d slot(s)", len(slots))

    def eat(self):
        """Eat one food item. False when there's none left."""
        self.state = "eating"
        if self.food_slots is None:
            self.scan_food()
        actions.open_tab(self.ctx, "inventory")
        for attempt in range(2):
            occ = inventory.occupied(self.grab())
            for i in list(self.food_slots):
                if not occ[i]:
                    self.food_slots.remove(i)
                    continue
                x, y = R.INV_SLOTS[i].center
                self.inp.move(x + random.randint(-4, 4), y + random.randint(-4, 4), steps=2)
                self.sleep(0.12)
                from lumberjack.core import backpack
                inv = backpack.slots()
                if inv is not None:
                    food = inv[i]["key"] if inv[i]["key"] in self.foods else None
                else:
                    food = mouseover.which(self.grab(), "eat", self.foods, "orange")
                if not food:
                    self.food_slots.remove(i)
                    continue
                self.inp.click()
                self.eaten += 1
                log.info("Ate %s (%d left)", food.replace("_", " "), len(self.food_slots) - 1)
                self.food_slots.remove(i)
                self.sleep(EAT_DELAY)
                self.inp.move(260, 300)
                return True
            if attempt == 0:
                self.scan_food()       # maybe food was picked up / slots moved
        return False

    # ---- levels & attack style -------------------------------------------------------
    # ---- ranged: a bow and arrows, spawned and worn ----------------------------------------
    ARROWS_LOW = 50            # fewer worn arrows than this -> spawn more
    ARROWS_SPAWN = 1000

    def equip_ranged(self):
        """With game data + spawning: wear the best bow for the Ranged level and its arrows."""
        if not (self.gs and self.spawn_tools):
            return
        from lumberjack import items
        from lumberjack.core import backpack, gamestate
        lv = (gamestate.skill("ranged") or {}).get("base", 1)
        bow, arrows = items.best_bow(lv)
        worn = {s["key"]: s.get("count", 1) for s in self._worn()}
        for key, amount in ((bow, 1), (arrows, self.ARROWS_SPAWN)):
            if key in worn and (amount == 1 or worn[key] >= self.ARROWS_LOW):
                continue
            inv = backpack.slots() or []
            slot = next((i for i, s in enumerate(inv) if s["key"] == key), None)
            if slot is None and not any(s["id"] < 0 for s in inv):
                self.drop_outgrown_ranged(bow, arrows, loot=True)
                inv = backpack.slots() or []
            if slot is None:
                log.info("Ranged %d: spawning %s", lv, key.replace("_", " "))
                items.spawn(self.ctx, key, amount)
                self.sleep(0.6)
                inv = backpack.slots() or []
                slot = next((i for i, s in enumerate(inv) if s["key"] == key), None)
            if slot is None:
                log.warning("Couldn't get %s", key.replace("_", " "))
                continue
            if actions.use_slot(self.ctx, self.gs, slot, "Wield"):
                log.info("Wielding %s", key.replace("_", " "))
                self.sleep(0.8)
        self.drop_outgrown_ranged(bow, arrows)     # the bow it replaced, arrows it can't fire
        self.inp.move(260, 300)

    def drop_outgrown_ranged(self, bow, arrows, loot=False):
        """Drop bows and arrows other than `bow` and `arrows` from the backpack (and loot, raw and
        burnt items with loot=True). Returns how many slots were dropped."""
        from lumberjack import items
        from lumberjack.core import backpack
        gear = {b for b, _, _ in items.BOWS} | {a for _, _, a in items.BOWS} | {"bronze_arrow", "iron_arrow"}
        inv = backpack.slots() or []
        junk = [i for i, s in enumerate(inv) if s["id"] >= 0 and (
            (s["key"] in gear and s["key"] not in (bow, arrows)) or
            (loot and backpack.kind(s["key"]) in ("loot", "raw", "burnt")))]
        if not junk:
            return 0
        log.info("Dropping %d item(s): old bows/arrows%s", len(junk), " and loot" if loot else "")
        actions.drop_known(self.ctx, self.gs, junk)
        return len(junk)

    def equip_melee(self):
        """With game data + spawning: wear the best scimitar for Attack and the best armour for
        Defence (helm, platebody, platelegs, kiteshield), upgrading as the levels go up."""
        if not (self.gs and self.spawn_tools):
            return
        from lumberjack import items
        from lumberjack.core import backpack, gamestate
        att = (gamestate.skill("attack") or {}).get("base", 1)
        dfc = (gamestate.skill("defence") or {}).get("base", 1)
        worn = {s["key"] for s in self._worn()}
        gear = items.melee_gear(att, dfc)
        for key in gear:
            if key in worn or key not in items.BY_KEY:
                continue
            inv = backpack.slots() or []
            slot = next((i for i, s in enumerate(inv) if s["key"] == key), None)
            if slot is None:
                if not any(s["id"] < 0 for s in inv):
                    self.drop_outgrown(gear, loot=True)
                    inv = backpack.slots() or []
                if not any(s["id"] < 0 for s in inv):
                    log.info("No room to spawn %s", key.replace("_", " "))
                    continue
                items.spawn(self.ctx, key, 1)
                self.sleep(0.6)
                inv = backpack.slots() or []
                slot = next((i for i, s in enumerate(inv) if s["key"] == key), None)
            if slot is None:
                continue
            if actions.use_slot(self.ctx, self.gs, slot, "Wield") or actions.use_slot(self.ctx, self.gs, slot, "Wear"):
                log.info("Wearing %s", key.replace("_", " "))
                self.sleep(0.8)
        self.drop_outgrown(gear)            # what the upgrades took off lands in the backpack
        self.inp.move(260, 300)

    def drop_outgrown(self, gear, loot=False):
        """Drop scimitars and armour worse than `gear` (an upgrade swaps the old piece into the
        backpack - a few levels of that filled it). With loot=True also loot, raw and burnt
        items. Returns how many slots were dropped."""
        from lumberjack import items
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        metals = tuple(f"{m}_" for m, _ in items.METALS)
        pieces = ("scimitar",) + items.ARMOUR_PIECES

        def outgrown(k):
            return bool(k) and k not in gear and k.startswith(metals) and k.split("_", 1)[1] in pieces
        junk = [i for i, s in enumerate(inv) if s["id"] >= 0 and (outgrown(s["key"]) or (
            loot and backpack.kind(s["key"]) in ("loot", "raw", "burnt")))]
        if not junk:
            return 0
        log.info("Dropping %d item(s): old gear%s", len(junk), " and loot" if loot else "")
        actions.drop_known(self.ctx, self.gs, junk)
        return len(junk)

    def _worn(self):
        from lumberjack.core import backpack
        try:
            return [dict(s, key=backpack.key(s["name"])) for s in self.gs.inv(94) if s.get("id", -1) >= 0]
        except Exception:
            return []

    def check_levels(self, force_style=False):
        """Read melee + hitpoints levels; learn the orb digits; switch style if needed."""
        from lumberjack.ui import stats
        self.state = "checking levels"
        self.kills_at_check = self.kills
        levels = {}
        for s in MELEE + ("hitpoints",):
            lv = stats.read_level(self.ctx, s)
            if lv:
                levels[s] = lv[1]
                if s == "hitpoints":
                    self.max_hp = lv[1]
                    n = health.learn_digits(self.grab(), lv[0])
                    if n:
                        log.info("Learned %d HP-orb digit(s)", n)
        if not levels:
            log.warning("Couldn't read levels from the Stats tab")
            return
        self.levels = levels
        log.info("Levels: %s", ", ".join(f"{k} {v}" for k, v in levels.items()))
        if self.train == "ranged":
            self.equip_ranged()                  # a better bow, more arrows
            return
        self.equip_melee()                       # a better scimitar / armour after a level-up
        if self.train == "auto":
            want = choose_stat(levels, self.training, self.spread, self.level_caps)
        elif self.train in MELEE:
            want = self.train
        else:
            want = None
        if want and (want != self.training or force_style):
            self.set_style(want)

    def set_style(self, skill):
        """Open the Combat tab and click the attack-style button that trains `skill`."""
        slot = self.style_slots.get(skill)
        if slot is None or slot >= len(STYLE_BUTTONS):
            log.warning("No attack-style button configured for %s", skill)
            return False
        self.state = f"style: {skill}"
        actions.open_tab(self.ctx, "combat")
        x, y = STYLE_BUTTONS[slot]
        self.inp.click(x + random.randint(-6, 6), y + random.randint(-4, 4))
        self.sleep(0.5)
        actions.open_tab(self.ctx, "inventory")
        self.training = skill
        log.info("Training %s (style button %d)", skill, slot + 1)
        return True

    # ---- finding and attacking -------------------------------------------------------
    # ---- targets from the game's NPC list --------------------------------------------
    def _gs_call(self, fn, *a):
        from lumberjack.core.gamestate import tolerant_call
        return tolerant_call(self, fn, *a, logger=log)

    def _targets_gs(self):
        """[(npc, me)]: monsters we want, attackable, not fighting another player; nearest first."""
        from lumberjack.core.backpack import key
        me = self.gs.player()
        mine = 32768 + me.get("index", -1)
        out = []
        skip = getattr(self, "skip_until", None) or {}
        now = time.monotonic()
        for n in self.gs.npcs():
            if key(n["name"]) not in self.targets or "Attack" not in n["ops"]:
                continue
            if n["interacting"] >= 32768 and n["interacting"] != mine:
                continue                       # someone else's fight
            if skip.get(n["index"], 0) > now and n["interacting"] != mine:
                continue                       # couldn't get a fight going with it just now
            out.append(n)
        out.sort(key=lambda n: (n["interacting"] != mine, n["dist"]))   # ones already on us first
        return out, me

    def attack_nearest_gs(self):
        from lumberjack.core.backpack import key
        from lumberjack.core.gamestate import menu_row_point, top_entry
        targets, _ = self._targets_gs()
        for n in targets[:MAX_CANDIDATES]:
            (gx, gy), (bx, by) = n["screen"], n["body"]
            for x, y in ((bx, by), ((gx + bx) // 2, (gy + by) // 2), (gx, gy - 4)):
                if not npcs.in_view(x, y, margin=4):
                    continue
                self.inp.move(x + random.randint(-2, 2), y + random.randint(-2, 2))
                self.sleep(random.uniform(0.08, 0.14))
                menu = self.gs.menu()
                top = top_entry(menu)
                hit = lambda e: e and e["verb"] == "Attack" and key(e["subject"].split("(level")[0]) == key(n["name"])
                if hit(top):
                    self.inp.click()
                elif any(hit(e) for e in menu.get("entries") or []):
                    px, py = self.inp.pos           # Attack is in the menu but not on top: pick its row
                    self.inp.right_click(px, py)
                    self.sleep(random.uniform(0.2, 0.3))
                    menu = self.gs.menu()
                    e = next((e for e in menu.get("entries") or [] if hit(e)), None)
                    if not (menu.get("open") and e):
                        self.inp.move(px, max(R.VIEWPORT.y + 30, py - 90))
                        continue
                    rx, ry = menu_row_point(menu, e["row"])
                    self.inp.click(rx + random.randint(-12, 12), ry + random.randint(-1, 1))
                else:
                    continue
                self.target_index = n["index"]
                self.last_click = self.kill_point = (x, y)
                log.info("Attacking %s (%d tile(s) away)", n["name"], n["dist"])
                return key(n["name"])
        return None

    UNDER_ATTACK_WAIT_S = 6.0

    def waiting_out_combat(self):
        """We're in combat but can't tell with what yet: attacking anything else only gets
        "You're already under attack!" - wait a moment (bounded) for the attacker to show."""
        from lumberjack.core.gamestate import GameStateError
        try:
            busy = self.gs.player().get("in_combat")
        except GameStateError:
            return False
        if not busy:
            self.under_attack_since = None
            return False
        now = time.monotonic()
        since = getattr(self, "under_attack_since", None) or now
        self.under_attack_since = since
        if now - since > self.UNDER_ATTACK_WAIT_S:
            return False
        self.state = "under attack - waiting to see by what"
        self.sleep(0.6)
        return True

    def attacker_gs(self):
        """NpcList index of a monster that's fighting us right now, or None."""
        me = self.gs.player()
        if not me.get("in_combat"):
            return None
        mine = 32768 + me.get("index", -1)
        for n in self.gs.npcs():
            if n["interacting"] == mine or n["index"] == me.get("interacting"):
                return n["index"]
        return None

    def fight_gs(self):
        """Follow the fight in the game's data: it's a kill when our target disappears from
        the NPC list after we fought it (or its health hit 0)."""
        start = last_fight = time.monotonic()
        engaged, low = False, None
        while time.monotonic() - start < FIGHT_TIMEOUT:
            now = time.monotonic()
            me = self.gs.player()
            mine = 32768 + me.get("index", -1)
            npc = next((n for n in self.gs.npcs() if n["index"] == self.target_index), None)
            self.ensure_hp(self.grab())
            if npc is None:
                return "kill" if engaged else "no_engage"
            self.kill_tile = tuple(npc["tile"])
            if self.npcs_screen_ok(npc):
                self.kill_point = tuple(npc["screen"])
            theirs = npc["interacting"] >= 32768 and npc["interacting"] != mine
            if theirs and not engaged:
                return "taken"                 # another player got to it first
            # it faces whoever fights it: facing someone else, its health bar isn't ours
            fighting = npc["interacting"] == mine or (me.get("interacting") == npc["index"] and not theirs)
            if fighting and (npc["in_combat"] or me.get("in_combat")):
                engaged, last_fight = True, now
                low = npc["hp_bar"] if low is None else min(low, npc["hp_bar"])
            if engaged and npc["hp_bar"] == 0 and npc["in_combat"]:
                # dying: the drop appears when the death animation ends and it vanishes
                end = time.monotonic() + DEATH_WAIT
                while time.monotonic() < end and any(n["index"] == self.target_index for n in self.gs.npcs()):
                    self.sleep(0.2)
                return "kill"
            if engaged and not fighting and now - last_fight > COMBAT_GONE_S:
                return "ended"
            if not engaged and now - start > ENGAGE_TIMEOUT and not me.get("moving"):
                return "no_engage"
            if actions.dismiss_dialog(self.ctx):
                self.check_levels()
            self.sleep(0.25)
        return "ended"

    @staticmethod
    def npcs_screen_ok(n):
        return npcs.in_view(*n["screen"], margin=4)

    def walk_toward_npc_gs(self):
        """Click the minimap toward the nearest wanted monster (by tile, turned to the compass)."""
        from lumberjack.core import interact
        targets, me = self._targets_gs()
        far = [n for n in targets if n["dist"] <= 30]
        if not far:
            return False
        n = far[0]
        self.state = "walking toward a target"
        log.info("Walking toward a %s %d tile(s) away", n["name"], n["dist"])
        interact.walk_toward(self.ctx, me["tile"], n["tile"])
        self._wait_walk()
        return True

    def attack_nearest(self, frame):
        """Hover NPC candidates; click the first whose hover reads 'Attack <target>'."""
        if self.gs:
            r = self._gs_call(self.attack_nearest_gs)
            if self.gs:
                return r
        now = time.monotonic()
        self.bad_spots = [b for b in self.bad_spots if b[2] > now]
        cands = npcs.find_npcs(frame, self.prev_frame)
        self.prev_frame = frame
        for c in cands[:MAX_CANDIDATES]:
            if any(abs(c.x - bx) < 18 and abs(c.y - by) < 18 for bx, by, _ in self.bad_spots):
                continue
            for px, py in c.probe_points():
                if not npcs.in_view(px, py, margin=4):
                    continue
                self.inp.move(px, py)
                self.sleep(random.uniform(0.12, 0.2))
                target = npcs.hover_target(self.grab(), self.targets)
                if target:
                    self.inp.click()
                    self.last_click = (px, py)
                    log.info("Attacking %s at (%d, %d)", target, px, py)
                    return target
            self.bad_spots.append((c.x, c.y, now + BAD_SPOT_S))
        return None

    def fight(self, engaged=False):
        """Wait while we walk up and fight, until the target dies or the fight fizzles."""
        if self.gs and self.target_index is not None:
            result = self._gs_call(self.fight_gs)
            if result is not None:
                if result in ("no_engage", "taken"):    # (fenced off, out of reach, someone else's)
                    if not hasattr(self, "skip_until"):
                        self.skip_until = {}
                    self.skip_until[self.target_index] = time.monotonic() + SKIP_TARGET_S
                if result == "no_engage" and getattr(self, "train", None) == "ranged":
                    self.equip_ranged()                  # out of arrows? (no fight starts without them)
                self.target_index = None
                if result == "no_engage":
                    log.info("Didn't get into a fight - trying another target")
                elif result == "taken":
                    self.taken = getattr(self, "taken", 0) + 1
                    log.info("Another player is fighting it - trying another target")
                elif result == "ended":
                    log.info("Fight ended without a kill")
                else:
                    self._count_kill()
                    self.sleep(SERVER_TICK)
                    self.after_kill()
                self.last_click = self.kill_point = self.kill_tile = None
                return result
        self.activity.reset()
        start = last_seen = time.monotonic()
        min_target = None
        seen_target = False
        while time.monotonic() - start < FIGHT_TIMEOUT:
            frame = self.grab()
            self.activity.update(frame)
            now = time.monotonic()
            bars = health.find_bars(frame)
            tb = health.target_bar(frame, bars, near=self.last_click)
            if tb is not None:
                self.kill_point = (tb.center[0], tb.center[1] + 30)   # bar floats over the body
                seen_target = True
                min_target = tb.frac if min_target is None else min(min_target, tb.frac)
            if health.in_combat(frame, bars):
                engaged = True
                last_seen = now
            if engaged:
                self.ensure_hp(frame)
                # the target's bar ran out and is gone -> it died
                if seen_target and tb is None and min_target is not None and min_target <= KILL_FRAC:
                    break
                if now - last_seen > COMBAT_GONE_S and not self.activity.active:
                    break
            elif now - start > ENGAGE_TIMEOUT and not self.activity.active:
                break
            self.sleep(0.2)
        result = fight_result(engaged, min_target)
        if result == "no_engage":
            log.info("Didn't get into a fight - trying another target")
            if self.last_click:
                self.bad_spots.append((*self.last_click, time.monotonic() + BAD_SPOT_S))
        else:
            self._count_kill()
            self.sleep(LOOT_SETTLE)
            self.after_kill()
        self.last_click = self.kill_point = None
        return result

    def after_kill(self):
        if self.loot_items:
            self.loot()
        if self.bury_bones:
            self.bury()

    def loot_gs(self):
        """Pick up wanted drops from the game's ground-item list: items on (or next to) the
        tile the target died on, nearest first; 'Take <item>' confirmed in the menu entries."""
        from lumberjack.core.backpack import key
        from lumberjack.core.gamestate import find_entry, menu_row_point, top_entry
        wanted = set(self.loot_items)
        picked = 0
        self.state = "looting"
        unloaded = False
        while picked < LOOT_PICKS:
            items = [g for g in self.gs.ground(LOOT_RADIUS) if key(g["name"]) in wanted]
            if self.kill_tile:   # only the drop pile (+1 tile for big monsters), not everything around
                items = [g for g in items if max(abs(g["tile"][0] - self.kill_tile[0]),
                                                 abs(g["tile"][1] - self.kill_tile[1])) <= 1]
            items = [g for g in items if npcs.in_view(*g["screen"], margin=4)]
            if not items:
                break
            if inventory.count(self.grab()) >= 28 and not any(g["id"] in self._stackable_ids() for g in items):
                if self.bury_bones:
                    self.bury()
                if inventory.count(self.grab()) >= 28:
                    if unloaded or not self.unload_loot():
                        log.info("Backpack full - not looting")
                        break
                    unloaded = True
                    continue                      # we moved: look at the pile again
            g = items[0]
            x, y = g["screen"]
            self.inp.move(x + random.randint(-2, 2), y + random.randint(-2, 2))
            self.sleep(random.uniform(0.08, 0.14))
            menu = self.gs.menu()
            top, e = top_entry(menu), find_entry(menu, "Take", g["name"])
            if top and top is e:
                self.inp.click()
            elif e:                                   # under other options: pick its menu row
                self.inp.right_click(x, y)
                self.sleep(random.uniform(0.2, 0.3))
                menu = self.gs.menu()
                e = find_entry(menu, "Take", g["name"])
                if not (menu.get("open") and e):
                    self.inp.move(x, max(R.VIEWPORT.y + 30, y - 90))
                    break
                rx, ry = menu_row_point(menu, e["row"])
                self.inp.click(rx + random.randint(-12, 12), ry + random.randint(-1, 1))
            else:
                break
            same = lambda lst: sum(1 for h in lst if h["tile"] == g["tile"] and h["id"] == g["id"])
            before = same(items)
            end = time.monotonic() + PICKUP_WAIT          # walk over + pick up: wait until it's gone
            while time.monotonic() < end:
                self.sleep(0.25)
                if same(self.gs.ground(LOOT_RADIUS)) < before:
                    break
            picked += 1
            self.looted += 1
            log.info("Picked up %s", g["name"] + (f" x{g['count']}" if g["count"] > 1 else ""))
        self.inp.move(260, 300)

    def _stackable_ids(self):
        """Item ids already in the backpack: taking more of a stackable (coins, feathers) needs
        no free slot."""
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        return {s["id"] for s in inv if s["id"] >= 0 and s["count"] > 1}

    def loot(self):
        """Pick up wanted drops: hover around where the target died and around us for
        "Take <item>", click, wait for the backpack to change, repeat for the rest of the pile."""
        if self.gs:
            self._gs_call(self.loot_gs)
            if self.gs:
                return          # done - only if game data dropped out mid-loot do pixels take over
        if not mouseover.available(TAKE_ACTION):
            return
        wanted = [i for i in self.loot_items if mouseover.available(i)]
        if not wanted:
            return
        self.state = "looting"
        picked, kill_point = 0, self.kill_point
        while picked < LOOT_PICKS:
            if inventory.count(self.grab()) >= 28:
                if self.bury_bones:
                    self.bury()
                if inventory.count(self.grab()) >= 28:
                    log.info("Backpack full - not looting")
                    break
            item = None
            for x, y in loot_points(kill_point):
                self.inp.move(x + random.randint(-2, 2), y + random.randint(-2, 2))
                self.sleep(random.uniform(0.15, 0.22))
                item = mouseover.which(self.grab(), TAKE_ACTION, wanted, "orange")
                if item:
                    break
            if not item:
                break
            before = inventory.count(self.grab())
            self.inp.click()
            end = time.monotonic() + PICKUP_WAIT
            while time.monotonic() < end and inventory.count(self.grab()) == before:
                self.sleep(0.2)
            picked += 1
            self.looted += 1
            log.info("Picked up %s", item.replace("_", " "))
            self.sleep(SERVER_TICK)
            kill_point = None      # we walked onto the pile: search around us from now on
        self.inp.move(260, 300)

    def unload_loot(self):
        """Backpack full of loot (game data): bank it at the nearest booth and come back, else drop
        it. Food, tools and anything not known as loot stay. True if room was made."""
        from lumberjack import bank
        from lumberjack.core import backpack
        inv = backpack.slots()
        if inv is None:
            return False
        loot = [i for i, s in enumerate(inv) if s["id"] >= 0 and backpack.kind(s["key"]) in ("loot", "raw", "burnt")]
        if not loot:
            return False
        keep = {i for i, s in enumerate(inv) if s["id"] >= 0} - set(loot)
        self.state = "banking loot"
        if bank.gs_bank_trip(self.ctx, keep_slots=keep):
            log.info("Banked %d loot item(s)", len(loot))
            self.banked += 1
        else:
            log.info("No bank nearby - dropping %d loot item(s)", len(loot))
            actions.drop_known(self.ctx, self.gs, loot)     # known by name: the hover check refused some
        return True

    def bury(self):
        """Bury any bones in the backpack ('Bury Bones' hover). Needs bury/bones templates."""
        from lumberjack.core import backpack
        inv = backpack.slots()
        if inv is not None:                # by name: click each bones slot (left-click is Bury)
            actions.open_tab(self.ctx, "inventory")
            for i, s in enumerate(inv):
                if s["key"] in ("bones", "big_bones", "burnt_bones", "wolf_bones", "bat_bones"):
                    self.inp.click(*R.INV_SLOTS[i].center)
                    self.sleep(SERVER_TICK * 2)
            self.inp.move(260, 300)
            return
        if not (mouseover.available("bury") and mouseover.available("bones")):
            return
        actions.open_tab(self.ctx, "inventory")
        occ = inventory.occupied(self.grab())
        for i, o in enumerate(occ):
            if not o or (self.food_slots and i in self.food_slots):
                continue
            self.inp.move(*R.INV_SLOTS[i].center, steps=2)
            self.sleep(0.12)
            if mouseover.is_text(self.grab(), "bury", "bones", "orange"):
                self.inp.click()
                self.sleep(SERVER_TICK * 2)
        self.inp.move(260, 300)

    # ---- nothing to attack -----------------------------------------------------------
    def no_target(self):
        self.empty_rounds += 1
        self.state = "looking for targets"
        if self.empty_rounds >= GIVE_UP_ROUNDS:
            raise StopBot(f"nothing to attack for a long time ({'/'.join(self.targets)})")
        if self.empty_rounds < EMPTY_ROUNDS_BEFORE_WALK:
            self.rotate_camera()
            return
        if self.approach_walks < APPROACH_WALKS and self.walk_toward_npc():
            self.approach_walks += 1
            return
        if self.walker and self.walk_to_next_spot():
            self.approach_walks = 0
            return
        self.state = "waiting for respawn"
        self.sleep(4.0)
        self.approach_walks = 0

    def rotate_camera(self):
        """A quarter turn: brings NPCs at the screen's narrow top/bottom into the wide view."""
        actions.rotate_camera(self.ctx)   # exact quarter turn via the game's camera, else arrow key

    def walk_toward_npc(self):
        """Click the minimap toward a random nearby NPC dot outside the view."""
        if self.gs:
            r = self._gs_call(self.walk_toward_npc_gs)
            if self.gs:
                return r
        dots = npcs.far_dots(self.grab())
        if not dots:
            return False
        dx, dy = random.choice(dots[:3])
        x, y = npcs.minimap_click_point(dx, dy)
        self.state = "walking toward NPCs"
        log.info("No target in view - walking toward an NPC on the minimap")
        self.inp.click(x, y)
        self._wait_walk()
        return True

    def _wait_walk(self, timeout=8.0):
        self.activity.reset()
        start = time.monotonic()
        self.sleep(0.6)
        while time.monotonic() - start < timeout:
            self.activity.update(self.grab())
            if self.activity.filled and not self.activity.active:
                break
            self.sleep(0.2)

    def _fight_spots(self):
        import json
        from lumberjack.nav.worldmap import MAPS
        wm = self.walker.map
        try:  # pick up spots saved from the control panel while we were running
            wm.spots = json.loads((MAPS / f"{wm.name}.json").read_text()).get("spots", {})
        except (OSError, ValueError):
            pass
        if self.fight_spots is not None:
            return [(n, s) for n, s in wm.spots.items() if n in self.fight_spots]
        return [(n, s) for n, s in wm.spots.items() if s.get("kind") == "combat"]

    def walk_to_next_spot(self):
        """Walk to the next saved combat spot (round-robin), skipping the one we're at."""
        spots = self._fight_spots()
        if not spots:
            return False
        here = self.walker.where()
        for _ in range(len(spots)):
            name, s = spots[self.spot_turn % len(spots)]
            self.spot_turn += 1
            if here and math.hypot(s["x"] - here.x, s["y"] - here.y) < 25 and len(spots) > 1:
                continue
            self.state = f"walking to {name}"
            log.info("Nothing to attack here - walking to '%s'", name)
            if self.walker.walk_to(s["x"], s["y"]):
                actions.reset_camera(self.ctx)
                return True
            log.warning("Couldn't reach '%s'", name)
        return False


# ---- calibration helpers (call from the control panel's manual runner) --------------------
def _save_orange(strip, name):
    """Save the orange item name in a hover strip as mouseover/<name>.png (a variant if known)."""
    if mouseover.available(name):
        mouseover.add_variant(strip, name, "orange")
        return
    col = mouseover.strip_mask(strip, "orange")
    cols = np.where(col.any(axis=0))[0]
    word = mouseover._trim(col[:, cols.min():cols.max() + 1])
    mouseover.TEMPLATES.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(mouseover.TEMPLATES / f"{name}.png"), word)
    mouseover._cache.pop(name, None)
    mouseover._variants_cache.pop(name, None)


def _discard(name):
    p = mouseover.TEMPLATES / f"{name}.png"
    if p.exists():
        p.unlink()
    mouseover._cache.pop(name, None)
    mouseover._variants_cache.pop(name, None)


def learn_loot(ctx, name):
    """Stand on or next to ONE ground item (e.g. bones a chicken dropped): hover our tile and
    the tiles around it until the text reads "Take <orange name>", then save the name as
    `name` (and the 'Take' verb if it's new)."""
    for x, y in loot_points():
        ctx.inp.move(x, y)
        ctx.sleep(0.25)
        strip = R.MOUSEOVER_TEXT.crop(ctx.grab())
        if mouseover.strip_mask(strip, "orange").sum() // 255 < 10:
            continue
        if mouseover.available(TAKE_ACTION):
            if not mouseover.action_ok(strip, TAKE_ACTION):
                continue
        else:
            mouseover.save_word_templates(None, TAKE_ACTION, "_tmp_loot", "orange", strip=strip)
            _discard("_tmp_loot")
            log.info("Learned the 'Take' action")
        try:
            _save_orange(strip, name)
        except ValueError as e:
            log.warning("Not learning that as %s: %s", name.replace("_", " "), e)
            ctx.inp.move(260, 300)
            return False
        log.info("Learned ground item '%s' at (%d, %d)", name, x, y)
        ctx.inp.move(260, 300)
        return True
    ctx.inp.move(260, 300)
    log.warning("No 'Take <item>' hover found - stand on or right next to the item and retry")
    return False


def learn_bury(ctx):
    """With bones in the backpack (and 'bones' learned from the ground): hover the backpack
    until the text reads "<verb> Bones" and save the verb as 'bury'."""
    if not mouseover.available("bones"):
        log.warning("Learn the ground item 'bones' first")
        return False
    actions.open_tab(ctx, "inventory")
    for i, occ in enumerate(inventory.occupied(ctx.grab())):
        if not occ:
            continue
        ctx.inp.move(*R.INV_SLOTS[i].center, steps=2)
        ctx.sleep(0.25)
        frame = ctx.grab()
        strip = R.MOUSEOVER_TEXT.crop(frame)
        if not mouseover._ok(mouseover.word_score(frame, "bones", "orange")):
            continue
        mouseover.save_word_templates(None, "bury", "_tmp_bones", "orange", strip=strip)
        _discard("_tmp_bones")
        log.info("Learned 'Bury' from slot %d", i + 1)
        ctx.inp.move(260, 300)
        return True
    ctx.inp.move(260, 300)
    log.warning("No bones in the backpack - pick some up first")
    return False
