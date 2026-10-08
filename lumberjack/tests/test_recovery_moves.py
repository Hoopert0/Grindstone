"""Getting unstuck: dialogs read before closing, unreachable targets skipped, moving for a clear
shot, and chat a bot handles itself."""
import sys
import types

_stub = types.ModuleType("lumberjack.core.input")
_stub.VK_LEFT, _stub.VK_RIGHT, _stub.VK_UP, _stub.VK_DOWN, _stub.VK_SHIFT = 0x25, 0x27, 0x26, 0x28, 0x10


class _NoInput:
    def __init__(self, *a, **k):
        raise RuntimeError("tests must never create AgentInput")


_stub.AgentInput = _NoInput
sys.modules.setdefault("lumberjack.core.input", _stub)

import pytest  # noqa: E402

from lumberjack.core import interact  # noqa: E402
from lumberjack.ui import widgets  # noqa: E402


class Chat:
    def __init__(self):
        self.lines, self.count = [], 0

    def say(self, text):
        self.lines.insert(0, {"type": 0, "text": text})
        self.count += 1

    def chat(self, n=20):
        return {"count": self.count, "lines": self.lines[:n]}


def test_a_dialog_is_read_before_it_is_closed(monkeypatch):
    comps = [{"if": 210, "text": "You don't have any feathers left.", "w": 400, "h": 20, "x": 0, "y": 0},
             {"if": 210, "text": "Click here to continue", "w": 200, "h": 20, "x": 0, "y": 30}]
    monkeypatch.setattr(widgets, "find", lambda gs, m=None: [c for c in comps if m is None or m.isdigit()
                                                              or m.lower() in c["text"].lower()])
    monkeypatch.setattr(widgets, "_no_dialog_at", float("-inf"))
    clicks = []
    ctx = types.SimpleNamespace(inp=types.SimpleNamespace(click=lambda x, y: clicks.append((x, y))),
                                sleep=lambda s: None)
    assert widgets.continue_dialog(ctx, object())
    assert widgets.last_dialog == "You don't have any feathers left." and clicks


def test_fishing_gets_its_tools_again_when_the_game_says_they_ran_out(monkeypatch):
    from lumberjack.skills import fishing as F
    from lumberjack.skills.base import StopBot
    f = F.Fisher.__new__(F.Fisher)
    f.method, f.spawn_tools, f.failed_clicks = "lure", True, 4
    f.log = __import__("logging").getLogger("t")
    got = []
    f.ensure_tools = lambda: got.append(1)
    monkeypatch.setattr(widgets, "last_dialog", "You don't have any feathers left.")
    f.after_dialog()
    assert got == [1] and f.failed_clicks == 0 and widgets.last_dialog == ""
    monkeypatch.setattr(widgets, "last_dialog", "You need a Fishing level of at least 30 to lure these fish.")
    with pytest.raises(StopBot, match="Fishing level"):
        f.after_dialog()


def test_thieving_leaves_an_unreachable_target_alone(monkeypatch):
    from lumberjack import actions
    from lumberjack.skills import thieving_task as T
    gs = Chat()
    gs.skills = lambda: {"hitpoints": {"boosted": 20, "level": 20}, "thieving": {"xp": 100}}
    warriors = [{"name": "Al-Kharid warrior", "ops": ["Attack", "Pickpocket"], "dist": d, "index": i,
                 "screen": [300, 200], "tile": [3290, 3168]} for i, d in ((7, 1), (8, 3))]
    gs.npcs = lambda name=None: warriors
    t = T.Thief.__new__(T.Thief)
    t.gs, t.state, t.targets = gs, "", ["al-kharid_warrior"]
    t.stolen = t.caught = 0
    t.ctx, t.sleep, t.log = types.SimpleNamespace(sleep=lambda s: None), lambda s: None, __import__("logging").getLogger("t")
    monkeypatch.setattr(actions, "dismiss_dialog", lambda ctx: False)
    monkeypatch.setattr(interact, "on_screen", lambda x, y, margin=4: True)
    monkeypatch.setattr(interact, "use_option", lambda *a: gs.say("I can't reach that.") or (300, 200))
    n = t.pick_target()
    assert n["index"] == 7
    t.attempt(n)
    assert t.pick_target()["index"] == 8 and t.stolen == 0 and t.caught == 0


