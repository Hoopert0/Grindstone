"""ui.widgets with a fake game: dialogs and make boxes from the client's interfaces."""
import types

from lumberjack.core.gamestate import GameStateError
from lumberjack.ui import widgets


class Ctx:
    def __init__(self):
        self.clicks, self.rclicks = [], []
        self.inp = types.SimpleNamespace(click=lambda x=None, y=None: self.clicks.append((x, y)),
                                         right_click=lambda x, y: self.rclicks.append((x, y)),
                                         move=lambda *a, **k: None)

    def sleep(self, s):
        pass


def W(text="", ops=(), x=0, y=0, w=40, h=20, obj=-1):
    return {"if": 1, "idx": 1, "type": 4, "x": x, "y": y, "w": w, "h": h, "text": text, "option": None,
            "ops": list(ops), "obj": obj, "count": 1}


class GS:
    def __init__(self, ws, menu=None):
        self.ws, self.menu_ = ws, menu or {"open": False, "entries": []}

    def _q(self, cmd):
        match = cmd[len("widgets "):].lower() if " " in cmd else None
        return [dict(w) for w in self.ws if not match or match in (w["text"] + " " + " ".join(w["ops"])).lower()]

    def menu(self):
        return self.menu_


def setup_function():
    widgets._unsupported = False


def test_continue_dialog_clicks_the_text():
    ctx = Ctx()
    gs = GS([W("<col=0000ff>Click here to continue", x=100, y=440, w=200, h=12)])
    assert widgets.continue_dialog(ctx, gs)
    (x, y), = ctx.clicks
    assert 192 <= x <= 208 and 445 <= y <= 447
    assert not widgets.continue_dialog(Ctx(), GS([W("Hello")]))


def test_make_picks_all_left_or_right_click():
    ctx = Ctx()
    gs = GS([W("How many?"), W(ops=["Make All", "Make 1"], x=200, y=380)])
    assert widgets.make(ctx, gs) and len(ctx.clicks) == 1 and not ctx.rclicks     # first option: left-click
    menu = {"open": True, "x": 150, "y": 300, "w": 100, "entries": [{"verb": "Make 1", "row": 0},
                                                                      {"verb": "Make All", "row": 1}]}
    ctx = Ctx()
    gs = GS([W(ops=["Make 1", "Make 5", "Make All"], x=200, y=380)], menu)
    assert widgets.make(ctx, gs) and len(ctx.rclicks) == 1 and len(ctx.clicks) == 1


def test_make_picks_the_product_by_label():
    ctx = Ctx()
    gs = GS([W("Oak shortbow (u)", x=60, y=420, w=80, h=12), W("Oak longbow (u)", x=260, y=420, w=80, h=12),
             W(ops=["Make 10", "Make 1"], x=80, y=380), W(ops=["Make 10", "Make 1"], x=280, y=380)])
    assert widgets.make(ctx, gs, product="oak longbow", amounts=("10",))
    (x, _), = ctx.clicks
    assert 290 <= x <= 310


def test_old_addon_falls_back_quietly():
    class Old:
        def _q(self, cmd):
            raise GameStateError("unknown state 'widgets'")
    assert widgets.find(Old()) == [] and widgets._unsupported
    assert widgets.make_box(Old()) == [] and not widgets.continue_dialog(Ctx(), Old())


def test_make_smithing_buttons_one_per_amount():
    """The smithing screen gives each amount its own button (the server's SmithingType ids) -
    pick the product's 'All' button, not the nearest '1'."""
    ctx = Ctx()
    gs = GS([W("Dagger", x=20, y=60, w=50, h=12), W("Axe", x=120, y=60, w=50, h=12),
             W(ops=["Make 1"], x=20, y=30, w=10, h=10), W(ops=["Make 5"], x=32, y=30, w=10, h=10),
             W(ops=["Make X"], x=44, y=30, w=10, h=10), W(ops=["Make All"], x=56, y=30, w=10, h=10),
             W(ops=["Make 1"], x=120, y=30, w=10, h=10), W(ops=["Make All"], x=156, y=30, w=10, h=10)])
    assert widgets.make(ctx, gs, product="dagger", amounts=("All", "10", "5"))
    (x, _), = ctx.clicks
    assert 56 <= x <= 66


def test_make_stacked_buttons_use_the_menu():
    menu = {"open": True, "x": 0, "y": 0, "w": 100, "entries": [{"verb": "Make 1", "row": 0},
                                                                {"verb": "Make All", "row": 1}]}
    ctx = Ctx()
    gs = GS([W("Dagger", x=20, y=60, w=50, h=12),
             W(ops=["Make 1"], x=20, y=30), W(ops=["Make All"], x=20, y=30),     # same spot
             W(ops=["Make 1"], x=220, y=30)], menu)
    assert widgets.make(ctx, gs, product="dagger", amounts=("All",))
    assert len(ctx.rclicks) == 1 and len(ctx.clicks) == 1


def test_make_one_item_dialogue_with_stacked_buttons():
    """The server's one-item skill dialogue (309): Make 1 / 5 / X / All as separate buttons on the
    item picture - choose All from the right-click menu."""
    menu = {"open": True, "x": 0, "y": 0, "w": 100, "entries": [{"verb": "Make 1", "row": 0},
                                                                {"verb": "Make 5", "row": 1},
                                                                {"verb": "Make X", "row": 2},
                                                                {"verb": "Make All", "row": 3}]}
    ctx = Ctx()
    gs = GS([W("How many would you like to make?", x=0, y=380, w=500, h=12),
             W(ops=["Make 1"], x=200, y=400), W(ops=["Make 5"], x=200, y=400),
             W(ops=["Make X"], x=200, y=400), W(ops=["Make All"], x=200, y=400)], menu)
    assert widgets.make(ctx, gs)
    assert len(ctx.rclicks) == 1 and len(ctx.clicks) == 1 and ctx.clicks[0][1] > 0
