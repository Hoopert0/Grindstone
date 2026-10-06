"""Construction and Summoning, nonstop (game data + admin ::tele / ::item).

Construction - the player-owned house's garden (every house starts with one; the 2009scape
server's content/global/skill/construction):

    no house yet -> 1000 coins to the Varrock estate agent ("How can I get a house?" - "Yes please!")
    Rimmington house portal -> Enter -> "Go to your house (building mode)"
    every garden space (big tree, tree, two big plants, two small plants): Build -> the best item
    for the level in the furniture box (interface 396) -> then Remove each ("Really remove it?" Yes)
    An admin account builds without the bagged plants / watering can (the server skips the
    materials, tools and level checks for admins), so nothing needs spawning.

Summoning - pouches at the obelisk in Pikkupstix's cellar under Taverley (2209, 5344):

    spawn the best pouch for the level's charm, spirit shards, empty pouches and secondary
    (a backpack's worth) -> obelisk: Infuse-pouch -> the pouch's Infuse-All (interface 669):
    the server makes every pouch the backpack holds at once -> drop the pouches, repeat
"""
import random
import time

from lumberjack import actions
from lumberjack.skills.base import BotBase, StopBot

# ---- Construction ------------------------------------------------------------------------
PORTAL_TILE = (2953, 3224, 0)          # Rimmington: in front of the house portal (HouseLocation)
PORTAL_ID = 15478
ESTATE_AGENT = (3240, 3476, 0)         # Varrock's estate agent stands at (3240, 3474)
HOUSE_COST = 1000
# garden space object -> its decorations in the furniture box's order: (name, level, xp)
TREES = [("Dead tree", 5, 31), ("Nice tree", 10, 44), ("Oak tree", 15, 70), ("Willow tree", 30, 100),
         ("Maple tree", 45, 122), ("Yew tree", 60, 141), ("Magic tree", 75, 223)]
SPACES = {
    15362: TREES,                                                                       # big tree
    15363: TREES,                                                                       # tree
    15364: [("Fern", 1, 31), ("Bush", 6, 70), ("Tall plant", 12, 100)],                # big plant 1
    15365: [("Short plant", 1, 31), ("Large leaf plant", 6, 70), ("Huge plant", 12, 100)],
    15366: [("Plant", 1, 31), ("Small fern", 6, 70), ("Fern", 12, 100)],               # small plant 1
    15367: [("Dock leaf", 1, 31), ("Thistle", 6, 70), ("Reeds", 12, 100)],
}
# what each built decoration's object is (Decoration.java) - removed again for the next build
BUILT_IDS = set(range(13411, 13418)) | set(range(13418, 13425)) | set(range(13425, 13437))
BUILD_BOX = 396
SLOT_OF = [0, 2, 4, 6, 1, 3, 5]       # the box's slot for a decoration index (BuildingUtils.BUILD_INDEXES)
HOUSE_RADIUS = 20
BUSY_LIMIT = 10                 # tries in a row that build/remove nothing before going in again


def best_deco(space_id, level):
    """(index, name, xp) of the best decoration for a garden space at `level`, or None."""
    ok = [(i, n, xp) for i, (n, lv, xp) in enumerate(SPACES.get(space_id, [])) if lv <= level]
    return max(ok, key=lambda d: d[2]) if ok else None


