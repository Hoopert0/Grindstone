"""nav.places.teleport: ::tele, and once more after clearing a dialog that swallowed the typing."""
import types

from lumberjack.nav import places
from lumberjack.ui import widgets


class Game:
    def __init__(self, swallow=1):
        self.tile, self.swallow, self.typed, self.cleared = [3222, 3218], swallow, [], 0
        self.clock = 0.0

    def player(self):
        return {"tile": list(self.tile), "plane": 0}


def setup(monkeypatch, g):
    def type_text(text, enter=False):
        g.typed.append(text)
        if g.swallow:
            g.swallow -= 1
            return
        x, y, _ = map(int, text.split()[1:])
        g.tile = [x, y]

    def sleep(s):
        g.clock += s
    ctx = types.SimpleNamespace(inp=types.SimpleNamespace(move=lambda *a, **k: None, type_text=type_text),
                                sleep=sleep)
    monkeypatch.setattr(places.time, "monotonic", lambda: g.clock)
    monkeypatch.setattr(widgets, "continue_dialog", lambda ctx, gs: g.__setattr__("cleared", g.cleared + 1) or False)
    from lumberjack import actions
    monkeypatch.setattr(actions, "reset_camera", lambda ctx: None)
    return ctx


def test_a_swallowed_tele_is_typed_again_after_clearing_dialogs(monkeypatch):
    g = Game(swallow=1)
    ctx = setup(monkeypatch, g)
    assert places.teleport(ctx, g, (2474, 3437), 0)
    assert g.typed == ["::tele 2474 3437 0"] * 2 and g.cleared == 1 and g.tile == [2474, 3437]


def test_gives_up_after_the_second_try(monkeypatch):
    g = Game(swallow=5)
    ctx = setup(monkeypatch, g)
    assert not places.teleport(ctx, g, (2474, 3437), 0)
    assert len(g.typed) == 2


def test_first_try_lands(monkeypatch):
    g = Game(swallow=0)
    ctx = setup(monkeypatch, g)
    assert places.teleport(ctx, g, (3097, 3509), 0) and len(g.typed) == 1 and g.cleared == 0
