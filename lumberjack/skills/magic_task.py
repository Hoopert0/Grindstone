"""Magic: cast the best training spell for the level, runes spawned (singleplayer admin).

    1   Wind Strike on chickens       (stand by chickens: the route's place) - then Water Strike 5,
        Earth Strike 9, Fire Strike 13, Wind Bolt 17
    21  Low Level Alchemy on arrows   (a spawned stack: one arrow per cast)
    25  Varrock Teleport              (anywhere)
    45  Camelot Teleport
    55  High Level Alchemy on arrows

Spells are found in the spellbook by name through the game's interfaces (ui.widgets - needs the
rebuilt add-on); targets are clicked when the menu's top entry reads "Cast ...".
"""
import random
import time


from lumberjack import actions, items
from lumberjack.skills.base import BotBase, StopBot

# (level, spell, kind, runes per cast)
SPELLS = [
    (55, "High Level Alchemy", "alch", {"fire_rune": 5, "nature_rune": 1}),
    (45, "Camelot Teleport", "tele", {"law_rune": 1, "air_rune": 5}),
    (25, "Varrock Teleport", "tele", {"law_rune": 1, "air_rune": 3, "fire_rune": 1}),
    (21, "Low Level Alchemy", "alch", {"fire_rune": 3, "nature_rune": 1}),
    # combat spells on chickens until alchemy (runes per cast from the server's spell code)
    (17, "Wind Bolt", "npc", {"air_rune": 2, "chaos_rune": 1}),
    (13, "Fire Strike", "npc", {"air_rune": 2, "fire_rune": 3, "mind_rune": 1}),
    (9, "Earth Strike", "npc", {"air_rune": 1, "earth_rune": 2, "mind_rune": 1}),
    (5, "Water Strike", "npc", {"air_rune": 1, "water_rune": 1, "mind_rune": 1}),
    (1, "Wind Strike", "npc", {"air_rune": 1, "mind_rune": 1}),
]
RUNE_SPAWN = 1000
ALCH_ITEM = "iron_arrow"       # stackable and alchable: one stack lasts a thousand casts
TARGETS = ["chicken"]
CAST_S = {"alch": 3.0, "tele": 4.2, "npc": 2.6}
EAT_BELOW = 0.5
MISS = "miss"            # strike(): the spell was picked but no target stayed under the cursor
MISS_LIMIT = 30
FAIL_LIMIT = 8
XP_GRACE_S = 3.0


def best_spell(level):
    for lv, spell, kind, runes in SPELLS:
        if level >= lv:
            return spell, kind, runes
    return SPELLS[-1][1:]


def pick_targets(npcs, me):
    """Whom to cast on, best first. Something already fighting us (or that we're fighting) comes
    first - and while we're under attack it's the only choice: anything else just gets "You're
    already under attack!". Otherwise the nearest one nobody is fighting."""
    mine = 32768 + me.get("index", -1)
    ours = [n for n in npcs if n.get("interacting") == mine or n.get("index") == me.get("interacting")]
    if ours:
        return sorted(ours, key=lambda n: n["dist"])
    if me.get("in_combat"):
        return []                       # attacked by something we can't see/cast on yet: wait
    return sorted((n for n in npcs if not n.get("in_combat")), key=lambda n: n["dist"])


