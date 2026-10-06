"""Skills trained on spawned supplies (singleplayer admin ::item), nonstop, no bank:

    Prayer     spawn bones           -> bury them all
    Fletching  knife + logs          -> the best bow (u) / arrow shafts the level makes
    Crafting   chisel + uncut gems   -> cut them ("Make All" in the make box)
    Smithing   hammer + bars         -> daggers at an anvil (stand by one: ★ Varrock anvil)

SupplyTask is the loop they share (Firemaking and Cooking do the same in their own modules):

    tools present? (spawned)  ->  supplies left?  no: drop the products, walk back to where
    we started, spawn a backpack of the best supply for the level  ->  process them  ->  repeat

All of them need the game's own data (items by name) and 'Spawn missing tools'.
"""
import logging
import random
import time

from lumberjack import actions, items
from lumberjack.skills.base import BotBase, StopBot

MAX_STALLS = 4             # process rounds in a row that used nothing -> try a lower supply
MAX_UNBLOCKS = 3           # nothing lower left: give the blocked supplies another go (a hiccup, not a wall)


class SupplyTask(BotBase):
    skill = None           # the skill whose level picks the supply
    TOOLS = ()             # item keys spawned when missing (and never dropped)
    SUPPLY = {}            # supply item key -> level needed

    def __init__(self, spawn_tools=True, **kw):
        kw.pop("keep_carried", None)
        super().__init__(keep_carried=False, **kw)    # tools are known by name
        self.spawn_tools = spawn_tools
        self.done = 0
        self.home = None
        self.current = None
        self.blocked = set()
        self.unblocks = 0

    def progress(self):
        return self.done

    def stats(self):
        return f"{self.done} {self.unit}"

    unit = "done"

    # ---- what each skill fills in ----------------------------------------------------
    def process(self, inv, supply_slots, tools):
        """Use the supplies up. Returns how many were used (0 = nothing happened)."""
        raise NotImplementedError

    def best_supply(self, level):
        ok = [k for k, need in self.SUPPLY.items() if need <= level and k not in self.blocked]
        return max(ok, key=lambda k: (self.SUPPLY[k], k)) if ok else None

    # ---- the loop ----------------------------------------------------------------------
    def run(self):
        self.log.info("%s started - press F12 to stop", self.name.capitalize())
        try:
            self.prepare()
            from lumberjack.core import backpack, gamestate
            self.gs = gamestate.shared()
            if self.gs is None or not self.spawn_tools:
                raise StopBot(f"{self.name.capitalize()} needs the game's own data and 'Spawn missing tools' "
                              "(it spawns its supplies)")
            self.home = self.gs.player()["tile"]
            self.setup()
            stalls = 0
            while True:
                self.check_stop()
                tools = self.ensure_tools()
                inv = backpack.slots() or []
                supply = self.supply_slots(inv)
                if not supply:
                    self.restock(inv, tools)
                    continue
                self.state = f"{self.name}: {(self.current or inv[supply[0]]['key']).replace('_', ' ')}"
                used = self.process(inv, supply, tools)
                if used:
                    self.done += used
                    stalls = 0
                    continue
                stalls += 1
                if stalls >= MAX_STALLS:
                    kind = self.supply_kind(inv, supply)
                    self.log.warning("Nothing happened with %s %d times - trying a lower one",
                                     kind.replace("_", " "), stalls)
                    self.blocked.add(kind)
                    actions.drop_known(self.ctx, self.gs, self.leftovers(inv, supply))
                    self.current = None
                    stalls = 0
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            try:
                self.teardown()
            except Exception as e:              # the game went away: nothing to tidy
                self.log.debug("teardown: %s", e)
            self.state = "stopped"
            self.inp.close()

    def keep(self, key):
        """Products worth keeping through a restock (they stack: one slot)."""
        return False

    def setup(self):
        """Once at the start, after the game data is there (e.g. unlock the skill)."""

    def teardown(self):
        """Once at the end, however the run ended (e.g. leave a place only teleports reach)."""

    def supply_slots(self, inv):
        """The slots of the supply to process now ([] = restock)."""
        return [i for i, s in enumerate(inv) if s["key"] in self.SUPPLY and s["key"] not in self.blocked]

    def supply_kind(self, inv, supply):
        """What gets blocked when processing keeps doing nothing."""
        return inv[supply[0]]["key"]

    def leftovers(self, inv, supply):
        """Slots dropped along with a blocked supply."""
        return supply

    def spawn_supply(self, kind):
        """Fill the backpack with `kind` (best_supply's pick). False if nothing arrived."""
        return bool(items.fill(self.ctx, kind, 28))

    def ensure_tools(self):
        """{tool key: slot}, spawning what's missing."""
        from lumberjack.core import backpack
        out = {}
        for key in self.TOOLS:
            items.ensure(self.ctx, lambda k, key=key: k == key, key, log=self.log)
            inv = backpack.slots() or []
            slot = next((i for i, s in enumerate(inv) if s["key"] == key), None)
            if slot is None:
                raise StopBot(f"no {key.replace('_', ' ')} and couldn't spawn one")
            out[key] = slot
        return out

    def restock(self, inv, tools):
        """Drop what the last load made (everything but tools, coins and food), go back to
        where we started, spawn a backpack of the best supply."""
        from lumberjack.core import backpack, gamestate
        junk = [i for i, s in enumerate(inv) if s["id"] >= 0 and i not in tools.values()
                and s["key"] != "coins" and backpack.kind(s["key"]) != "cooked" and not self.keep(s["key"])]
        if junk:
            self.state = "dropping"
            actions.drop_known(self.ctx, self.gs, junk)
        self.walk_home()
        lv = (gamestate.skill(self.skill) or {}).get("base", 1)
        self.current = self.best_supply(lv)
        if self.current is None and self.blocked and self.done and self.unblocks < MAX_UNBLOCKS:
            # it worked earlier this run: a dialog or a missed click stalled it, not the level
            self.unblocks += 1
            self.log.info("Giving %s another go (it worked earlier)", ", ".join(sorted(self.blocked)).replace("_", " "))
            self.blocked.clear()
            self.current = self.best_supply(lv)
        if self.current is None:
            raise StopBot(f"nothing left to train {self.skill} on at level {lv}")
        self.state = f"spawning {self.current.replace('_', ' ')}"
        if not self.spawn_supply(self.current):
            raise StopBot("couldn't spawn supplies (backpack full of other things?)")

    def walk_home(self):
        from lumberjack.core import interact
        me = self.gs.player()["tile"]
        if self.home and max(abs(me[0] - self.home[0]), abs(me[1] - self.home[1])) > 4:
            self.state = "walking back"
            interact.walk_to_tile(self.ctx, self.gs, self.home, arrive=1)

    def count(self, keys):
        from lumberjack.core import backpack
        return sum(1 for s in backpack.slots() or [] if s["key"] in keys)

    def wait_used(self, keys, still_s=4.0, limit_s=90.0, start=None):
        """Wait while items in `keys` keep disappearing (a make-all / burying in progress).
        Returns how many went since `start` (the count before the action; default: now). Stops
        early on a level-up dialog (the caller goes round again)."""
        last = self.count(keys)
        start = last if start is None else start
        quiet = time.monotonic()
        end = time.monotonic() + limit_s
        while time.monotonic() < end and last:
            self.sleep(0.6)
            if actions.dismiss_dialog(self.ctx):
                break
            now = self.count(keys)
            if now < last:
                last, quiet = now, time.monotonic()
            elif time.monotonic() - quiet > still_s:
                break
        return start - last


