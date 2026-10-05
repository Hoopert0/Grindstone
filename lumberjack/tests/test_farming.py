"""Farming: patch states from varbits, and one patch's whole cycle in a fake Falador farm."""
import sys
import types

import pytest

_stub = types.ModuleType("lumberjack.core.input")
_stub.VK_LEFT, _stub.VK_RIGHT, _stub.VK_UP, _stub.VK_DOWN, _stub.VK_SHIFT = 0x25, 0x27, 0x26, 0x28, 0x10


class _NoInput:
    def __init__(self, *a, **k):
        raise RuntimeError("tests must never create AgentInput")


_stub.AgentInput = _NoInput
sys.modules.setdefault("lumberjack.core.input", _stub)

from lumberjack import actions, items  # noqa: E402
from lumberjack.core import backpack, gamestate, interact  # noqa: E402
from lumberjack.skills import farming_task as F  # noqa: E402
from lumberjack.skills.base import StopBot  # noqa: E402


def test_states_from_the_servers_values():
    assert [F.herb_state(v) for v in (0, 2, 3, 4, 7, 8, 32, 36, 130, 171)] == \
        ["weeds", "weeds", "empty", "growing", "growing", "grown", "growing", "grown", "diseased", "dead"]
    assert [F.allotment_state(v) for v in (1, 3, 6, 9, 10, 6 | 0x80, 8 | 0x40, 40)] == \
        ["weeds", "empty", "growing", "growing", "grown", "diseased", "dead", "grown"]
    assert F.best_seed("allotment", 1)[1] == "potato_seed" and F.best_seed("herb", 8) is None
    assert F.best_seed("herb", 33)[1] == "ranarr_seed"


class Farm:
    """One allotment (varbit 708) that reacts to the bot like the server would."""

    def __init__(self):
        self.v, self.level, self.typed = 0, 1, []
        self.inv = [{"id": -1, "key": None} for _ in range(28)]
        self.selected = None

    def player(self):
        return {"tile": [3054, 3307], "plane": 0}

    def locs(self, radius=15, name=None):
        return [{"id": 8550, "name": "Allotment", "varbit": 708, "ops": ["Inspect", "Guide"], "tile": [3052, 3309],
                 "screen": [300, 200], "body": [300, 190]}]

    def varbits(self, *ids):
        return {708: self.v}

    def menu(self):
        return {"open": False, "entries": [{"verb": "Use", "subject": f"{self.selected} -> Allotment", "row": 0}]}

    def use(self, item):                       # an item used on the patch
        if item == "Rake" and self.v < 3:
            self.v = 3
        elif item == "Potato seed" and self.v == 3:
            self.v = 6


@pytest.fixture
def farm(monkeypatch):
    f = Farm()
    monkeypatch.setattr(backpack, "slots", lambda: f.inv)
    monkeypatch.setattr(gamestate, "skill", lambda name: {"base": f.level})

    def spawn(ctx, key, n=1):
        i = next(i for i, s in enumerate(f.inv) if s["id"] < 0)
        f.inv[i] = {"id": items.BY_KEY[key][1], "key": key, "name": items.BY_KEY[key][0]}
    monkeypatch.setattr(items, "spawn", spawn)

    def use_slot(ctx, gs, i, verb):
        f.selected = f.inv[i]["name"]
        return True
    monkeypatch.setattr(actions, "use_slot", use_slot)
    monkeypatch.setattr(actions, "cancel_selection", lambda ctx, force=False: None)
    monkeypatch.setattr(actions, "drop_known", lambda ctx, gs, slots: None)
    monkeypatch.setattr(interact, "on_screen", lambda x, y, margin=4: True)

    def harvest(ctx, gs, points, verb, subject):
        f.harvested = verb
        f.v = 3                                  # picked clean
        return 300, 200
    monkeypatch.setattr(interact, "use_option", harvest)
    return f


def farmer(f):
    b = F.Farmer.__new__(F.Farmer)
    b.gs, b.state, b.log = f, "", __import__("logging").getLogger("t")
    b.ctx = types.SimpleNamespace(sleep=lambda s: None)

    def click():
        f.use(f.selected)
    b.inp = types.SimpleNamespace(move=lambda *a: None, click=click, close=lambda: None,
                                  type_text=lambda t, enter=False: f.typed.append(t))
    b.sleep = lambda s: None
    b.harvests = b.planted = 0
    b.composted, b.last_grow, b.came_from = set(), 0.0, None
    return b


def test_one_allotment_cycle(farm):
    b = farmer(farm)
    b.tick()
    assert farm.v == 3                                    # raked
    b.tick()
    assert 708 in b.composted and farm.selected == "Supercompost"
    b.tick()
    assert farm.v == 6 and b.planted == 1                 # potatoes in
    b.tick()
    assert farm.typed == ["::grow"]                       # growing: ::grow
    farm.v = 10
    b.tick()
    assert farm.harvested == "Harvest" and b.harvests == 1 and 708 not in b.composted


def test_no_patches_means_a_clear_stop(farm):
    farm.locs = lambda radius=15, name=None: []
    with pytest.raises(StopBot, match="no farming patches"):
        farmer(farm).tick()