def test_combat_moves_for_a_clear_shot_before_giving_up(monkeypatch):
    from lumberjack.skills import combat
    f = combat.Fighter.__new__(combat.Fighter)
    f.gs = types.SimpleNamespace(player=lambda: {"tile": [3250, 3238]})
    f.target_index, f.last_click, f.kill_tile, f.train = 5, None, None, "ranged"
    f.home_tile, f.ctx, f.state = [3250, 3238], None, ""
    f._gs_call = lambda fn, *a: "no_engage"
    f.equip_ranged = lambda: None
    walks = []
    monkeypatch.setattr(interact, "walk_to_tile", lambda ctx, gs, tile, arrive=2, max_clicks=15: walks.append(tile))
    for _ in range(combat.REPOSITION_EVERY):
        f.target_index = 5
        f.fight()
    assert len(walks) == 1 and abs(walks[0][0] - 3250) <= 3 and f.skip_until == {}


def test_chat_a_bot_handles_itself_never_stops_it():
    from lumberjack.skills import watch

    class Stop(Exception):
        pass
    gs, bot = Chat(), types.SimpleNamespace(chat_handled=("can't light a fire here",))
    watch.check_chat(bot, gs, 0.0, Stop)
    for i in range(watch.REPEAT_LIMIT + 3):
        gs.say("You can't light a fire here.")
        watch.check_chat(bot, gs, 1.0 + i, Stop)              # 9 times in 10 s: still running


def test_a_fishing_level_up_is_not_a_reason_to_stop(monkeypatch):
    from lumberjack.skills import fishing as F
    f = F.Fisher.__new__(F.Fisher)
    f.method, f.spawn_tools, f.failed_clicks = "lure", True, 0
    f.log = __import__("logging").getLogger("t")
    f.ensure_tools = lambda: None
    monkeypatch.setattr(widgets, "last_dialog", "Congratulations, you've just advanced a Fishing level! "
                                                "Your Fishing level is now 26.")
    f.after_dialog()                                         # no StopBot


def test_leftover_herblore_and_summoning_items_are_droppable():
    from lumberjack.core import backpack
    for k in ("attack_potion(3)", "harralander_potion_(unf)", "chocolate_dust", "guam_leaf",
              "spirit_wolf_pouch", "red_spiders'_eggs"):
        assert backpack.is_product(k), k
    for k in ("tinderbox", "steel_axe", "fly_fishing_rod", "feather", "coins"):
        assert not backpack.is_product(k), k


def test_thieving_doesnt_stop_when_food_is_there_but_eating_missed(monkeypatch):
    from lumberjack import actions
    from lumberjack.core import backpack
    from lumberjack.skills import thieving_task as T
    t = T.Thief.__new__(T.Thief)
    t.gs = types.SimpleNamespace(skills=lambda: {"hitpoints": {"boosted": 16, "level": 36}, "thieving": {"xp": 0}})
    t.eat_below, t.spawn_tools, t.eaten, t.state = 0.5, True, 0, ""
    t.ctx, t.sleep, t.log = None, lambda s: None, __import__("logging").getLogger("t")
    monkeypatch.setattr(backpack, "slots", lambda: [{"id": 379, "key": "lobster"}])
    monkeypatch.setattr(actions, "dismiss_dialog", lambda ctx: False)
    monkeypatch.setattr(actions, "open_tab", lambda ctx, name: None)
    monkeypatch.setattr(actions, "use_slot", lambda *a: False)       # stunned: Eat never takes
    t.ensure_hp()                                                    # waits, no StopBot


def test_the_tinderbox_is_only_used_on_logs_never_a_freshly_spawned_axe(monkeypatch):
    from lumberjack.core import backpack
    from lumberjack.skills import firemaking, fish_cook
    inv = [{"id": 590, "key": "tinderbox"}, {"id": 335, "key": "raw_trout"}, {"id": 1353, "key": "steel_axe"},
           {"id": 1511, "key": "logs"}] + [{"id": -1, "key": ""}] * 24
    monkeypatch.setattr(backpack, "slots", lambda: inv)
    cls = next(v for v in vars(fish_cook).values() if isinstance(v, type) and hasattr(v, "make_fire"))
    b = cls.__new__(cls)
    b.ctx, b.state, b.burned, b.tinder_slot, b.keep_slots = None, "", 0, 5, {5}
    b.log = __import__("logging").getLogger("t")
    used = []
    monkeypatch.setattr(firemaking, "light_one", lambda ctx, t, l: used.append((t, l)) or True)
    monkeypatch.setattr(fish_cook.actions, "dismiss_dialog", lambda ctx: False)
    assert b.make_fire([2, 3]) == []
    assert used == [(0, 3)] and b.tinder_slot == 0 and b.keep_slots == {0}