# ---- Prayer ---------------------------------------------------------------------------------
class Prayer(SupplyTask):
    """Bury spawned bones. Dragon bones give the most XP and need no level."""
    name = "prayer"
    skill = "prayer"
    unit = "bones buried"
    SUPPLY = {"bones": 1, "big_bones": 1, "dragon_bones": 1}
    BURY_DELAY = (1.1, 1.3)    # one bone per ~2 ticks

    def best_supply(self, level):
        for k in ("dragon_bones", "big_bones", "bones"):
            if k not in self.blocked:
                return k
        return None

    def process(self, inv, supply_slots, tools):
        import random
        buried = 0
        for i in supply_slots:
            self.check_stop()
            if actions.use_slot(self.ctx, self.gs, i, "Bury"):
                buried += 1
                self.sleep(random.uniform(*self.BURY_DELAY))
            actions.dismiss_dialog(self.ctx)
        self.inp.move(260, 300)
        return buried


# ---- Fletching ------------------------------------------------------------------------------
class Fletcher(SupplyTask):
    """Knife on spawned logs -> the best bow (u) (arrow shafts below level 5)."""
    name = "fletching"
    skill = "fletching"
    unit = "logs fletched"
    TOOLS = ("knife",)
    # logs -> the level its first bow needs (logs: arrow shafts from 1)
    SUPPLY = {"logs": 1, "oak_logs": 20, "willow_logs": 35, "maple_logs": 50, "yew_logs": 65, "magic_logs": 80}

    def process(self, inv, supply_slots, tools):
        from lumberjack.core import gamestate
        from lumberjack.skills import fletching
        kind = inv[supply_slots[0]]["key"]
        lv = (gamestate.skill("fletching") or {}).get("base", 1)
        product = fletching.best_product(kind, lv)
        if product is None:
            return 0
        self.state = f"fletching {product.replace('_', ' ')}"
        return fletching.fletch_type(self.ctx, tools["knife"], kind, product, keep=set(tools.values()))