class Constructor(BotBase):
    name = "construction"

    def __init__(self, **kw):
        kw.pop("keep_carried", None)
        super().__init__(keep_carried=False, **kw)
        self.built = self.removed = 0
        self.reentered = False              # went back in through the portal once already
        self.gs = None

    def progress(self):
        return self.built

    def stats(self):
        return f"{self.built} built, {self.removed} removed"

    def run(self):
        self.log.info("Construction started - press F12 to stop")
        try:
            self.prepare()
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs is None:
                raise StopBot("Construction needs the game's own data")
            self.enter_house()
            idle = busy = 0
            while True:
                self.check_stop()
                actions.dismiss_dialog(self.ctx)
                done = self.built + self.removed
                if self.tick():
                    idle = 0
                    busy = 0 if self.built + self.removed > done else busy + 1
                    if busy < BUSY_LIMIT:
                        continue
                    # clicking away (walking up, opening the box) with nothing built or removed:
                    # out of building mode, or a space we can't reach - go in again / stop
                    self.log.info("%d tries without building or removing anything", busy)
                    busy, idle = 0, 6
                else:
                    idle += 1
                if idle >= 6:
                    if not self.in_house() or not self.reentered:
                        self.log.info("Not in the house in building mode - going (back) in")
                        self.reentered = True
                        self.enter_house(force=True)
                        idle = 0
                        continue
                    raise StopBot("nothing to build or remove in the garden (not in building mode?)")
                self.sleep(1.0)
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            self.inp.close()

    def level(self):
        from lumberjack.core import gamestate
        return (gamestate.skill("construction") or {}).get("base", 1)

    # ---- the house ---------------------------------------------------------------------
    def in_house(self):
        locs = self.gs.locs(HOUSE_RADIUS)
        return any(l.get("id") in SPACES or l.get("id") in BUILT_IDS for l in locs)

    def enter_house(self, force=False):
        """Through the Rimmington portal in building mode (buying the house first if need be)."""
        from lumberjack.nav import places
        if not force and self.in_house():
            self.log.info("Already in the house")
            return
        for attempt in range(2):
            self.state = "going to the house portal"
            self.log.info("Going to the house portal at Rimmington")
            x, y, plane = PORTAL_TILE
            if not places.teleport(self.ctx, self.gs, (x, y), plane):
                raise StopBot("couldn't teleport to the Rimmington house portal")
            if self.use_portal():
                return
            if attempt == 0 and self.no_house_yet():
                self.buy_house()
                continue
            break
        raise StopBot("couldn't get into the house in building mode")

    def use_portal(self):
        from lumberjack.core import interact
        portal = next((l for l in self.gs.locs(10) if l.get("id") == PORTAL_ID
                       or (l["name"] == "Portal" and "Enter" in (l.get("ops") or []))), None)
        if portal is None:
            self.log.warning("No house portal in sight at Rimmington")
            return False
        self.state = "entering the house (building mode)"
        if not interact.use_option(self.ctx, self.gs, interact.points_for(portal), "Enter", portal["name"]):
            interact.walk_to_tile(self.ctx, self.gs, portal["tile"], arrive=1)
            if not interact.use_option(self.ctx, self.gs, interact.points_for(portal), "Enter", portal["name"]):
                return False
        if not self.pick_option("building mode", wait_s=6):
            self.log.info("No 'building mode' option at the portal")
            return False
        for _ in range(20):                     # the house is built on the server: a moment
            self.sleep(0.6)
            try:
                if self.in_house():
                    self.log.info("In the house, building mode")
                    actions.reset_camera(self.ctx)
                    return True
            except Exception:
                continue
        return False

    def no_house_yet(self):
        try:
            chat = self.gs.chat(10)
        except Exception:
            return True                         # can't tell: buying is harmless (it says if we have one)
        return any("don't have a house" in (l.get("text") or "").lower() for l in chat.get("lines", []))

    def buy_house(self):
        """1000 coins to the Varrock estate agent for a Rimmington house."""
        from lumberjack import items
        from lumberjack.core import backpack, interact
        from lumberjack.nav import places
        self.log.info("No house yet - buying one from the estate agent (%d coins)", HOUSE_COST)
        coins = sum(s.get("count", 1) for s in backpack.slots() or [] if s["key"] == "coins")
        if coins < HOUSE_COST:
            items.spawn(self.ctx, "coins", HOUSE_COST)
        x, y, plane = ESTATE_AGENT
        if not places.teleport(self.ctx, self.gs, (x, y), plane):
            raise StopBot("couldn't teleport to the Varrock estate agent")
        agent = next((n for n in self.gs.npcs() if n["name"] == "Estate agent"), None)
        if agent is None:
            raise StopBot("no estate agent in Varrock to buy a house from")
        self.state = "buying a house"
        if not interact.use_option(self.ctx, self.gs, interact.points_for(agent), "Talk-to", agent["name"]):
            raise StopBot("couldn't talk to the estate agent")
        if not self.talk_through(["how can i get a house", "yes please"]):
            raise StopBot("couldn't buy a house from the estate agent")
        self.log.info("Bought a house in Rimmington")

    # ---- dialogues ---------------------------------------------------------------------
    def pick_option(self, text, wait_s=4.0):
        """Click the dialogue option containing `text` once it shows. True if clicked."""
        from lumberjack.ui import widgets
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            for w in widgets.find(self.gs, text):
                if w["w"] > 0 and w["h"] > 0:
                    x, y = widgets.center(w)
                    self.inp.click(x + random.randint(-6, 6), y + random.randint(-1, 1))
                    self.sleep(0.8)
                    return True
            self.sleep(0.4)
        return False

    def talk_through(self, options, limit=20):
        """Click through a conversation: each of `options` (in order) when it shows, "Click here
        to continue" otherwise. True when the last option was picked."""
        from lumberjack.ui import widgets
        want = list(options)
        for _ in range(limit):
            if want and self.pick_option(want[0], wait_s=1.2):
                want.pop(0)
                if not want:
                    for _ in range(4):           # the closing lines
                        self.sleep(0.6)
                        widgets.continue_dialog(self.ctx, self.gs)
                    return True
                continue
            widgets.continue_dialog(self.ctx, self.gs)
            self.sleep(0.6)
        return False

    # ---- building ------------------------------------------------------------------------
    def tick(self):
        """Remove one built decoration, else build one empty space. True if something was done."""
        locs = self.gs.locs(HOUSE_RADIUS)
        built = [l for l in locs if "Remove" in (l.get("ops") or []) and l.get("id") in BUILT_IDS]
        if built:
            return self.remove(min(built, key=lambda l: l.get("dist", 99)))
        lv = self.level()
        spaces = [(l, best_deco(l.get("id"), lv)) for l in locs if "Build" in (l.get("ops") or [])]
        spaces = [(l, d) for l, d in spaces if d is not None]
        if not spaces:
            return False
        loc, deco = max(spaces, key=lambda t: (t[1][2], -t[0].get("dist", 99)))   # best xp, nearest
        return self.build(loc, deco)

    def build(self, loc, deco):
        from lumberjack.core import interact
        index, name, xp = deco
        self.state = f"building a {name.lower()}"
        if not interact.use_option(self.ctx, self.gs, interact.points_for(loc), "Build", loc["name"]):
            interact.walk_to_tile(self.ctx, self.gs, loc["tile"], arrive=1, max_clicks=3)
            return True                          # (moved: counts as doing something)
        if not self.choose_in_box(index, name):
            self.close_box()
            return False
        if self.wait_xp(6.0):
            self.built += 1
        return True

    def choose_in_box(self, index, name):
        """In the furniture box: click `name` (decoration `index`). True if clicked. The box lists
        each piece as an icon with its name beside it; the icon (the item grid, component 132)
        is what builds - found by hovering the row until the game offers "Build"."""
        from lumberjack.core.gamestate import menu_row_point, top_entry
        from lumberjack.ui import widgets
        end = time.monotonic() + 4
        box = []
        while time.monotonic() < end and not box:
            self.sleep(0.4)
            box = [w for w in widgets.find(self.gs, str(BUILD_BOX)) if w.get("if") == BUILD_BOX and w["w"] > 0]
        if not box:
            return False
        if not getattr(self, "_box_logged", False):
            self._box_logged = True
            self.log.info("Furniture box: %s", "; ".join(
                f"{w.get('idx')}:{(w.get('text') or '').strip()[:20]!r}{w.get('ops') or ''}@{w['x']},{w['y']} "
                f"{w['w']}x{w['h']}" for w in box[:30]))
        label = next((w for w in box if (w.get("text") or "").strip().lower() == name.lower()), None)
        grid = next((w for w in box if w.get("idx") == 132), None)
        pts = []
        if label is not None:                    # the icon sits left of the name, the row below it
            for dy in (14, 4, 24, 34, -4):
                for dx in (-45, -60, -30, -15, 10, 30):
                    pts.append((label["x"] + dx, label["y"] + dy))
        if grid is not None:                     # the grid's own cells (2 columns, 4 rows)
            slot = SLOT_OF[index]
            for cols, rows in ((2, 4), (1, 7)):
                col, row = (slot % 2, slot // 2) if cols == 2 else (0, index)
                cw, ch = grid["w"] / cols, grid["h"] / rows
                pts.append((int(grid["x"] + cw * (col + 0.5)), int(grid["y"] + ch * (row + 0.5))))
        build = lambda e: bool(e) and e["verb"].lower().startswith("build")    # noqa: E731
        for x, y in pts:
            self.inp.move(x, y)
            self.sleep(0.12)
            menu = self.gs.menu()
            if build(top_entry(menu)):
                self.inp.click()
                return True
            if any(build(e) for e in menu.get("entries") or []):
                self.inp.right_click(x, y)
                self.sleep(0.3)
                menu = self.gs.menu()
                e = next((e for e in menu.get("entries") or [] if build(e)), None)
                if menu.get("open") and e:
                    rx, ry = menu_row_point(menu, e["row"])
                    self.inp.click(rx, ry)
                    return True
        self.log.info("Nothing in the furniture box offered Build for %s", name)
        return False

    def close_box(self):
        """Close the furniture box (its X)."""
        from lumberjack.ui import widgets
        for w in widgets.find(self.gs, str(BUILD_BOX)):
            if w.get("if") == BUILD_BOX and w["w"] > 0 and any(o.lower() == "close" for o in w.get("ops") or []):
                self.inp.click(*widgets.center(w))
                self.sleep(0.4)
                return True
        return False

    def remove(self, loc):
        from lumberjack.core import interact
        self.state = f"removing the {loc['name'].lower()}"
        if not interact.use_option(self.ctx, self.gs, interact.points_for(loc), "Remove", loc["name"]):
            interact.walk_to_tile(self.ctx, self.gs, loc["tile"], arrive=1, max_clicks=3)
            return True
        if self.pick_option("yes", wait_s=3):
            self.removed += 1
            self.sleep(1.2)
        return True

    def wait_xp(self, limit_s):
        from lumberjack.core import gamestate
        xp0 = (gamestate.skill("construction") or {}).get("xp", 0)
        end = time.monotonic() + limit_s
        while time.monotonic() < end:
            self.sleep(0.6)
            if (gamestate.skill("construction") or {}).get("xp", 0) > xp0:
                return True
        return False


# ---- Summoning ---------------------------------------------------------------------------
OBELISK_TILE = (2209, 5342, 0)          # Pikkupstix's cellar, in front of the obelisk at (2209, 5344)
INFUSE_BOX = 669
SHARDS = 12183
STACKS = {12158, 12159, 12160, 12163, 12164, 12165, 12166, 12167, 12168, SHARDS}   # charms + shards stack
# (level, pouch name, pouch id, xp, [(item id, amount)]) - SummoningPouch.java, one secondary each
POUCHES = [
    (1, "Spirit wolf pouch", 12047, 4.8, [(12158, 1), (12155, 1), (2859, 1), (SHARDS, 7)]),
    (4, "Dreadfowl pouch", 12043, 9.3, [(12158, 1), (12155, 1), (2138, 1), (SHARDS, 8)]),
    (10, "Spirit spider pouch", 12059, 12.6, [(12158, 1), (12155, 1), (6291, 1), (SHARDS, 8)]),
    (13, "Thorny snail pouch", 12019, 12.6, [(12158, 1), (12155, 1), (3363, 1), (SHARDS, 9)]),
    (16, "Granite crab pouch", 12009, 21.6, [(12158, 1), (12155, 1), (440, 1), (SHARDS, 7)]),
    (17, "Spirit mosquito pouch", 12778, 46.5, [(12158, 1), (12155, 1), (6319, 1), (SHARDS, 1)]),
    (19, "Spirit scorpion pouch", 12055, 83.2, [(12160, 1), (12155, 1), (3095, 1), (SHARDS, 57)]),
    (22, "Spirit tz-kih pouch", 12808, 96.8, [(12160, 1), (12168, 1), (12155, 1), (SHARDS, 64)]),
    (23, "Albino rat pouch", 12067, 202.4, [(12163, 1), (12155, 1), (2134, 1), (SHARDS, 75)]),
    (25, "Spirit kalphite pouch", 12063, 220.0, [(12163, 1), (12155, 1), (3138, 1), (SHARDS, 51)]),
    (29, "Giant chinchompa pouch", 12800, 255.2, [(12163, 1), (12155, 1), (10033, 1), (SHARDS, 84)]),
    (36, "Bronze minotaur pouch", 12073, 316.8, [(12163, 1), (12155, 1), (2349, 1), (SHARDS, 102)]),
    (46, "Iron minotaur pouch", 12075, 404.8, [(12163, 1), (12155, 1), (2351, 1), (SHARDS, 125)]),
    (55, "Spirit jelly pouch", 12027, 484.0, [(12163, 1), (12155, 1), (1937, 1), (SHARDS, 151)]),
    (56, "Steel minotaur pouch", 12077, 492.8, [(12163, 1), (12155, 1), (2353, 1), (SHARDS, 141)]),
    (57, "Spirit graahk pouch", 12810, 501.6, [(12163, 1), (12155, 1), (10099, 1), (SHARDS, 154)]),
    (58, "Karamthulhu pouch", 12023, 510.4, [(12163, 1), (12155, 1), (6667, 1), (SHARDS, 144)]),
    (66, "Mithril minotaur pouch", 12079, 580.8, [(12163, 1), (12155, 1), (2359, 1), (SHARDS, 152)]),
    (73, "Obsidian golem pouch", 12792, 642.4, [(12163, 1), (12155, 1), (12168, 1), (SHARDS, 195)]),
    (76, "Adamant minotaur pouch", 12081, 668.8, [(12163, 1), (12155, 1), (2361, 1), (SHARDS, 144)]),
    (77, "Talon beast pouch", 12794, 1015.2, [(12160, 1), (12155, 1), (12162, 1), (SHARDS, 174)]),
    (86, "Rune minotaur pouch", 12083, 756.8, [(12163, 1), (12155, 1), (2363, 1), (SHARDS, 1)]),
]


def best_pouch(level):
    ok = [p for p in POUCHES if p[0] <= level]
    return max(ok, key=lambda p: p[3]) if ok else POUCHES[0]


def per_load(pouch):
    """How many of `pouch` a backpack holds the ingredients for (stackables take one slot each)."""
    stack = [i for i, _ in pouch[4] if i in STACKS]
    single = [i for i, _ in pouch[4] if i not in STACKS]
    return max(1, (28 - len(stack)) // max(1, len(single)))


class Summoner(BotBase):
    name = "summoning"

    def __init__(self, **kw):
        kw.pop("keep_carried", None)
        super().__init__(keep_carried=False, **kw)
        self.made = 0
        self.gs = None
        self.fails = 0

    def progress(self):
        return self.made

    def stats(self):
        return f"{self.made} pouches made"

    def run(self):
        self.log.info("Summoning started - press F12 to stop")
        try:
            self.prepare()
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs is None:
                raise StopBot("Summoning needs the game's own data")
            while True:
                self.check_stop()
                actions.dismiss_dialog(self.ctx)
                self.go_to_obelisk()
                pouch = best_pouch(self.level())
                if not self.has_load(pouch):
                    self.restock(pouch)
                    continue
                made = self.infuse(pouch)
                if made:
                    self.made += made
                    self.fails = 0
                    self.log.info("+%d %s - %s", made, pouch[1].lower(), self.stats())
                    continue
                self.fails += 1
                if self.fails >= 4:
                    raise StopBot(f"the obelisk won't make a {pouch[1].lower()} (4 tries in a row)")
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            self.inp.close()

    def level(self):
        from lumberjack.core import gamestate
        return (gamestate.skill("summoning") or {}).get("base", 1)

    def obelisk(self):
        return next((l for l in self.gs.locs(12) if "Infuse-pouch" in (l.get("ops") or [])), None)

    def go_to_obelisk(self):
        from lumberjack.nav import places
        if self.obelisk() is not None:
            return
        x, y, plane = OBELISK_TILE
        self.state = "teleporting to the obelisk"
        if not places.teleport(self.ctx, self.gs, (x, y), plane):
            raise StopBot("couldn't teleport to the summoning obelisk (Taverley cellar)")
        if self.obelisk() is None:
            raise StopBot("no obelisk with Infuse-pouch at Pikkupstix's cellar")

    def counts(self):
        from lumberjack.core import backpack
        out = {}
        for s in backpack.slots() or []:
            if s["id"] >= 0:
                out[s["id"]] = out.get(s["id"], 0) + max(1, s.get("count", 1))
        return out

    def has_load(self, pouch):
        have = self.counts()
        return all(have.get(i, 0) >= n for i, n in pouch[4])

    def restock(self, pouch):
        """Drop the last load's pouches and leftovers, spawn a backpack of ingredients."""
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        junk = [i for i, s in enumerate(inv) if s["id"] >= 0 and s["id"] not in {i for i, _ in pouch[4]}]
        if junk:
            self.state = "dropping the pouches"
            actions.drop_known(self.ctx, self.gs, junk)
        n = per_load(pouch)
        self.state = f"spawning {pouch[1].lower()} ingredients"
        have = self.counts()
        for iid, amount in pouch[4]:
            want = amount * n - have.get(iid, 0)
            if want > 0:
                self.spawn_id(iid, want)
        if not self.has_load(pouch):
            raise StopBot(f"couldn't spawn the ingredients for a {pouch[1].lower()} (backpack full?)")

    def spawn_id(self, iid, amount):
        actions.dismiss_dialog(self.ctx)
        self.inp.move(260, 300)
        self.inp.type_text(f"::item {iid} {amount}" if amount > 1 else f"::item {iid}", enter=True)
        self.sleep(1.0)

    def infuse(self, pouch):
        """Infuse-pouch at the obelisk, then the pouch's Infuse-All. How many were made."""
        from lumberjack.core import interact
        from lumberjack.ui import widgets
        before = self.counts().get(pouch[2], 0)
        ob = self.obelisk()
        if ob is None:
            return 0
        self.state = f"infusing: {pouch[1].lower()}"
        actions.dismiss_dialog(self.ctx)            # a level-up box swallows the next clicks
        if not interact.use_option(self.ctx, self.gs, interact.points_for(ob), "Infuse-pouch", ob["name"]):
            interact.walk_to_tile(self.ctx, self.gs, ob["tile"], arrive=1, max_clicks=3)
            return 0
        box = []
        end = time.monotonic() + 5
        while time.monotonic() < end and not box:
            self.sleep(0.4)
            box = [w for w in widgets.find(self.gs, str(INFUSE_BOX)) if w.get("if") == INFUSE_BOX and w["w"] > 0]
        if not box:
            self.log.info("The infusing screen didn't open")
            return 0
        item = next((w for w in box if w.get("obj") == pouch[2]), None) or \
            next((w for w in box if pouch[1].lower() in (w.get("text") or w.get("name") or "").lower()), None)
        if item is None:
            self.log.info("%s isn't on the infusing screen", pouch[1])
            actions.cancel_selection(self.ctx, force=True)
            return 0
        if not widgets.choose(self.ctx, self.gs, item, "Infuse-All", menu=True):
            self.log.info("No Infuse-All for %s", pouch[1])
            actions.dismiss_dialog(self.ctx)
            actions.cancel_selection(self.ctx, force=True)      # start the next try afresh
            return 0
        end = time.monotonic() + 6
        while time.monotonic() < end:
            self.sleep(0.6)
            now = self.counts().get(pouch[2], 0)
            if now > before:
                self.sleep(0.6)
                return self.counts().get(pouch[2], 0) - before
        return 0
