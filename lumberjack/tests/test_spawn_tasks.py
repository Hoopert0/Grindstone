"""The spawned-supply skills (Prayer, Fletching, Crafting) and Thieving, with a fake game."""
import types

import pytest

from lumberjack import actions, items
from lumberjack.core import backpack, gamestate
from lumberjack.skills import spawn_tasks as S
from lumberjack.skills import thieving_task as T
from lumberjack.skills.base import StopBot


class Game:
    """A backpack, skills, a menu and NPCs - what the bots read through game data."""

    def __init__(self):
        self.inv = [{"id": -1, "key": None, "name": None} for _ in range(28)]
        self.skills_ = {k: {"level": 1, "boosted": 1, "xp": 0} for k in gamestate.SKILLS}
        self.skills_["hitpoints"] = {"level": 10, "boosted": 10, "xp": 1154}
        self.top = None
        self.npcs_ = []

    def put(self, key, n=1):
        for _ in range(n):
            i = next(i for i, s in enumerate(self.inv) if s["id"] < 0)
            self.inv[i] = {"id": 1, "key": key, "name": key}

    def skills(self):
        return self.skills_

    def player(self):
        return {"tile": [3200, 3200], "plane": 0, "logged_in": True, "index": 1}

    def menu(self):
        return {"open": False, "entries": [{"verb": self.top, "subject": ""}] if self.top else []}

    def npcs(self, name=None):
        return self.npcs_


@pytest.fixture
def game(monkeypatch):
    g = Game()
    monkeypatch.setattr(backpack, "slots", lambda: g.inv)
    monkeypatch.setattr(gamestate, "shared", lambda: g)
    monkeypatch.setattr(gamestate, "skill", lambda name: {"base": g.skills_[name]["level"]})
    def spawn(ctx, key, n=1):
        if key.endswith(("_rune", "_arrow", "coins")):           # stackables: one slot
            g.put(key)
            g.inv[next(i for i, s in reversed(list(enumerate(g.inv))) if s["key"] == key)]["count"] = n
        else:
            g.put(key, min(n, sum(s["id"] < 0 for s in g.inv)))
    monkeypatch.setattr(items, "spawn", spawn)
    monkeypatch.setattr(actions, "dismiss_dialog", lambda ctx: False)
    monkeypatch.setattr(actions, "drop_known", lambda ctx, gs, slots: [g.inv.__setitem__(
        i, {"id": -1, "key": None, "name": None}) for i in slots] and len(slots))
    return g


def bot(cls, game, **kw):
    b = cls.__new__(cls)
    b.ctx = types.SimpleNamespace(sleep=lambda s: None)
    b.inp = types.SimpleNamespace(move=lambda *a, **k: None, close=lambda: None)
    b.log = __import__("logging").getLogger("t")
    b.gs, b.state, b.home = game, "", [3200, 3200]
    b.done, b.blocked, b.current, b.spawn_tools, b.unblocks = 0, set(), None, True, 0
    b.sleep = lambda s: None
    b.check_stop = lambda: None
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_prayer_spawns_dragon_bones_and_buries(game, monkeypatch):
    p = bot(S.Prayer, game)
    p.restock(game.inv, {})
    assert sum(s["key"] == "dragon_bones" for s in game.inv) == 28
    buried = []

    def use(ctx, gs, i, verb):
        assert verb == "Bury"
        buried.append(i)
        game.inv[i] = {"id": -1, "key": None, "name": None}
        return True
    monkeypatch.setattr(actions, "use_slot", use)
    supply = [i for i, s in enumerate(game.inv) if s["key"] == "dragon_bones"]
    assert p.process(game.inv, supply, {}) == 28 and len(buried) == 28


def test_crafting_picks_gem_by_level_and_keeps_chisel(game, monkeypatch):
    game.skills_["crafting"]["level"] = 30
    c = bot(S.Crafter, game)
    assert c.best_supply(30) == "uncut_emerald" and c.best_supply(1) == "uncut_opal"
    game.put("chisel")
    game.put("sapphire", 3)                                  # last load's cut gems
    monkeypatch.setattr(items, "ensure", lambda *a, **k: -1)
    tools = c.ensure_tools()
    c.restock(game.inv, tools)
    keys = [s["key"] for s in game.inv]
    assert keys[0] == "chisel" and keys.count("uncut_emerald") == 27 and "sapphire" not in keys
    assert all(k in items.BY_KEY for k in list(S.GEMS) + ["chisel", "knife", "dragon_bones"])