# ---- Crafting: gem cutting --------------------------------------------------------------------
GEMS = {"uncut_opal": 1, "uncut_jade": 13, "uncut_red_topaz": 16, "uncut_sapphire": 20,
        "uncut_emerald": 27, "uncut_ruby": 34, "uncut_diamond": 43, "uncut_dragonstone": 55}


class Crafter(SupplyTask):
    """Chisel on spawned uncut gems -> Make All."""
    name = "crafting"
    skill = "crafting"
    unit = "gems cut"
    TOOLS = ("chisel",)
    SUPPLY = GEMS

    def process(self, inv, supply_slots, tools):
        kind = inv[supply_slots[0]]["key"]
        before = self.count({kind})
        if not (actions.use_slot(self.ctx, self.gs, tools["chisel"], "Use")
                and actions.use_slot(self.ctx, self.gs, supply_slots[0], "Use")):
            actions.cancel_selection(self.ctx, force=True)
            return 0
        if not make_all(self.ctx, self.gs):
            return self.wait_used({kind}, still_s=2.0, start=before)   # a single gem cuts straight away
        self.inp.move(260, 300)
        return self.wait_used({kind}, start=before)


# ---- Smithing --------------------------------------------------------------------------------
class Smither(SupplyTask):
    """Hammer + spawned bars on an anvil -> daggers (one bar each; XP per bar is the same for
    every item of a metal). Needs an anvil within a few tiles - the route's place is one."""
    name = "smithing"
    skill = "smithing"
    unit = "bars smithed"
    TOOLS = ("hammer",)
    SUPPLY = {"bronze_bar": 1, "iron_bar": 15, "steel_bar": 30, "mithril_bar": 50,
              "adamantite_bar": 70, "runite_bar": 85}
    PRODUCT = "dagger"
    ANVIL_RADIUS = 10

    def process(self, inv, supply_slots, tools):
        from lumberjack.core import interact
        from lumberjack.core.gamestate import top_entry
        from lumberjack.ui import widgets
        kind = inv[supply_slots[0]]["key"]
        before = self.count({kind})
        anvils = [l for l in self.gs.locs(self.ANVIL_RADIUS, "anvil") if l["name"] == "Anvil"]
        if not anvils:
            raise StopBot("no anvil nearby - start next to one (e.g. ★ Varrock anvil)")
        if not actions.use_slot(self.ctx, self.gs, supply_slots[0], "Use"):
            return 0
        clicked = False
        for a in anvils[:2]:
            for px, py in interact.points_for(a):
                if not interact.on_screen(px, py):
                    continue
                self.inp.move(px, py)
                self.sleep(0.15)
                top = top_entry(self.gs.menu())
                if top and top["verb"] == "Use" and top["subject"].lower().endswith("anvil"):
                    self.inp.click()
                    clicked = True
                    break
            if clicked:
                break
        if not clicked:
            actions.cancel_selection(self.ctx, force=True)
            return 0
        end = time.monotonic() + 6                  # we may walk to the anvil first
        while time.monotonic() < end:
            self.sleep(0.4)
            if widgets.make(self.ctx, self.gs, product=self.PRODUCT, amounts=("All", "10", "5")) \
                    or self.click_smith_all():
                self.inp.move(260, 300)
                used = self.wait_used({kind}, still_s=6.0, start=before)
                if used == 1 and before > 2 and not self.smith_by_menu:
                    # one bar per click: the left-click on that button is "Make 1" in this client
                    self.log.info("One bar per click - picking the amount from the right-click menu from now on")
                    self.smith_by_menu = True
                return used
        self.log.warning("The smithing screen never offered '%s' - %s", self.PRODUCT,
                         "it didn't open" if not self.smith_screen() else "its buttons didn't match")
        return 0

    SMITH_IF = 300              # the smithing screen
    SMITH_ALL_BUTTON = 21       # the dagger's "All" button (server: SmithingType.TYPE_DAGGER {24,23,22,21} = 1,5,X,All)

    def smith_screen(self):
        from lumberjack.ui import widgets
        return [w for w in widgets.find(self.gs, str(self.SMITH_IF)) if w.get("if") == self.SMITH_IF]

    smith_by_menu = False       # left-clicks made one bar each: right-click and pick the amount

    def click_smith_all(self):
        """The smithing screen is up but its buttons carry no 'Make ...' text: click the
        dagger's All button by its component number (or pick "All" from its right-click menu)."""
        from lumberjack.ui import widgets
        btn = next((w for w in self.smith_screen() if w.get("idx") == self.SMITH_ALL_BUTTON and w["w"] > 0), None)
        if btn is None:
            return False
        x, y = widgets.center(btn)
        if self.smith_by_menu and self.pick_amount_from_menu(x, y):
            return True
        self.log.info("Smithing screen: clicking the dagger's All button")
        self.inp.click(x, y)
        return True

    def pick_amount_from_menu(self, x, y):
        """Right-click the button and pick the biggest amount on offer ("... All", else X/10/5)."""
        from lumberjack.core.gamestate import GameStateError, menu_row_point
        self.inp.right_click(x, y)
        self.sleep(0.3)
        try:
            m = self.gs.menu()
        except GameStateError:
            return False
        entries = [e for e in m.get("entries") or [] if e["verb"] not in ("Cancel", "Examine", "Walk here")]
        if not m.get("open") or not entries:
            self.inp.move(x, max(10, y - 120))
            return False
        if not getattr(self, "_menu_logged", False):
            self.log.info("Smithing menu: %s", ", ".join(e["verb"] for e in entries))
            self._menu_logged = True

        def rank(e):
            v = e["verb"].lower()
            return (4 if "all" in v else 3 if v.endswith(" x") or v == "x" else
                    2 if "10" in v else 1 if "5" in v else 0)
        e = max(entries, key=rank)
        rx, ry = menu_row_point(m, e["row"])
        self.log.info("Smithing screen: %s", e["verb"])
        self.inp.click(rx + random.randint(-10, 10), ry + random.randint(-1, 1))
        if rank(e) == 3:                         # "Make X" asks how many
            self.sleep(1.0)
            self.inp.type_text("28", enter=True)
        return True


