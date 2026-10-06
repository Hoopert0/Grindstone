"""Offline tests for the mining skill (no game, no input).

    python lumberjack/tests/test_mining.py        (or: python -m pytest lumberjack/tests)

lumberjack.core.input is replaced by a stub before anything imports it, so loading the
skill module can never reach the input agent.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_stub = types.ModuleType("lumberjack.core.input")
_stub.VK_LEFT, _stub.VK_RIGHT, _stub.VK_UP, _stub.VK_DOWN, _stub.VK_SHIFT = 0x25, 0x27, 0x26, 0x28, 0x10


class _NoInput:
    def __init__(self, *a, **k):
        raise RuntimeError("tests must never create AgentInput")


_stub.AgentInput = _NoInput
sys.modules.setdefault("lumberjack.core.input", _stub)
# another test file's stub may already be in place (pytest runs them in one process) - fine,
# as long as it isn't the real module, which always has a __file__
assert not hasattr(sys.modules["lumberjack.core.input"], "__file__"), "real input module was already imported"

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from lumberjack.core import regions as R  # noqa: E402
from lumberjack.skills import mining  # noqa: E402
from lumberjack.vision import rocks  # noqa: E402


def test_best_ore():
    assert mining.best_ore(1, mining.AUTO_ORES) == "copper"
    assert mining.best_ore(14, mining.AUTO_ORES) == "copper"
    assert mining.best_ore(15, mining.AUTO_ORES) == "iron"
    assert mining.best_ore(29, mining.AUTO_ORES) == "iron"
    assert mining.best_ore(30, mining.AUTO_ORES) == "coal"
    assert mining.best_ore(54, mining.AUTO_ORES) == "coal"
    assert mining.best_ore(55, mining.AUTO_ORES) == "mithril"
    assert mining.best_ore(70, mining.AUTO_ORES) == "adamant"
    assert mining.best_ore(99, mining.AUTO_ORES) == "rune"
    # only spots for copper/tin/iron saved -> stay with what we can reach
    assert mining.best_ore(60, mining.AUTO_ORES, have={"copper", "tin", "iron"}) == "iron"
    # no spot fits at all -> fall back to the level choice
    assert mining.best_ore(60, mining.AUTO_ORES, have={"rune"}) == "mithril"
    assert mining.best_ore(0) is None
    assert mining.best_ore(40, ["gold", "silver"]) == "gold"


def test_order_ores():
    assert mining.order_ores(["tin", "coal", "iron"]) == ["coal", "iron", "tin"]


def test_rock_spots():
    spots = {
        "lumb_swamp": {"x": 1, "y": 2, "kind": "rocks", "trees": ["copper", "tin"]},
        "varrock_se": {"x": 3, "y": 4, "kind": "rocks", "rocks": ["iron"]},
        "any_rocks": {"x": 5, "y": 6, "kind": "rocks", "trees": []},
        "willows": {"x": 7, "y": 8, "kind": "trees", "trees": ["willow"]},
        "bank": {"x": 9, "y": 9, "kind": "bank", "trees": []},
    }
    names = [n for n, _ in mining.rock_spots(spots, ["copper"])]
    assert names == ["lumb_swamp", "any_rocks"]
    names = [n for n, _ in mining.rock_spots(spots, ["iron"])]
    assert names == ["varrock_se", "any_rocks"]


def test_rank_candidates():
    C = rocks.RockCandidate
    cands = [C(0, 0, 1, (0, 0, 1, 1), 10.0, None), C(0, 0, 1, (0, 0, 1, 1), 20.0, "tin"),
             C(0, 0, 1, (0, 0, 1, 1), 30.0, "iron"), C(0, 0, 1, (0, 0, 1, 1), 40.0, "coal")]
    wanted = mining.order_ores(["copper", "tin", "iron"])   # iron, copper, tin
    ranked = mining.rank_candidates(cands, wanted)
    assert [c.ore for c in ranked] == ["iron", "tin", None]          # coal dropped
    assert [c.ore for c in mining.rank_candidates(cands, wanted, allow_unknown=False)] == ["iron", "tin"]


def _rock(img, center, rng, speck_bgr=None, n_specks=0):
    """Draw a lumpy grey-brown boulder, optionally with ore specks."""
    body = np.zeros(img.shape[:2], np.uint8)
    cv2.ellipse(body, center, (22, 17), 0, 0, 360, 255, -1)
    noise = rng.normal(0, 22, img.shape[:2])
    base = np.array([92, 104, 116], np.float64)        # BGR: S ~52, V ~116
    for k in range(3):
        ch = img[..., k].astype(np.float64)
        ch[body > 0] = np.clip(base[k] + noise[body > 0], 0, 255)
        img[..., k] = ch.astype(np.uint8)
    if speck_bgr is not None:
        ys, xs = np.where(body > 0)
        idx = rng.choice(len(xs), n_specks, replace=False)
        for i in idx:
            cv2.rectangle(img, (int(xs[i]) - 1, int(ys[i]) - 1), (int(xs[i]) + 1, int(ys[i]) + 1),
                          speck_bgr, -1)


def _scene():
    rng = np.random.default_rng(1)
    img = np.zeros((503, 765, 3), np.uint8)
    img[:] = (40, 120, 70)                              # flat grass-ish background
    _rock(img, (150, 120), rng, (30, 110, 200), 30)     # copper: orange-brown
    _rock(img, (380, 110), rng)                         # depleted: no specks
    _rock(img, (160, 280), rng, (200, 70, 60), 30)      # mithril: purple-blue (H ~118)
    _rock(img, (400, 270), rng, (60, 150, 40), 30)      # adamant: green
    cv2.rectangle(img, (40, 200), (60, 330), (110, 110, 110), -1)   # flat grey post: not a rock
    img[300:338, 300:400] = (111, 82, 65)               # water (H ~109): not mithril, not a rock
    return img


def test_find_rocks_synthetic():
    img = _scene()
    cands = rocks.find_rocks(img)
    got = sorted((c.x // 10, c.y // 10, c.ore) for c in cands)
    assert len(cands) == 4, got
    by_pos = {(round(c.x, -1), round(c.y, -1)): c.ore for c in cands}
    ore_at = lambda x, y: next(o for (cx, cy), o in by_pos.items() if abs(cx - x) < 15 and abs(cy - y) < 15)
    assert ore_at(150, 120) == "copper", got
    assert ore_at(380, 110) is None, got
    assert ore_at(160, 280) == "mithril", got
    assert ore_at(400, 270) == "adamant", got
    # nearest-first relative to the player
    d = [c.dist for c in cands]
    assert d == sorted(d)
    px, py = R.PLAYER.center
    assert all(abs(c.dist - np.hypot(c.x - px, c.y - py)) < 1e-6 for c in cands)
    # restricting classification
    only_copper = rocks.find_rocks(img, ores=["copper"])
    assert sum(c.ore == "copper" for c in only_copper) == 1
    assert all(c.ore in (None, "copper") for c in only_copper)
    assert rocks.draw(img, cands).shape == img.shape


def test_classify_thresholds():
    assert rocks.classify({"copper": 0.0, "tin": 0.0}) is None
    assert rocks.classify({"copper": 0.03, "tin": 0.05}) == "copper"   # tin under its 0.06 bar
    assert rocks.classify({"copper": 0.03, "coal": 0.5}) == "coal"     # 5x its bar beats 1.2x
    assert rocks.classify({"copper": 0.03, "coal": 0.5}, ores=["copper"]) == "copper"


def test_templates_constants():
    assert set(mining.ORE_TARGETS) >= set(mining.ORE_LEVELS)
    assert set(rocks.ORE_SPECKS) == set(mining.ORE_LEVELS)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL  {name}: {e}")
    sys.exit(1 if fails else 0)


def test_describe_view_empty():
    assert mining.describe_view(np.zeros((503, 765, 3), np.uint8)) == "No rocks found in view."


# ---- rocks from the game's scene data ---------------------------------------------------
def rock(id_, dist, ops=("Mine", "Prospect")):
    return {"id": id_, "name": "Rocks", "ops": list(ops), "tile": [3200 + dist, 3200], "dist": dist,
            "screen": [250 + 10 * dist, 180], "body": [250 + 10 * dist, 120], "size": [1, 1]}


def test_ore_of_item():
    assert mining.ore_of_item("copper_ore") == "copper" and mining.ore_of_item("clay") == "clay"
    assert mining.ore_of_item("runite_ore") == "rune" and mining.ore_of_item("adamantite_ore") == "adamant"
    assert mining.ore_of_item("logs") is None and mining.ore_of_item(None) is None


def test_rank_rocks_gs():
    locs = [rock(1, 1), rock(2, 2), rock(3, 3), rock(4, 4), rock(5, 1, ops=("Examine",))]
    mapping = {1: "empty", 2: "tin", 3: "copper"}
    # learned ids win; empty and non-minable are skipped; unknown (4) comes last
    got = mining.rank_rocks_gs(locs, mapping, ["copper", "tin"])
    assert [(o, k, l["id"]) for o, k, l in got] == [("copper", True, 3), ("tin", True, 2), ("copper", False, 4)]
    # the screen's guess places an unknown rock
    got = mining.rank_rocks_gs(locs, mapping, ["iron"], guess=lambda l: "iron" if l["id"] == 4 else None)
    assert [(o, k, l["id"]) for o, k, l in got] == [("iron", False, 4)]
    # ids that failed this run are skipped; unknowns can be disallowed
    assert mining.rank_rocks_gs(locs, mapping, ["copper"], allow_unknown=False, empty_ids={3}) == []


def test_learn_rock(tmp_path, monkeypatch):
    monkeypatch.setattr(mining, "ROCK_ORES", tmp_path / "rock_ores.json")
    m = mining.Miner.__new__(mining.Miner)
    m.log = __import__("logging").getLogger("t")
    m.rock_ores, m.rock_fails = {}, {}
    m.last_rock = rock(11, 1)

    class GS:   # after the mine, tile holds the empty rock (id 450)
        def locs(self, radius):
            return [dict(rock(450, 1), tile=[3201, 3200])]
    m.gs = GS()
    m._ore_counts = lambda: {"copper": 1}
    assert m.learn_rock({}, 1) == "copper"
    assert m.rock_ores == {11: "copper", 450: "empty"}
    loaded = mining.load_rock_ores()                     # saved, plus the server's built-in table
    assert loaded[11] == "copper" and loaded[450] == "empty" and loaded[2090] == "copper"
    m.last_rock = rock(12, 2)
    m.learn_rock({}, 0)
    m.learn_rock({}, 0)
    assert m.rock_fails == {12: 2}


def test_level_1_never_tries_rocks_above_its_level(monkeypatch, tmp_path):
    from lumberjack.skills import mining as M
    monkeypatch.setattr(M, "ROCK_ORES", tmp_path / "rock_ores.json")
    known = M.load_rock_ores()
    assert known[2090] == "copper" and known[2092] == "iron" and known[450] == "empty"
    locs = [{"id": 2092, "ops": ["Mine"], "dist": 1, "name": "Rocks"},       # iron: too high, skipped
            {"id": 450, "ops": ["Mine"], "dist": 1, "name": "Rocks"},        # depleted
            {"id": 2090, "ops": ["Mine"], "dist": 4, "name": "Rocks"}]       # copper
    ranked = M.rank_rocks_gs(locs, known, ["copper", "tin"], guess=lambda l: "copper")
    assert [r[2]["id"] for r in ranked] == [2090]


def _miner(monkeypatch, gs):
    m = mining.Miner.__new__(mining.Miner)
    m.log = __import__("logging").getLogger("t")
    m.rock_ores, m.rock_fails, m.fail_streak, m.mined, m.blocked = {}, {}, {}, {}, {}
    m.bad_spots, m.ores, m.taken, m.logs_cut, m.dry_since = [], ["copper", "tin"], 0, 0, None
    m.gs, m.state = gs, ""
    clock = {"t": 0.0}
    monkeypatch.setattr(mining.time, "monotonic", lambda: clock["t"])
    m.sleep = lambda s: clock.__setitem__("t", clock["t"] + s)
    m.grab = lambda: None
    m.activity = types.SimpleNamespace(reset=lambda: None, update=lambda f: None, active=True, filled=False)
    monkeypatch.setattr(mining.inventory, "count", lambda frame: 5)
    m._ore_counts = lambda: {}
    m.active_ores = lambda: ["copper", "tin"]
    return m, clock


def test_a_rock_someone_else_empties_is_left_and_not_blamed(monkeypatch):
    """Other players mine the same rocks: one emptied while we walk over isn't a failed rock
    (that blacklisted the whole copper id after two) - we move on at once."""
    looks = {"n": 0}

    class GS:
        def locs(self, radius=15, name=None):
            looks["n"] += 1
            return [dict(rock(2090 if looks["n"] < 3 else 450, 1))]       # emptied on the 3rd look

        def player(self):
            return {"anim": -1, "moving": True}
    m, clock = _miner(monkeypatch, GS())
    m.last_rock = rock(2090, 1)
    assert m.mine("copper", types.SimpleNamespace(x=1, y=1, dist=2)) is False
    assert m.taken == 1 and m.rock_fails == {} and m.fail_streak == {}
    assert clock["t"] < 5                                  # not the 30 s timeout


def test_mined_out_waits_for_respawn_then_stops(monkeypatch):
    m, clock = _miner(monkeypatch, object())
    m.wait_for_respawn()
    assert m.state == "waiting for respawn" and m.dry_since == 0.0
    clock["t"] = mining.RESPAWN_PATIENCE_S + 1
    import pytest
    with pytest.raises(mining.StopBot, match="free here"):
        m.wait_for_respawn()


def test_rocks_that_cant_be_clicked_are_not_mined_out(monkeypatch):
    """v109 treated "clicks didn't take" like "everything is mined" and waited for respawns that
    never came. Only rocks known to be empty mean that."""
    from lumberjack.core import interact

    class GS:
        def locs(self, radius=15, name=None):
            return [rock(2090, 2), rock(2091, 3)]
    m, clock = _miner(monkeypatch, GS())
    m.allow_unknown, m.ctx = True, None
    m._guess_ore = lambda frame: (lambda loc: None)
    monkeypatch.setattr(interact, "on_screen", lambda x, y, margin=4: True)
    monkeypatch.setattr(interact, "use_option", lambda *a, **k: None)          # hover never takes
    assert m.find_and_click_rock_gs(walked=True) is None
    assert m.seen["ranked"] == 2 and not m.mined_out()
    m.rock_ores = {2090: "empty", 2091: "empty"}                                 # now they're all mined
    assert m.find_and_click_rock_gs(walked=True) is None
    assert m.mined_out()