def test_supply_task_blocks_a_supply_that_never_works(game, monkeypatch):
    f = bot(S.Fletcher, game)
    game.skills_["fletching"]["level"] = 40
    assert f.best_supply(40) == "willow_logs"
    f.blocked.add("willow_logs")
    assert f.best_supply(40) == "oak_logs"


def test_thief_success_stun_and_eating(game, monkeypatch):
    from lumberjack.core import interact
    t = bot(T.Thief, game, targets=["man", "woman"], eat_below=0.5, stolen=0, caught=0, eaten=0)
    game.npcs_ = [{"name": "Man", "ops": ["Talk-to", "Attack", "Pickpocket"], "dist": 2, "screen": [300, 200],
                   "tile": [3201, 3201]}, {"name": "Cow", "ops": ["Attack"], "dist": 1, "screen": [310, 200],
                                           "tile": [3200, 3201]}]
    monkeypatch.setattr(interact, "on_screen", lambda x, y, margin=4: True)
    n = t.pick_target()
    assert n["name"] == "Man"
    outcome = {"xp": 8, "hp": 0}

    def pick(ctx, gs, pts, verb, subject):
        assert verb == "Pickpocket" and subject == "Man"
        game.skills_["thieving"]["xp"] += outcome["xp"]
        game.skills_["hitpoints"]["boosted"] -= outcome["hp"]
        return (300, 200)
    monkeypatch.setattr(interact, "use_option", pick)
    t.attempt(n)
    assert t.stolen == 1 and t.caught == 0
    outcome.update(xp=0, hp=2)
    t.attempt(n)
    assert t.caught == 1
    game.skills_["hitpoints"]["boosted"] = 4                  # below 50%: eat - none, so spawn
    eaten = []
    monkeypatch.setattr(actions, "use_slot", lambda ctx, gs, i, verb: eaten.append(game.inv[i]["key"]) or True)
    t.ensure_hp()
    assert eaten == ["lobster"] and t.eaten == 1
    t.spawn_tools = False
    game.inv = [{"id": -1, "key": None, "name": None} for _ in range(28)]
    with pytest.raises(StopBot):
        t.ensure_hp()


def test_thief_makes_room_but_keeps_coins_and_food(game):
    t = bot(T.Thief, game)
    game.put("coins")
    game.put("lobster", 2)
    game.put("potato_seed", 25)
    t.make_room()
    assert [s["key"] for s in game.inv if s["id"] >= 0] == ["coins", "lobster", "lobster"]


def test_thieving_route_and_place_check_targets():
    from lumberjack.nav import training
    assert training.pick("thieving", 30)[1]["thieve"] == ["al-kharid_warrior", "al_kharid_warrior"]
    gs = types.SimpleNamespace(npcs=lambda name=None: [
        {"name": "Guard", "ops": ["Attack", "Pickpocket"], "tile": [1, 1], "dist": 3},
        {"name": "Guard", "ops": ["Attack", "Pickpocket"], "tile": [9, 9], "dist": 30}])
    assert training.find_targets(gs, "thieving", {"thieve": ["guard"]}, 15) == [[1, 1]]


