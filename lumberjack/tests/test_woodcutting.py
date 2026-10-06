"""Woodcutting with game data: trees other players fell, and waiting for respawns."""
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

from lumberjack.skills import woodcutting as W  # noqa: E402


def tree(id_, ops=("Chop down", "Examine")):
    return {"id": id_, "name": "Oak", "ops": list(ops), "tile": [3200, 3210], "dist": 4,
            "screen": [300, 200], "body": [300, 150]}


def cutter(monkeypatch, gs):
    w = W.Woodcutter.__new__(W.Woodcutter)
    w.gs, w.state, w.trees, w.blocked, w.fail_streak = gs, "", ["oak"], {}, {}
    w.logs_cut, w.taken, w.no_start_streak, w.dry_since, w.drop_at = 0, 0, 0, None, 28
    clock = {"t": 0.0}
    monkeypatch.setattr(W.time, "monotonic", lambda: clock["t"])
    w.sleep = lambda s: clock.__setitem__("t", clock["t"] + s)
    w.grab = lambda: None
    w.activity = types.SimpleNamespace(reset=lambda: None, update=lambda f: None, active=True, filled=False)
    w.stats = lambda: ""
    monkeypatch.setattr(W.inventory, "count", lambda frame: 3)
    return w, clock


def test_a_tree_felled_by_someone_else_is_left_at_once(monkeypatch):
    looks = {"n": 0}

    class GS:
        def locs(self, radius=18, name=None):
            looks["n"] += 1
            return [tree(1281) if looks["n"] < 3 else tree(1356, ops=("Examine",))]   # a stump now

        def player(self):
            return {"anim": -1, "moving": True}
    w, clock = cutter(monkeypatch, GS())
    w.last_tree = tree(1281)
    w.chop("oak")
    assert w.taken == 1 and w.fail_streak == {} and clock["t"] < 5


def test_all_felled_waits_then_stops(monkeypatch):
    w, clock = cutter(monkeypatch, object())
    w.wait_for_respawn()
    assert w.state.startswith("waiting") and w.dry_since == 0.0
    clock["t"] = W.RESPAWN_PATIENCE_S + 1
    with pytest.raises(W.StopBot, match="standing here"):
        w.wait_for_respawn()
