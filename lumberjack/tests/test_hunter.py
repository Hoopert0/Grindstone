"""Hunter: traps laid, checked and dismantled in a fake Feldip Hills."""
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
from lumberjack.nav import places  # noqa: E402
from lumberjack.skills import hunter_task as H  # noqa: E402
from lumberjack.skills.base import StopBot  # noqa: E402

SNARE = items.BY_KEY["bird_snare"][1]


class Field:
    def __init__(self, level=1):
        self.tile, self.level = [2608, 2927], level
        self.inv = [{"id": -1, "key": None} for _ in range(28)]
        self.traps = []                  # [id, tile, name, ops]

    def player(self):
        return {"tile": list(self.tile), "plane": 0}

    def locs(self, radius=15, name=None):
        return [{"id": t[0], "tile": t[1], "name": t[2], "ops": t[3], "screen": [300, 200], "body": [300, 190]}
                for t in self.traps]


@pytest.fixture
def field(monkeypatch):
    f = Field()
    monkeypatch.setattr(backpack, "slots", lambda: f.inv)
    monkeypatch.setattr(gamestate, "skill", lambda name: {"base": f.level})

    def spawn(ctx, key, n=1):
        for _ in range(n):
            i = next(i for i, s in enumerate(f.inv) if s["id"] < 0)
            f.inv[i] = {"id": items.BY_KEY[key][1], "key": key}
    monkeypatch.setattr(items, "spawn", spawn)

    def use_slot(ctx, gs, i, verb):
        assert verb == "Lay" and f.inv[i]["id"] == SNARE
        if any(t[1] == f.tile for t in f.traps):
            return True                                   # "You can't lay a trap here"
        f.inv[i] = {"id": -1, "key": None}
        f.traps.append([19175, list(f.tile), "Bird snare", ["Dismantle", "Investigate"]])
        f.tile = [f.tile[0] - 1, f.tile[1]]               # the game steps us off it
        return True
    monkeypatch.setattr(actions, "use_slot", use_slot)

    def use_option(ctx, gs, points, verb, subject):
        t = f.traps.pop(0)
        f.clicked = (verb, t[0])
        i = next(i for i, s in enumerate(f.inv) if s["id"] < 0)
        f.inv[i] = {"id": SNARE, "key": "bird_snare"}     # the trap comes back
        if verb == "Check":
            f.inv[next(i for i, s in enumerate(f.inv) if s["id"] < 0)] = {"id": 526, "key": "bones"}
        return 300, 200
    monkeypatch.setattr(interact, "use_option", use_option)
    monkeypatch.setattr(interact, "walk_to_tile", lambda ctx, gs, tile, arrive=2, max_clicks=15:
                        f.__setattr__("tile", list(tile)))
    monkeypatch.setattr(actions, "drop_known", lambda ctx, gs, slots: [f.inv.__setitem__(i, {"id": -1, "key": None})
                                                                        for i in slots])
    monkeypatch.setattr(places, "teleport", lambda ctx, gs, tile, plane=0: f.__setattr__("tile", list(tile)) or True)
    return f


def hunter(f):
    h = H.Hunter.__new__(H.Hunter)
    h.gs, h.state, h.log = f, "", __import__("logging").getLogger("t")
    h.ctx = types.SimpleNamespace(sleep=lambda s: None)
    h.inp = types.SimpleNamespace(move=lambda *a, **k: None, close=lambda: None)
    h.caught = h.laid = h.collapsed = h.lay_fails = 0
    h.spot = h.came_from = h.home = None
    h.sleep = lambda s: None
    return h


def test_lays_up_to_the_limit_then_checks_and_dismantles(field):
    h = hunter(field)
    h.go_to_spot()
    assert h.spot[1] == "crimson swift" and field.tile == [2608, 2927]
    h.tick()                                              # no snare: spawn one (max 1 at level 1)
    assert sum(s["id"] == SNARE for s in field.inv) == 1
    h.tick()
    assert len(field.traps) == 1 and h.laid == 1
    h.tick()                                              # the limit: wait
    assert len(field.traps) == 1 and h.state.startswith("waiting")
    field.traps[0] = [19180, field.traps[0][1], "Bird snare", ["Check", "Dismantle"]]   # a catch
    h.tick()
    assert field.clicked == ("Check", 19180) and h.caught == 1
    field.traps.append([19174, [2606, 2927], "Bird snare", ["Dismantle"]])           # collapsed
    h.tick()
    assert field.clicked == ("Dismantle", 19174) and h.collapsed == 1


def test_more_traps_with_level_and_steps_aside_when_a_tile_is_taken(field):
    field.level = 25                                      # wagtails, 2 traps
    h = hunter(field)
    h.go_to_spot()
    assert h.spot[1] == "tropical wagtail"
    for _ in range(4):
        h.tick()
    assert len(field.traps) == 2
    field.traps.clear()
    field.traps.append([19175, list(field.tile), "Bird snare", ["Dismantle"]])        # we stand on one
    h.tick()                                              # both snares are out there: spawn one
    before = list(field.tile)
    h.tick()
    assert h.lay_fails == 1 and field.tile != before                                   # stepped aside


def test_moves_up_to_chinchompas_and_drops_junk(field):
    field.level = 63
    h = hunter(field)
    h.go_to_spot()
    assert h.spot[1] == "red chinchompa" and h.spot[3] == "box_trap" and H.max_traps(63) == 4
    for i in range(25):
        field.inv[i] = {"id": 526, "key": "bones"}
    h.make_room()
    assert not any(s["id"] == 526 for s in field.inv)


def test_teleport_failure_stops(field, monkeypatch):
    monkeypatch.setattr(places, "teleport", lambda ctx, gs, tile, plane=0: False)
    with pytest.raises(StopBot, match="teleport"):
        hunter(field).go_to_spot()