# ---- Herblore ----------------------------------------------------------------------------------
# (level, potion, unfinished potion, secondary) - from the 2009scape server's FinishedPotion table
POTIONS = [
    (3, "attack_potion", "guam_potion_(unf)", "eye_of_newt"),
    (5, "antipoison", "marrentill_potion_(unf)", "unicorn_horn_dust"),
    (12, "strength_potion", "tarromin_potion_(unf)", "limpwurt_root"),
    (22, "restore_potion", "harralander_potion_(unf)", "red_spiders'_eggs"),
    (26, "energy_potion", "harralander_potion_(unf)", "chocolate_dust"),
    (30, "defence_potion", "ranarr_potion_(unf)", "white_berries"),
    (34, "agility_potion", "toadflax_potion_(unf)", "toad's_legs"),
    (36, "combat_potion", "harralander_potion_(unf)", "goat_horn_dust"),
    (38, "prayer_potion", "ranarr_potion_(unf)", "snape_grass"),
    (45, "super_attack", "irit_potion_(unf)", "eye_of_newt"),
    (48, "super_antipoison", "irit_potion_(unf)", "unicorn_horn_dust"),
    (50, "fishing_potion", "avantoe_potion_(unf)", "snape_grass"),
    (52, "super_energy", "avantoe_potion_(unf)", "mort_myre_fungus"),
    (55, "super_strength", "kwuarm_potion_(unf)", "limpwurt_root"),
    (60, "weapon_poison", "kwuarm_potion_(unf)", "dragon_scale_dust"),
    (63, "super_restore", "snapdragon_potion_(unf)", "red_spiders'_eggs"),
    (66, "super_defence", "cadantine_potion_(unf)", "white_berries"),
    (69, "antifire", "lantadyme_potion_(unf)", "dragon_scale_dust"),
    (72, "ranging_potion", "dwarf_weed_potion_(unf)", "wine_of_zamorak"),
    (76, "magic_potion", "lantadyme_potion_(unf)", "potato_cactus"),
    (78, "zamorak_brew", "torstol_potion_(unf)", "jangerberries"),
]
RECIPES = {name: (lv, unf, sec) for lv, name, unf, sec in POTIONS}
DRUIDIC_RITUAL = (48, 80, 4)          # quest index, its varp, the varp's value once complete
RITUAL_XP = 250                       # the quest's Herblore reward (Herblore needs level 3)