def test_a_spawned_axe_in_the_last_slot_still_leaves_room_for_fire_logs(monkeypatch):
    from lumberjack import actions
    from lumberjack.core import backpack
    from lumberjack.skills import fish_cook
    inv = ([{"id": 590, "key": "tinderbox"}, {"id": 1357, "key": "adamant_axe"}]
           + [{"id": 349, "key": "raw_trout"}] * 24 + [{"id": 343, "key": "burnt_fish"}] * 2)
    monkeypatch.setattr(backpack, "slots", lambda: inv)
    dropped = []
    monkeypatch.setattr(actions, "drop_known", lambda ctx, gs, slots: dropped.extend(slots))
    cls = next(v for v in vars(fish_cook).values() if isinstance(v, type) and hasattr(v, "make_fire"))
    b = cls.__new__(cls)
    b.ctx, b.gs, b.keep_slots, b.log = None, None, {0, 1}, __import__("logging").getLogger("t")
    b.make_log_room()
    assert sorted(dropped) == [26, 27]                     # the burnt fish go first


def test_fishing_keeps_going_while_fish_still_come_in(monkeypatch):
    import time
    from lumberjack.skills import fishing as F
    from lumberjack.skills.base import StopBot
    f = F.Fisher.__new__(F.Fisher)
    f.failed_clicks, f.method, f.started = F.MAX_FAILED_CLICKS - 1, "lure", time.monotonic() - 3600
    f.last_catch_at = time.monotonic() - 30                # caught one half a minute ago
    f.keep_slots, f.logs_cut, f.drop_at = set(), 0, 26
    monkeypatch.setattr(F.inventory, "occupied", lambda frame: [False] * 28)
    f.grab, f.sleep = lambda: None, lambda s: None
    f.activity = types.SimpleNamespace(reset=lambda: None, update=lambda fr: None, active=False, filled=True)
    f.busy_from_game = lambda: False
    monkeypatch.setattr(F, "FISH_TIMEOUT_S", 0)
    f.fish()                                               # 6th dud click: no StopBot yet
    f.last_catch_at = time.monotonic() - F.NO_CATCH_STOP_S - 1
    with pytest.raises(StopBot, match="tries without a catch"):
        f.fish()


def test_cooking_picks_a_fire_from_the_menu_when_we_stand_over_it(monkeypatch):
    from lumberjack.core import interact
    from lumberjack.skills import cooking
    fire = {"name": "Fire", "screen": [300, 200], "dist": 1}
    entries = [{"verb": "Walk here", "subject": "", "row": 0},
               {"verb": "Use", "subject": "Raw trout -> Fire", "row": 1}]
    state = {"open": False}
    gs = types.SimpleNamespace(locs=lambda r, n: [fire],
                               menu=lambda: {"open": state["open"], "entries": entries})
    clicks = []
    ctx = types.SimpleNamespace(sleep=lambda s: None, inp=types.SimpleNamespace(
        click=lambda *a: clicks.append(a), move=lambda *a, **k: None,
        right_click=lambda *a: state.update(open=True)))
    monkeypatch.setattr(interact, "on_screen", lambda x, y, margin=4: True)
    monkeypatch.setattr(interact, "points_for", lambda t: [(300, 200)])
    import lumberjack.core.gamestate as G
    monkeypatch.setattr(G, "menu_row_point", lambda m, row: (310, 230 + 15 * row))
    assert cooking.use_on_fire_gs(ctx, gs, 5) is True
    assert any(c and abs(c[1] - 245) <= 1 for c in clicks)  # the "Use ... -> Fire" row, not Walk here


def test_a_fresh_fire_gets_a_second_look_before_another_is_lit(monkeypatch):
    """"Lit a fire / Cancelling a selected item / Lit a fire" - about every other load lit two
    fires: the first wasn't usable yet while we stepped off it. Look again once first."""
    from lumberjack.skills import cooking, fish_cook
    cls = next(v for v in vars(fish_cook).values() if isinstance(v, type) and hasattr(v, "make_fire"))
    b = cls.__new__(cls)
    b.ctx, b.gs, b.keep_slots, b.state = None, None, set(), ""
    b.log, b.sleep = __import__("logging").getLogger("t"), lambda s: None
    monkeypatch.setattr(cooking, "sort_backpack", lambda ctx, keep: {"raw": [5, 6], "log": [7]})
    b.cookable = lambda raw: raw
    answers = ["no_fire", "no_fire", "ok"]               # none yet / the new one not ready / cooked
    monkeypatch.setattr(cooking, "cook", lambda ctx, raw: (answers.pop(0), list(raw)))
    monkeypatch.setattr(cooking, "still_raw", lambda ctx, raw: [])
    fires = []
    b.make_fire = lambda logs: fires.append(1) or []
    monkeypatch.setattr(fish_cook.mouseover, "available", lambda name: True)
    b.cook_load()
    assert fires == [1] and answers == []