class Mage(BotBase):
    name = "magic"

    def __init__(self, spawn_tools=True, targets=None, **kw):
        kw.pop("keep_carried", None)
        super().__init__(keep_carried=False, **kw)
        self.spawn_tools = spawn_tools
        self.targets = [t.lower() for t in (targets or TARGETS)]
        self.casts = 0
        self.fails = 0
        self.misses = 0

    def progress(self):
        return self.casts

    def stats(self):
        return f"{self.casts} casts"

    def run(self):
        self.log.info("Magic started - press F12 to stop")
        try:
            self.prepare()
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs is None or not self.spawn_tools:
                raise StopBot("Magic needs the game's own data and 'Spawn missing tools' (it spawns runes)")
            while True:
                self.check_stop()
                lv = (gamestate.skill("magic") or {}).get("base", 1)
                spell, kind, runes = best_spell(lv)
                self.ensure_runes(runes)
                if kind == "npc":
                    self.ensure_hp()
                ok = self.cast(spell, kind)
                if ok is None:                         # nothing to cast on right now
                    continue
                if ok == MISS:                         # the target walked off the cursor: no failed cast
                    self.misses += 1
                    if self.misses >= MISS_LIMIT:
                        raise StopBot(f"couldn't get the cursor on a target {MISS_LIMIT} times in a row")
                    continue
                self.misses = 0
                self.fails = 0 if ok else self.fails + 1
                if self.fails >= FAIL_LIMIT:
                    raise StopBot(f"couldn't cast {spell} {FAIL_LIMIT} times in a row (no spellbook data? restart "
                                  "the game so the add-on is rebuilt)")
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            self.inp.close()

    # ---- steps -----------------------------------------------------------------------
    def ensure_runes(self, runes):
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        have = {s["key"]: s.get("count", 1) for s in inv if s["id"] >= 0}
        for rune, per in runes.items():
            if have.get(rune, 0) < per * 5:
                self.log.info("Spawning %s", rune.replace("_", " "))
                items.spawn(self.ctx, rune, RUNE_SPAWN)

    def spell_widget(self, spell):
        from lumberjack.ui import widgets
        found = [w for w in widgets.find(self.gs, spell.lower()) if w["w"] > 0]
        return found[0] if found else None

    def select_spell(self, spell):
        """Open the spellbook and click the spell. True if clicked."""
        actions.open_tab(self.ctx, "magic")
        w = self.spell_widget(spell)
        if w is None:
            self.log.warning("Can't find %s in the spellbook", spell)
            return False
        from lumberjack.ui import widgets
        x, y = widgets.center(w)
        self.inp.click(x + random.randint(-3, 3), y + random.randint(-3, 3))
        self.sleep(random.uniform(0.25, 0.4))
        return True

    def _xp(self):
        return self.gs.skills()["magic"]["xp"]

    def cast(self, spell, kind):
        xp0 = self._xp()
        self.state = f"casting {spell}"
        if kind == "tele":
            self.state = f"teleporting ({spell})"      # 'teleport' in the state: the jump guard ignores it
            if not self.select_spell(spell):
                return False
        elif kind == "alch":
            if not self.alch(spell):
                return False
        else:
            hit = self.strike(spell)
            if hit is None or hit == MISS:
                return hit
            if not hit:
                return False
        self.sleep(CAST_S[kind] + random.uniform(0, 0.4))
        # a strike walks into range first: give the xp a few more seconds to land
        end = time.monotonic() + (XP_GRACE_S if kind == "npc" else 0)
        while True:
            if self._xp() > xp0:
                self.casts += 1
                return True
            if time.monotonic() >= end:
                return False
            self.sleep(0.5)

    def alch(self, spell):
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        slot = next((i for i, s in enumerate(inv) if s["key"] == ALCH_ITEM), None)
        if slot is None:
            items.spawn(self.ctx, ALCH_ITEM, RUNE_SPAWN)
            self.sleep(0.6)
            inv = backpack.slots() or []
            slot = next((i for i, s in enumerate(inv) if s["key"] == ALCH_ITEM), None)
            if slot is None:
                return False
        if not self.select_spell(spell):
            return False
        self.sleep(0.3)                                 # the backpack opens by itself
        if actions.use_slot(self.ctx, self.gs, slot, "Cast"):
            return True
        actions.open_tab(self.ctx, "inventory")
        return actions.use_slot(self.ctx, self.gs, slot, "Cast")

    def strike(self, spell):
        from lumberjack.core import interact
        from lumberjack.core.backpack import key
        from lumberjack.core.gamestate import top_entry
        npcs = [n for n in self.gs.npcs() if key(n["name"]) in self.targets and interact.on_screen(*n["screen"])]
        npcs = pick_targets(npcs, self.gs.player())
        if not npcs:
            self.state = "looking for something to cast on"
            self.sleep(1.5)
            return None                                  # waiting, not a failed cast
        if not self.select_spell(spell):
            return False
        for n in npcs[:3]:                               # chickens wander: the next one if this moved
            for px, py in interact.points_for(n):
                if not interact.on_screen(px, py):
                    continue
                self.inp.move(px + random.randint(-2, 2), py + random.randint(-2, 2))
                self.sleep(random.uniform(0.1, 0.16))
                top = top_entry(self.gs.menu())
                if top and top["verb"] == "Cast" and n["name"].lower() in top["subject"].lower():
                    self.inp.click()
                    return True
        actions.cancel_selection(self.ctx, force=True)
        return MISS

    def ensure_hp(self):
        s = self.gs.skills()["hitpoints"]
        if not s["level"] or s["boosted"] / s["level"] >= EAT_BELOW:
            return
        from lumberjack.core import backpack
        actions.open_tab(self.ctx, "inventory")
        inv = backpack.slots() or []
        food = [i for i, x in enumerate(inv) if backpack.kind(x["key"]) == "cooked"]
        if not food:
            items.fill(self.ctx, "lobster", 5)
            inv = backpack.slots() or []
            food = [i for i, x in enumerate(inv) if backpack.kind(x["key"]) == "cooked"]
        if food and actions.use_slot(self.ctx, self.gs, food[0], "Eat"):
            self.sleep(1.8)
            return
        raise StopBot("HP low and no food")