def test_smither_uses_bar_on_anvil_and_makes_daggers(game, monkeypatch):
    from lumberjack.core import interact
    from lumberjack.ui import widgets
    sm = bot(S.Smither, game)
    assert sm.best_supply(33) == "steel_bar" and all(k in items.BY_KEY for k in S.Smither.SUPPLY)
    game.put("hammer")
    game.put("steel_bar", 5)
    anvil = {"name": "Anvil", "ops": [], "screen": [300, 200], "body": [300, 190], "tile": [1, 1], "dist": 2}
    game.locs = lambda radius, name=None: [anvil]
    used, hovered = [], []
    monkeypatch.setattr(actions, "use_slot", lambda ctx, gs, i, verb: used.append((i, verb)) or True)
    monkeypatch.setattr(interact, "on_screen", lambda x, y, margin=4: True)
    sm.inp = types.SimpleNamespace(move=lambda x, y, **k: hovered.append((x, y)), click=lambda *a: None,
                                   close=lambda: None)
    game.top = "Use"
    game.menu = lambda: {"open": False, "entries": [{"verb": "Use", "subject": "Steel bar -> Anvil"}]}

    def make(ctx, gs, product=None, amounts=()):
        assert product == "dagger" and amounts[0] == "All"
        for i, s in enumerate(game.inv):
            if s["key"] == "steel_bar":
                game.inv[i] = {"id": 1, "key": "steel_dagger", "name": "Steel dagger"}
        return True
    monkeypatch.setattr(widgets, "make", make)
    supply = [i for i, s in enumerate(game.inv) if s["key"] == "steel_bar"]
    assert sm.process(game.inv, supply, {"hammer": 0}) == 5
    assert used == [(1, "Use")]
    game.locs = lambda radius, name=None: []
    with pytest.raises(StopBot):
        sm.process(game.inv, supply, {"hammer": 0})


def test_smither_switches_to_the_menu_when_a_click_makes_one_bar(game, monkeypatch):
    """The dagger's All button made one bar per left-click in game: from then on its right-click
    menu's biggest amount is picked."""
    from lumberjack.core import interact
    from lumberjack.ui import widgets
    sm = bot(S.Smither, game)
    game.put("hammer")
    game.put("bronze_bar", 10)
    anvil = {"name": "Anvil", "ops": [], "screen": [300, 200], "body": [300, 190], "tile": [1, 1], "dist": 2}
    game.locs = lambda radius, name=None: [anvil]
    monkeypatch.setattr(actions, "use_slot", lambda ctx, gs, i, verb: True)
    monkeypatch.setattr(interact, "on_screen", lambda x, y, margin=4: True)
    monkeypatch.setattr(widgets, "make", lambda *a, **k: False)
    btn = {"if": 300, "idx": 21, "x": 100, "y": 100, "w": 40, "h": 20}
    monkeypatch.setattr(widgets, "find", lambda gs, match=None: [btn])
    menu = {"open": False}

    def smith(n):
        for _ in range(n):
            i = next(i for i, s in enumerate(game.inv) if s["key"] == "bronze_bar")
            game.inv[i] = {"id": 1, "key": "bronze_dagger", "name": "Bronze dagger"}
    picked = []

    def click(*a):
        if menu["open"]:
            picked.append(a)
            menu["open"] = False
            smith(sum(s["key"] == "bronze_bar" for s in game.inv))      # Make All
        elif a:
            smith(1)                                                     # this client: Make 1
    sm.inp = types.SimpleNamespace(move=lambda *a, **k: None, click=click, close=lambda: None,
                                   right_click=lambda x, y: menu.__setitem__("open", True))
    game.menu = lambda: ({"open": True, "x": 80, "y": 90, "w": 120, "h": 80, "entries": [
        {"verb": "Make 1", "subject": "", "row": 0}, {"verb": "Make 5", "subject": "", "row": 1},
        {"verb": "Make All", "subject": "", "row": 3}, {"verb": "Cancel", "subject": "", "row": 4}]}
        if menu["open"] else {"open": False, "entries": [{"verb": "Use", "subject": "Bronze bar -> Anvil"}]})
    monkeypatch.setattr(S, "time", types.SimpleNamespace(monotonic=iter(range(0, 10 ** 6)).__next__))
    supply = lambda: [i for i, s in enumerate(game.inv) if s["key"] == "bronze_bar"]   # noqa: E731
    assert sm.process(game.inv, supply(), {"hammer": 0}) == 1 and sm.smith_by_menu
    assert sm.process(game.inv, supply(), {"hammer": 0}) == 9 and picked