class Herbalist(SupplyTask):
    """Spawned unfinished potions + secondaries -> the best potion for the level (Make All).
    Herblore needs the Druidic Ritual quest: it's marked done with the admin quest command, and
    a level-1 account gets the quest's 250 XP the same way."""
    name = "herblore"
    skill = "herblore"
    unit = "potions made"
    SUPPLY = {name: lv for lv, name, _, _ in POTIONS}       # best_supply picks a recipe
    PER_LOAD = 14

    def setup(self):
        from lumberjack.core import gamestate
        quest, varp, done = DRUIDIC_RITUAL
        try:
            have = self.gs.varps(varp).get(varp)
        except gamestate.GameStateError:
            have = None                     # an older add-on: unlock anyway (harmless if done)
        if have != done:
            self.state = "unlocking Herblore"
            self.log.info("Herblore needs the Druidic Ritual quest - marking it done (::setqueststage %d 100)", quest)
            self.type_command(f"::setqueststage {quest} 100")
        if (gamestate.skill("herblore") or {}).get("base", 1) < 3:
            self.log.info("Adding the quest's %d Herblore XP (level 3 is the lowest anything needs)", RITUAL_XP)
            self.type_command(f"::addxp herblore {RITUAL_XP}")
            for _ in range(10):
                if (gamestate.skill("herblore") or {}).get("base", 1) >= 3:
                    break
                self.sleep(0.5)

    def type_command(self, text):
        self.inp.move(260, 300)
        self.inp.type_text(text, enter=True)
        self.sleep(1.5)

    def _slots(self, inv, key):
        iid = items.BY_KEY[key][1]
        return [i for i, s in enumerate(inv) if s["id"] == iid]

    def supply_slots(self, inv):
        if self.current not in RECIPES:
            return []
        _, unf, sec = RECIPES[self.current]
        u, s = self._slots(inv, unf), self._slots(inv, sec)
        return u + s if u and s else []

    def supply_kind(self, inv, supply):
        return self.current

    def spawn_supply(self, kind):
        _, unf, sec = RECIPES[kind]
        got = items.fill(self.ctx, unf, self.PER_LOAD)
        return bool(got and items.fill(self.ctx, sec, got))

    def process(self, inv, supply_slots, tools):
        _, unf, sec = RECIPES[self.current]
        u, s = self._slots(inv, unf), self._slots(inv, sec)
        before = len(u)
        if not (actions.use_slot(self.ctx, self.gs, s[0], "Use")
                and actions.use_slot(self.ctx, self.gs, u[0], "Use")):
            actions.cancel_selection(self.ctx, force=True)
            return 0
        count = lambda: len(self._slots(backpack_slots(), unf))
        if not make_all(self.ctx, self.gs):          # one of either: made straight away
            self.sleep(2.0)
            return before - count()
        self.inp.move(260, 300)
        end, last, quiet = time.monotonic() + 60, before, time.monotonic()
        while time.monotonic() < end and last:
            self.sleep(0.6)
            if actions.dismiss_dialog(self.ctx):
                break
            now = count()
            if now < last:
                last, quiet = now, time.monotonic()
            elif time.monotonic() - quiet > 4.0:
                break
        return before - last


