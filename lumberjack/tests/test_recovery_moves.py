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