def test_supply_task_gives_a_blocked_supply_another_go(game, monkeypatch):
    sm = bot(S.Smither, game)
    sm.blocked.add("bronze_bar")
    sm.done = 5                                           # it worked earlier this run
    monkeypatch.setattr(sm, "walk_home", lambda: None)
    sm.restock(game.inv, {})
    assert sm.current == "bronze_bar" and not sm.blocked and sm.unblocks == 1
    sm.blocked.add("bronze_bar")
    sm.unblocks = S.MAX_UNBLOCKS
    with pytest.raises(StopBot, match="nothing left"):
        sm.restock(game.inv, {})


def test_best_bow_and_ranged_equip(game, monkeypatch):
    assert items.best_bow(1) == ("shortbow", "iron_arrow") and items.best_bow(45)[0] == "yew_shortbow"
    assert all(k in items.BY_KEY for b in items.BOWS for k in (b[0], b[2]))
    from lumberjack.skills import combat
    f = bot(combat.Fighter, game, train="ranged")
    game.skills_["ranged"]["level"] = 22
    worn = []
    f._worn = lambda: [{"key": k, "count": c} for k, c in worn]
    wielded = []

    def use(ctx, gs, i, verb):
        assert verb == "Wield"
        k = game.inv[i]["key"]
        wielded.append(k)
        worn.append((k, 1000 if k.endswith("arrow") else 1))
        game.inv[i] = {"id": -1, "key": None, "name": None}
        return True
    monkeypatch.setattr(actions, "use_slot", use)
    f.equip_ranged()
    assert wielded == ["willow_shortbow", "mithril_arrow"]
    f.equip_ranged()                                     # all worn: nothing more
    assert wielded == ["willow_shortbow", "mithril_arrow"]


def test_magic_spell_by_level_and_casting(game, monkeypatch):
    from lumberjack.skills import magic_task as M
    from lumberjack.ui import widgets
    assert M.best_spell(1)[0] == "Wind Strike" and M.best_spell(30)[0] == "Varrock Teleport"
    assert M.best_spell(60)[0] == "High Level Alchemy"
    assert all(k in items.BY_KEY for _, _, _, r in M.SPELLS for k in r) and M.ALCH_ITEM in items.BY_KEY
    m = bot(M.Mage, game, casts=0, fails=0, targets=["chicken"])
    m.ensure_runes({"law_rune": 1, "air_rune": 3})
    assert {s["key"] for s in game.inv if s["id"] >= 0} == {"law_rune", "air_rune"}
    clicks = []
    m.inp = types.SimpleNamespace(click=lambda *a: clicks.append(a), move=lambda *a, **k: None, close=lambda: None)
    monkeypatch.setattr(actions, "open_tab", lambda ctx, tab: None)
    monkeypatch.setattr(widgets, "find", lambda gs, match=None: [
        {"x": 571, "y": 229, "w": 24, "h": 24, "text": "", "name": "Cast Varrock Teleport", "ops": []}])

    def tele(*a):
        clicks.append(a)
        game.skills_["magic"]["xp"] += 35
    m.inp.click = tele
    assert m.cast("Varrock Teleport", "tele") and m.casts == 1 and "teleport" in m.state
    monkeypatch.setattr(widgets, "find", lambda gs, match=None: [])
    assert not m.cast("Varrock Teleport", "tele")


def test_magic_target_walking_off_the_cursor_is_a_miss_not_a_failed_cast(game, monkeypatch):
    """Chickens wander: a spell picked with no chicken left under the cursor isn't one of the 8
    failed casts that stopped the run ("couldn't cast Earth Strike 8 times in a row")."""
    from lumberjack.core import interact
    from lumberjack.skills import magic_task as M
    m = bot(M.Mage, game, casts=0, fails=0, misses=0, targets=["chicken"])
    m.inp = types.SimpleNamespace(click=lambda *a: None, move=lambda *a, **k: None, close=lambda: None)
    chicken = {"name": "Chicken", "ops": ["Attack"], "dist": 2, "screen": [300, 200], "body": [300, 195],
               "tile": [3201, 3201]}
    game.npcs_ = [chicken, dict(chicken, tile=[3203, 3203], screen=[340, 220], body=[340, 215])]
    monkeypatch.setattr(interact, "on_screen", lambda x, y, margin=4: True)
    monkeypatch.setattr(M, "pick_targets", lambda npcs, me: npcs)
    monkeypatch.setattr(m, "select_spell", lambda spell: True)
    monkeypatch.setattr(actions, "cancel_selection", lambda ctx, force=False: True)
    game.menu = lambda: {"open": False, "entries": [{"verb": "Walk here", "subject": ""}]}
    assert m.cast("Earth Strike", "npc") == M.MISS and m.fails == 0
    hovered = []

    def menu():
        hovered.append(1)
        return {"open": False, "entries": [{"verb": "Cast", "subject": "Earth Strike -> Chicken"}]
                if len(hovered) > 6 else [{"verb": "Walk here", "subject": ""}]}
    game.menu = menu                                      # the first chicken moved, the second didn't

    def click(*a):
        game.skills_["magic"]["xp"] += 9
    m.inp.click = click
    assert m.cast("Earth Strike", "npc") is True and m.casts == 1


