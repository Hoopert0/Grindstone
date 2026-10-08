"""Offline tests for the fishing module (no game, no input).

    %USERPROFILE%\\.venvs\\lumberjack\\Scripts\\python.exe -m lumberjack.tests.test_fishing
    (or: python -m pytest lumberjack/tests/test_fishing.py)

lumberjack.core.input is replaced by a stub before importing, so nothing can talk to the
game's input agent.
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
# another test file's stub may already be in place (pytest runs them in one process) - fine,
# as long as it isn't the real module, which always has a __file__
assert not hasattr(sys.modules["lumberjack.core.input"], "__file__"), "real input module was already imported"

from lumberjack.core import regions as R  # noqa: E402
from lumberjack.skills import fishing as F  # noqa: E402
from lumberjack.vision import fishing_spots as S  # noqa: E402

GRASS_BGR = (40, 120, 110)      # olive Draynor grass
WATER_BGR = (112, 84, 67)       # measured Draynor water (HSV ~108/102/112)
FOAM_BGR = (235, 230, 225)


def _scene():
    f = np.zeros((503, 765, 3), np.uint8)
    f[:] = GRASS_BGR
    f[240:338, 150:516] = WATER_BGR             # shore: water along the bottom of the view
    return f


def _ring(f, x, y, r=5):
    cv2.circle(f, (x, y), r, FOAM_BGR, 1)
    cv2.circle(f, (x + 9, y + 4), r - 1, FOAM_BGR, 1)


# ---- vision ----------------------------------------------------------------------------
def test_water_mask_finds_water_only():
    f = _scene()
    f[20:30, 20:30] = WATER_BGR                  # small blue patch (clothing) - too small
    w = S.water_mask(f)
    vx, vy = R.VIEWPORT.x, R.VIEWPORT.y
    assert w[300 - vy, 300 - vx] > 0
    assert w[25 - vy, 25 - vx] == 0
    assert w[100 - vy, 100 - vx] == 0


def test_ring_on_water_is_found():
    f = _scene()
    _ring(f, 330, 290)
    c = S.find_fishing_spots(f)
    assert len(c) == 1, c
    assert abs(c[0].x - 334) <= 8 and abs(c[0].y - 292) <= 8


def test_white_on_grass_and_solid_blobs_rejected():
    f = _scene()
    cv2.circle(f, (200, 100), 6, FOAM_BGR, 1)    # white ring on grass, far from water
    f[280:300, 400:420] = FOAM_BGR               # solid white block on water (not a ripple)
    assert S.find_fishing_spots(f) == []


def test_sorted_by_distance_and_merge():
    f = _scene()
    _ring(f, 260, 260)                           # nearer the player (256, 172)
    _ring(f, 480, 320)
    c = S.find_fishing_spots(f)
    assert len(c) == 2 and c[0].dist < c[1].dist
    m = S.merge(c, [c[0]], [])
    assert len(m) == 2


def test_moving_bonus_with_two_frames():
    a, b = _scene(), _scene()
    _ring(a, 260, 262)
    _ring(b, 260, 262, r=6)                      # ring animated between frames
    _ring(a, 480, 320)
    _ring(b, 480, 320)
    c = S.find_fishing_spots(b, prev=a)
    assert c[0].moving
    assert S.draw(b, c).shape == b.shape


def test_no_water_no_spots():
    f = np.zeros((503, 765, 3), np.uint8)
    f[:] = GRASS_BGR
    assert S.find_fishing_spots(f) == [] and not S.has_water(f)


# ---- skill logic -----------------------------------------------------------------------
def test_click_plan():
    assert F.click_plan("net", "net", False) == "left"
    assert F.click_plan("net", "bait", True) == "menu"
    assert F.click_plan("net", "bait", False) == "menu"     # learns the 'Bait' row itself
    assert F.click_plan("net", "lure", False) is None       # net spots don't offer lure
    assert F.click_plan("cage", "harpoon", False) == "menu"
    assert F.click_plan(None, "net", True) is None


def test_methods_to_switch():
    none_missing = lambda m: []
    # level 5 at a net spot: bait (same spot, menu learned on the fly)
    assert F.methods_to_switch(5, list(F.METHOD_LEVELS), "net", "net", set(), none_missing) == ("bait", {})
    # level 25 at a net spot, no lure spot saved: stay on bait, say why lure isn't used
    best, blocked = F.methods_to_switch(25, list(F.METHOD_LEVELS), "bait", "net", set(), none_missing)
    assert best == "bait" and set(blocked) == {"lure"}
    # ... with a lure spot on the map: switch
    assert F.methods_to_switch(25, list(F.METHOD_LEVELS), "bait", "net", {"lure"}, none_missing)[0] == "lure"
    # lure not calibrated: blocked even with a spot
    miss = lambda m: ["mouseover/lure"] if m == "lure" else []
    best, blocked = F.methods_to_switch(25, list(F.METHOD_LEVELS), "bait", "net", {"lure"}, miss)
    assert best == "bait" and "not calibrated" in blocked["lure"]
    # spot not seen yet: anything calibrated counts
    assert F.methods_to_switch(7, ["net", "bait"], "net", None, set(), none_missing)[0] == "bait"
    # level 1: net only
    assert F.methods_to_switch(1, list(F.METHOD_LEVELS), "net", "net", set(), none_missing) == ("net", {})


def test_missing_now_ignores_menu_word():
    av = {"fishing_spot", "net"}.__contains__
    assert F.missing_now("bait", av) == []
    assert F.missing_now("lure", av) == ["mouseover/lure"]


def test_pick_method():
    assert F.pick_method(["net", "bait"], 3) == "net"
    assert F.pick_method(["net", "bait"], 5) == "bait"
    assert F.pick_method(["cage", "harpoon"], 36) == "harpoon"
    assert F.pick_method(["cage"], 10) is None
    assert F.pick_method(["lure", "net"], None) == "lure"


def test_counts():
    occ = [True, True, True, False] + [False] * 24
    assert F.catch_count(occ, {0, 1}) == 1
    assert F.emptied_kept(occ, {0, 3}) == [3]
    assert F.emptied_kept(occ, {0, 1}) == []


def test_missing_templates():
    have = {"fishing_spot", "net"}
    av = have.__contains__
    assert F.missing_templates("net", av, lambda n: False) == []
    assert F.missing_templates("bait", av, lambda n: False) == ["menu/bait"]
    assert F.missing_templates("bait", av, lambda n: n == "bait") == []
    m = F.missing_templates("cage", lambda n: False, lambda n: False)
    assert "mouseover/fishing_spot" in m and "mouseover/cage" in m


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok ", fn.__name__)
    print(f"{len(fns)} passed")


def test_methods_to_switch_drops_current_when_spot_lacks_it():
    none_missing = lambda m: []
    # picked lure before seeing a spot, then found net spots here and no lure spot saved
    assert F.methods_to_switch(25, list(F.METHOD_LEVELS), "lure", "net", set(), none_missing)[0] == "bait"


def test_spot_memory_remembers_and_finds_nearest(tmp_path, monkeypatch):
    from lumberjack.nav import spot_memory as M
    monkeypatch.setattr(M, "FILE", tmp_path / "fishing_spots.json")
    monkeypatch.setattr(M, "_cache", None)
    assert M.remember({"tile": [3100, 3250], "ops": ["Net", "Bait"]})
    assert not M.remember({"tile": [3101, 3251], "ops": ["Net", "Bait"]})      # the same spot, shuffled
    assert M.remember({"tile": [3110, 3270], "ops": ["Lure", "Bait"]})
    assert M.nearest("Net", (3100, 3245)) == ((3100, 3250), "remembered")
    assert M.nearest("Lure", (3112, 3260))[0] == (3110, 3270)
    assert M.nearest("Lure", (3112, 3268)) == (None, None)                    # standing there: it moved on
    M._cache = None                                                            # reloads from the file
    assert M.nearest("Net", (3100, 3245))[0] == (3100, 3250)
    assert M.nearest("Cage", (2830, 3420)) == ((2850, 3432), "★ Catherby fishing")   # built-in fallback
    assert M.nearest("Harpoon", (3200, 3200)) == (None, None)                  # nothing within 80


def test_harpoon_picks_the_right_spot_for_the_level():
    from lumberjack.skills.fishing import harpoon_spots
    sword = {"ops": ["Cage", "Harpoon"]}
    shark = {"ops": ["Net", "Harpoon"]}
    assert harpoon_spots([shark, sword], "harpoon", 60) == [sword]
    assert harpoon_spots([shark], "harpoon", 60) == []              # walk on to a cage/harpoon spot
    assert harpoon_spots([sword, shark], "harpoon", 80) == [shark]
    assert harpoon_spots([sword], "harpoon", 80) == [sword]
    assert harpoon_spots([shark, sword], "cage", 60) == [shark, sword]


def test_fish_cook_only_cooks_what_the_level_can(monkeypatch):
    from lumberjack.core import backpack, gamestate
    from lumberjack.skills.fish_cook import FishCooker
    inv = [{"id": 1, "key": k} for k in ("raw_lobster", "raw_tuna", "raw_swordfish", "raw_mystery")]
    monkeypatch.setattr(backpack, "slots", lambda: inv)
    monkeypatch.setattr(gamestate, "skill", lambda name: {"base": 40})
    f = FishCooker.__new__(FishCooker)
    f.log = __import__("logging").getLogger("t")
    assert f.cookable([0, 1, 2, 3]) == [0, 1, 3]          # swordfish needs 45; unknown fish is tried
    monkeypatch.setattr(backpack, "slots", lambda: None)
    assert f.cookable([0, 1, 2]) == [0, 1, 2]             # no game data: try them all


def test_a_moved_cage_spot_sends_us_up_the_beach_not_nowhere(tmp_path, monkeypatch):
    """Catherby: the Cage spot we stood at moved; the nearest remembered Cage tile was where we
    stood, so the bot never walked and gave up after a long search - four times in one run."""
    from lumberjack.nav import spot_memory as M
    monkeypatch.setattr(M, "FILE", tmp_path / "fishing_spots.json")
    monkeypatch.setattr(M, "_cache", None)
    for t in ([2837, 3431], [2845, 3429], [2855, 3423]):
        M.remember({"tile": t, "ops": ["Cage", "Harpoon"]})
    me = (2837, 3432)
    first = M.nearest("Cage", me)[0]
    assert first == (2845, 3429)
    second = M.nearest("Cage", me, skip=[first])[0]
    assert second == (2850, 3432)                                             # the ★ place, then on
    assert M.nearest("Cage", me, skip=[first, second])[0] == (2855, 3423)