# ---- Runecrafting ------------------------------------------------------------------------------
# (level, rune, the altar room's arrival tile) - the server's MysteriousRuins ends. Crafting at an
# altar has no quest check (Astral/Death/Blood aside, left out); only the ruins do, and an admin
# teleport goes past them. Chaos is left out too: its altar sits at the bottom of a maze.
ALTARS = [
    (1, "air_rune", (2841, 4829, 0)),
    (2, "mind_rune", (2793, 4828, 0)),
    (5, "water_rune", (3482, 4838, 0)),
    (9, "earth_rune", (2655, 4830, 0)),
    (14, "fire_rune", (2574, 4849, 0)),
    (20, "body_rune", (2521, 4834, 0)),
    (27, "cosmic_rune", (2162, 4833, 0)),
    (44, "nature_rune", (2400, 4835, 0)),
    (54, "law_rune", (2464, 4819, 0)),
]
ALTAR_RADIUS = 20
ALTAR_IDS = {2478, 2479, 2480, 2481, 2482, 2483, 2484, 2485, 2486}   # air ... nature (server: Altar.kt)


def altar_points(a):
    """Hover points over an altar: the game's own points first, then a grid around them."""
    from lumberjack.core import interact
    pts = list(interact.points_for(a))
    (gx, gy), (bx, by) = a["screen"], a.get("body", a["screen"])
    cx, cy = (gx + bx) // 2, (gy + by) // 2
    pts += [(cx + dx, cy + dy) for dy in (-24, 0, 24) for dx in (-30, 0, 30) if (dx, dy) != (0, 0)]
    return pts


def best_altar(level):
    return max((a for a in ALTARS if a[0] <= level), key=lambda a: a[0])