def test_magic_route_places():
    from lumberjack.nav import training
    assert training.pick("magic", 1)[0] == "★ Lumbridge chickens" and training.pick("magic", 40) == (None, {})
    assert None not in training.places_to_check()


def test_melee_gear_by_level(game, monkeypatch):
    assert items.melee_gear(1, 1)[0] == "bronze_scimitar" or items.melee_gear(1, 1)[0] == "iron_scimitar"
    assert items.melee_gear(42, 31) == ["rune_scimitar", "adamant_full_helm", "adamant_platebody",
                                        "adamant_platelegs", "adamant_kiteshield"]
    assert all(k in items.BY_KEY for k in items.melee_gear(42, 31) + items.melee_gear(5, 20))
    from lumberjack.skills import combat
    f = bot(combat.Fighter, game, train="auto")
    game.skills_["attack"]["level"], game.skills_["defence"]["level"] = 21, 6
    worn = []
    f._worn = lambda: [{"key": k} for k in worn]
    verbs = []

    def use(ctx, gs, i, verb):
        verbs.append(verb)
        k = game.inv[i]["key"]
        if verb == ("Wield" if k.endswith(("scimitar", "kiteshield")) else "Wear"):
            worn.append(k)
            game.inv[i] = {"id": -1, "key": None, "name": None}
            return True
        return False
    monkeypatch.setattr(actions, "use_slot", use)
    f.equip_melee()
    assert worn == ["mithril_scimitar", "steel_full_helm", "steel_platebody", "steel_platelegs", "steel_kiteshield"]


def test_herblore_unlocks_spawns_and_mixes(game, monkeypatch):
    def spawn(ctx, key, n=1):                     # real ids: the herbalist matches by id
        for _ in range(min(n, sum(s["id"] < 0 for s in game.inv))):
            i = next(i for i, s in enumerate(game.inv) if s["id"] < 0)
            game.inv[i] = {"id": items.BY_KEY[key][1], "key": key, "name": key}
    monkeypatch.setattr(items, "spawn", spawn)
    typed = []
    game.varps = lambda *ids: {80: 0}
    h = bot(S.Herbalist, game)
    h.inp.type_text = lambda text, enter=False: typed.append(text) or (
        text.startswith("::addxp") and game.skills_["herblore"].update(level=3))
    h.setup()
    assert typed == ["::setqueststage 48 100", "::addxp herblore 250"]
    game.put("lobster")
    h.restock(game.inv, {})
    assert h.current == "attack_potion"
    assert sum(s["key"] == "guam_potion_(unf)" for s in game.inv) == 14
    assert sum(s["key"] == "eye_of_newt" for s in game.inv) == 13         # the lobster takes a slot
    supply = h.supply_slots(game.inv)
    assert supply
    used = []

    def use(ctx, gs, i, verb):
        used.append(game.inv[i]["key"])
        return True
    monkeypatch.setattr(actions, "use_slot", use)

    def mix(ctx, gs):                              # Make All: every pair becomes a potion
        for i, s in enumerate(game.inv):
            if s["key"] in ("guam_potion_(unf)", "eye_of_newt"):
                game.inv[i] = {"id": 121, "key": "attack_potion(3)", "name": "x"} if s["key"] == "eye_of_newt" \
                    else {"id": -1, "key": None, "name": None}
        return True
    monkeypatch.setattr(S, "make_all", mix)
    assert h.process(game.inv, supply, {}) == 14
    assert used == ["eye_of_newt", "guam_potion_(unf)"]
    assert h.supply_slots(game.inv) == []        # -> restock: the potions are dropped, food stays
    h.restock(game.inv, {})
    assert not any(s["key"] == "attack_potion(3)" for s in game.inv)
    assert any(s["key"] == "lobster" for s in game.inv)
    game.skills_["herblore"]["level"] = 40
    game.varps = lambda *ids: {80: 4}
    typed.clear()
    h.setup()
    assert typed == []                             # already unlocked
    assert h.best_supply(40) == "prayer_potion" and h.best_supply(2) is None


