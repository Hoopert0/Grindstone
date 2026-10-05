"""Offline tests for fire finding, cooking and the fish + cook task (no game, no input).

    python -m pytest lumberjack/tests/test_cooking.py
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

_stub = types.ModuleType("lumberjack.core.input")
_stub.VK_LEFT, _stub.VK_RIGHT, _stub.VK_UP, _stub.VK_DOWN, _stub.VK_SHIFT = 37, 39, 38, 40, 16


class _NoInput:
    def __init__(self, *a, **k):
        raise RuntimeError("tests must not create input")


_stub.AgentInput = _NoInput
sys.modules.setdefault("lumberjack.core.input", _stub)
assert not hasattr(sys.modules["lumberjack.core.input"], "__file__"), "real input module was already imported"

from lumberjack.core import regions as R  # noqa: E402
from lumberjack.core.regions import Rect  # noqa: E402
from lumberjack.skills import cooking as C  # noqa: E402
from lumberjack.skills import fish_cook as FC  # noqa: E402
from lumberjack.ui import menu  # noqa: E402
from lumberjack.vision import fire as F  # noqa: E402

GRASS_BGR = (40, 120, 110)
FLAME_BGR = (20, 140, 255)      # orange flame (HSV ~ 14/235/255)
SAND_BGR = (120, 170, 190)      # Draynor sand: same hue, dull


def _scene():
    f = np.zeros((503, 765, 3), np.uint8)
    f[:] = GRASS_BGR
    return f


def _flame(f, x, y, w=8, h=12, color=FLAME_BGR):
    f[y - h // 2:y + h // 2, x - w // 2:x + w // 2] = color


# ---- fire finder -----------------------------------------------------------------------
def test_flame_near_player_is_found():
    f = _scene()
    px, py = R.PLAYER.center
    _flame(f, px + 30, py + 5)
    c = F.find_fires(f)
    assert len(c) == 1, c
    assert abs(c[0].x - (px + 30)) <= 3 and abs(c[0].y - (py + 5)) <= 3


def test_dull_sand_far_fires_and_hover_text_ignored():
    f = _scene()
    px, py = R.PLAYER.center
    f[200:260, 300:400] = SAND_BGR                 # big sand patch - not saturated enough
    _flame(f, px + 240, py + 100)                  # a fire, but far beyond MAX_DIST
    f[8:18, 20:90] = FLAME_BGR                     # orange item name in the hover-text strip
    _flame(f, px, py, w=2, h=2)                    # a 4-px speck
    assert F.find_fires(f) == []


def test_nearest_first_and_flicker_bonus():
    a, b = _scene(), _scene()
    px, py = R.PLAYER.center
    for fr in (a, b):
        _flame(fr, px + 20, py)                    # near, static
    _flame(a, px - 45, py, color=(10, 120, 250))   # farther, flickering between frames
    _flame(b, px - 45, py, color=(40, 200, 255))
    c = F.find_fires(b)
    assert c[0].x > px                             # without a previous frame: nearest wins
    c = F.find_fires(b, prev=a)
    assert c[0].moving and c[0].x < px             # 45 px - 30 px bonus beats 20 px
    assert F.draw(b, c).shape == b.shape


# ---- cooking helpers -------------------------------------------------------------------
def test_kind_of():
    assert C.kind_of("eat", None) == "cooked"
    assert C.kind_of("use", "raw_shrimps") == "raw"
    assert C.kind_of("use", "willow_logs") == "log"
    assert C.kind_of("use", "burnt_fish") == "burnt"
    assert C.kind_of("use", None) == "burnt"
    assert C.kind_of(None, None) is None
    assert C.kind_of("wield", "rune_axe") is None


def test_missing_templates():
    every = lambda n: True
    assert C.missing_templates("drop", every) == []
    have = {"fire", "raw_shrimps"}.__contains__
    assert C.missing_templates("bank", have) == []
    assert C.missing_templates("drop", have) == []      # 'Eat' is learned from the first cooked fish
    assert len(C.missing_templates("drop", lambda n: False)) == 2


def test_fish_tables():
    assert set(C.RAW) == set(C.FISH)
    for n in C.LEARNABLE:
        assert n in C.RAW or n in C.COOKED or n in C.BURNT, n


def _cook_box_scene():
    f = _scene()
    f[R.CHATBOX.y:R.CHATBOX.y + R.CHATBOX.h, R.CHATBOX.x:R.CHATBOX.x + R.CHATBOX.w] = C.BEIGE
    f[340:470, 497:515] = (100, 110, 120)           # no dark scrollbar track
    return f


def test_cook_box_heuristic():
    f = _cook_box_scene()
    assert not C.cook_box_open(f)                  # parchment but no item picture
    f[395:420, 240:270] = (120, 140, 230)          # the fish picture
    assert C.cook_box_open(f)
    x, y = C.sprite_center(f)
    assert abs(x - 255) <= 2 and abs(y - 407) <= 2
    g = f.copy()
    g[455:465, 200:240] = (255, 0, 0)              # blue "Click here to continue" (level-up dialog)
    assert not C.cook_box_open(g)
    h = f.copy()
    h[350:440, 497:511] = (20, 20, 20)             # chat scrollbar = plain chat, not a dialog
    assert not C.cook_box_open(h)
    assert not C.cook_box_open(_scene())


def test_sprite_mask_skips_text():
    chat = np.zeros((40, 100, 3), np.uint8)
    chat[:] = C.BEIGE
    chat[5:9, 5:60] = (20, 20, 140)                # dark-red title text
    chat[20:24, 5:60] = (0, 0, 0)                  # black text
    assert C.sprite_mask(chat).sum() == 0


def _menu_frame(widths):
    f = _scene()
    m = Rect(200, 150, 160, menu.HEADER_H + 2 + menu.ROW_H * len(widths))
    f[m.y:m.y + m.h, m.x:m.x + m.w] = menu.BODY_BGR
    rows = menu.rows(m)
    for r, w in zip(rows, widths):
        for x in range(r.x + 3, r.x + 3 + w, 2):   # text-like stripes, not a solid block
            f[r.y + 4:r.y + 11, x] = 255
    return f, rows


def test_cook_all_row_is_widest():
    f, rows = _menu_frame([30, 30, 31, 44])        # Cook 1, Cook 5, Cook X, Cook All
    assert C.cook_all_row(f, rows) == 3
    f, rows = _menu_frame([44, 30, 30])
    assert C.cook_all_row(f, rows) == 0
    f, rows = _menu_frame([0, 0])
    assert C.cook_all_row(f, rows) is None


def test_changed_slots():
    a = _scene()
    before = {i: R.INV_SLOTS[i].crop(a).astype(np.int16) for i in (0, 1, 2)}
    b = a.copy()
    s = R.INV_SLOTS[1]
    cv2.circle(b, s.center, 9, (60, 90, 200), -1)  # slot 1's fish got cooked (new picture)
    assert C.changed_slots(before, b, [0, 1, 2]) == [1]


# ---- fish + cook task ------------------------------------------------------------------
def test_usable_trees():
    every = lambda n: True
    assert FC.usable_trees(["tree", "willow", "oak"], {}, every) == ["willow", "oak", "tree"]
    assert FC.usable_trees(["willow", "oak"], {"woodcutting": 20}, every) == ["oak"]
    assert FC.usable_trees(["willow", "oak"], {"woodcutting": 71, "firemaking": 25}, every) == ["oak"]
    no_logs = lambda n: n != "willow_logs"
    assert FC.usable_trees(["willow"], {}, no_logs) == []
    assert FC.usable_trees(["bush"], {}, every) == []


def test_remaining():
    assert FC.remaining([4, 5, 6, 7], [5, 7]) == [4, 6]
    assert FC.remaining([4], []) == [4]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok ", fn.__name__)
    print(f"{len(fns)} passed")


def test_cook_box_real_capture():
    """The cook box from the laptop (panel screenshot scaled back to 765x503): the chatbox
    border and the red title must not be taken for the item picture."""
    f = cv2.imread(str(Path(__file__).parent / "fixtures" / "cook_box_shrimps.jpg"))
    assert f.shape == (503, 765, 3)
    assert C.cook_box_open(f)
    x, y = C.sprite_center(f)
    assert 225 <= x <= 270 and 388 <= y <= 420, (x, y)      # on the shrimps picture


def test_continue_blue_matches_levelup_purple_not_parchment():
    from lumberjack import actions
    band = np.zeros((28, 500, 3), np.int16)
    band[:] = C.BEIGE
    assert actions.continue_blue(band).sum() == 0
    band[10:16, 200:330] = (200, 60, 70)        # the level-up dialog's blue-purple (BGR)
    assert actions.continue_blue(band).sum() >= actions.CONTINUE_MIN_PX
    band[10:16, 200:330] = (255, 0, 0)          # pure blue, as in other dialogs
    assert actions.continue_blue(band).sum() >= actions.CONTINUE_MIN_PX
    band[10:16, 200:330] = (20, 20, 140)        # dark red title text is not "continue"
    assert actions.continue_blue(band).sum() == 0


def test_use_on_fire_from_game_data(monkeypatch):
    clicks = []

    class Inp:
        def click(self, x=None, y=None):
            clicks.append("click")

        def move(self, x, y, steps=None):
            pass

    hovered = {"n": 0}

    class GS:
        def locs(self, radius, name=None):
            return [{"name": "Fire", "id": 2732, "ops": [], "tile": [1, 1], "dist": 1,
                     "screen": [250, 180], "body": [250, 120]},
                    {"name": "Fire pit", "id": 1, "ops": [], "tile": [2, 2], "dist": 2, "screen": [300, 180],
                     "body": [300, 120]}]

        def menu(self):
            hovered["n"] += 1
            top = ("Use", "Raw shrimps -> Fire") if hovered["n"] >= 2 else ("Walk here", "")
            return {"entries": [{"verb": top[0], "subject": top[1], "kind": "object", "row": 0}]}

    ctx = types.SimpleNamespace(inp=Inp(), sleep=lambda s: None)
    monkeypatch.setattr(C, "_deselect", lambda c: clicks.append("deselect"))
    assert C.use_on_fire_gs(ctx, GS(), 4) is True
    assert clicks == ["click", "click"]          # select the fish, then the fire (2nd hover point)
    class NoFire(GS):
        def locs(self, radius, name=None):
            return []
    assert C.use_on_fire_gs(ctx, NoFire(), 4) is None


def test_cooking_task_picks_fish_and_spawns_into_free_slots(monkeypatch):
    import types
    from lumberjack import items
    from lumberjack.core import backpack
    from lumberjack.skills import cooking_task as C
    assert C.best_raw(1) == "raw_shrimps" and C.best_raw(17) == "raw_trout" and C.best_raw(99) == "raw_swordfish"
    assert all(k in items.BY_KEY for k in C.RAW_LEVELS)
    ck = C.Cooker.__new__(C.Cooker)
    ck.ctx, ck.state = types.SimpleNamespace(sleep=lambda s: None), ""
    inv = [{"id": 590, "key": "tinderbox"}] + [{"id": -1, "key": None}] * 27
    monkeypatch.setattr(backpack, "slots", lambda: inv)
    asked = []

    def spawn(ctx, key, amount=1):
        asked.append((key, amount))
        for _ in range(amount):
            free = next((i for i, s in enumerate(inv) if s["id"] < 0), None)
            if free is None:
                return
            inv[free] = {"id": 1, "key": key}
    monkeypatch.setattr(items, "spawn", spawn)
    assert ck.spawn_fill("logs", 3) and ck.spawn_fill("raw_trout", 28)
    assert asked == [("logs", 3), ("raw_trout", 24)]
    assert sum(s["key"] == "raw_trout" for s in inv) == 24 and not ck.spawn_fill("raw_trout", 28)