class Runecrafter(SupplyTask):
    """Teleport into the best altar room for the level, spawn pure essence, Craft-rune at the
    altar, repeat; teleport on to the next altar as the level allows, and back out at the end."""
    name = "runecrafting"
    skill = "runecrafting"
    unit = "essence crafted"
    SUPPLY = {"pure_essence": 1}

    def setup(self):
        from lumberjack.core import gamestate
        me = self.gs.player()
        self.came_from = (me["tile"][0], me["tile"][1], me.get("plane", 0))
        self.altar = None
        self.go_to_altar((gamestate.skill("runecrafting") or {}).get("base", 1))

    def go_to_altar(self, level):
        from lumberjack.nav import places
        altar = best_altar(level)
        if altar == self.altar:
            return
        lv, rune, (x, y, plane) = altar
        self.state = f"teleporting to the {rune.split('_')[0]} altar"
        self.log.info("Runecrafting %s runes (level %d+) - teleporting to the altar", rune.split("_")[0], lv)
        if not places.teleport(self.ctx, self.gs, (x, y), plane):
            raise StopBot(f"couldn't teleport to the {rune.split('_')[0]} altar (::tele {x} {y} {plane})")
        actions.reset_camera(self.ctx)              # the altar has to be on screen to click it
        self.altar, self.home = altar, [x, y]

    def restock(self, inv, tools):
        from lumberjack.core import gamestate
        self.go_to_altar((gamestate.skill("runecrafting") or {}).get("base", 1))
        super().restock(inv, tools)

    def process(self, inv, supply_slots, tools):
        from lumberjack.core import interact
        before = len(supply_slots)
        a = self.find_altar()
        if a is None:
            raise StopBot("no runecrafting altar here - the teleport went somewhere else")
        if not self.click_craft(a):
            self.log.info("Couldn't click Craft-rune on the altar (%d tiles away, menu: %s) - walking up to it",
                          a.get("dist", -1), self.seen_menu)
            interact.walk_to_tile(self.ctx, self.gs, a["tile"], arrive=1)
            a = self.find_altar() or a
            if not self.click_craft(a):
                self.log.warning("Still no Craft-rune on the altar (menu: %s)", self.seen_menu)
                return 0
        end = time.monotonic() + 15                 # walk over, then every essence at once
        while time.monotonic() < end:
            self.sleep(0.6)
            left = self.count({"pure_essence"})
            if left < before:
                self.sleep(0.6)
                return before - self.count({"pure_essence"})
        return 0

    def keep(self, key):
        return bool(key) and key.endswith("_rune")      # crafted runes stack - Magic can use them

    def click_craft(self, a):
        """Craft-rune on the altar: hover a spread of points over it (a big object's reported
        point can sit off its model - that hovers the floor: "Walk here"), take Craft-rune when
        it's on top, else right-click and pick it. True if clicked."""
        import random
        from lumberjack.core import interact
        from lumberjack.core.gamestate import menu_row_point, top_entry
        is_craft = lambda e: bool(e) and e["verb"].lower() == "craft-rune"
        self.seen_menu = None
        for x, y in altar_points(a):
            if not interact.on_screen(x, y):
                continue
            self.inp.move(x, y)
            self.sleep(random.uniform(0.1, 0.15))
            menu = self.gs.menu()
            top = top_entry(menu)
            self.seen_menu = f"{top['verb']} {top['subject']}" if top else None
            if is_craft(top):
                self.inp.click()
                return True
            e = next((e for e in menu.get("entries") or [] if is_craft(e)), None)
            if e:
                self.inp.right_click(x, y)
                self.sleep(random.uniform(0.2, 0.3))
                menu = self.gs.menu()
                e = next((e for e in menu.get("entries") or [] if is_craft(e)), None)
                if menu.get("open") and e:
                    rx, ry = menu_row_point(menu, e["row"])
                    self.inp.click(rx + random.randint(-10, 10), ry + random.randint(-1, 1))
                    return True
                self.inp.move(x, max(30, y - 90))
        return False

    def find_altar(self):
        """The altar in this room: by its object id (the server's Altar table) or by name +
        its Craft-rune option."""
        for l in self.gs.locs(ALTAR_RADIUS):
            ops = [o.lower() for o in l.get("ops") or []]
            if l.get("id") in ALTAR_IDS or (l["name"].lower() == "altar" and "craft-rune" in ops):
                return l
        return None

    def walk_home(self):
        pass                                        # the altar room is small: stay by the altar

    def teardown(self):
        """Stay put: a run also ends before every retry, and teleporting back to where it started
        sent us away from the spot each time. The next step travels by itself (Autopilot surfaces
        at Lumbridge first when it needs open ground)."""
        return



def backpack_slots():
    from lumberjack.core import backpack
    return backpack.slots() or []


def make_all(ctx, gs, wait_s=3.0):
    """The make box ("How many would you like to make?") is up: right-click the item picture and
    pick the '... All' entry (read from the game's menu, else the widest-text row). False if the
    box never showed."""
    import random
    from lumberjack.core.gamestate import menu_row_point
    from lumberjack.skills import cooking
    from lumberjack.ui import widgets
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        ctx.sleep(0.25)
        if widgets.make(ctx, gs):                 # the box's options read from the game
            return True
        if cooking.cook_box_open(ctx.grab()):
            break
    else:
        return False
    xy = cooking.sprite_center(ctx.grab())
    if not xy:
        return False
    ctx.inp.right_click(*xy)
    ctx.sleep(0.35)
    m = gs.menu()
    e = next((e for e in m.get("entries") or [] if e["verb"].endswith(" All") or e["verb"] == "All"), None)
    if m.get("open") and e:
        rx, ry = menu_row_point(m, e["row"])
        ctx.inp.click(rx + random.randint(-10, 10), ry + random.randint(-1, 1))
        return True
    ctx.inp.move(xy[0], xy[1] - 120)
    return cooking.choose_cook_all(ctx)


log = logging.getLogger("spawn_tasks")