def test_runecrafting_teleports_by_level_and_crafts(game, monkeypatch):
    from lumberjack.core import interact
    from lumberjack.nav import places
    teles = []
    monkeypatch.setattr(places, "teleport", lambda ctx, gs, tile, plane=0: teles.append((tuple(tile), plane)) or True)
    monkeypatch.setattr(actions, "reset_camera", lambda ctx: None)
    r = bot(S.Runecrafter, game)
    r.setup()
    assert teles == [((2841, 4829), 0)] and r.came_from == (3200, 3200, 0)     # air altar at level 1
    r.restock(game.inv, {})
    assert sum(s["key"] == "pure_essence" for s in game.inv) == 28 and len(teles) == 1
    game.locs = lambda radius, name=None: [{"name": "Portal", "id": 2465, "ops": ["Use"], "tile": [2841, 4828],
                                            "screen": [300, 260], "body": [300, 240]},
                                           {"name": "Altar", "id": 2478, "ops": ["Craft-rune"], "tile": [2843, 4833],
                                            "screen": [300, 200], "body": [300, 180]}]

    def craft():                                    # Craft-rune clicked: every essence becomes a rune
        for i, s in enumerate(game.inv):
            if s["key"] == "pure_essence":
                game.inv[i] = {"id": -1, "key": None, "name": None}
        game.put("air_rune")
    hovers = []

    def move(x, y, *a, **k):
        hovers.append((x, y))
        # the game's reported point is floor ("Walk here"); the altar's model is a little off it
        game.top = "Craft-rune" if len(hovers) > 2 else "Walk here"
    r.inp.move, r.inp.click = move, craft
    monkeypatch.setattr(interact, "on_screen", lambda x, y, margin=4: True)
    assert r.process(game.inv, r.supply_slots(game.inv), {}) == 28
    game.skills_["runecrafting"]["level"] = 27
    r.restock(game.inv, {})                       # level 27: on to the cosmic altar, runes kept (one stack)
    assert teles[-1] == ((2162, 4833), 0)
    assert any(s["key"] == "air_rune" for s in game.inv)
    n = len(teles)
    r.teardown()
    assert len(teles) == n                                       # stays at the altar (retries come back here)


def test_thief_waits_out_combat_then_fights_back(game, monkeypatch):
    from lumberjack.core import interact
    t = bot(T.Thief, game)
    clock = {"t": 100.0}
    monkeypatch.setattr(T.time, "monotonic", lambda: clock["t"])
    me = {"tile": [3200, 3200], "plane": 0, "index": 1, "in_combat": True}
    game.player = lambda raw=False: me
    attacker = {"name": "Al-Kharid warrior", "ops": ["Attack", "Pickpocket"], "interacting": 32769,
                "in_combat": True, "screen": [300, 200], "body": [300, 190], "tile": [3201, 3200], "dist": 1}
    game.npcs_ = [attacker]
    attacks = []
    monkeypatch.setattr(interact, "use_option", lambda ctx, gs, pts, verb, subject: attacks.append(verb) or (1, 1))
    assert t.out_of_combat() and attacks == []              # first: wait (a catch's hit, likely)
    clock["t"] += T.COMBAT_WAIT_S + 1
    assert t.out_of_combat() and attacks == ["Attack"]      # still attacked: hit back
    me["in_combat"] = False
    assert not t.out_of_combat()                             # free again: pickpocket on
