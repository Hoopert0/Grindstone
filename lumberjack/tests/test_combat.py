"""Offline tests for the combat skill (no game, no input).

    python lumberjack/tests/test_combat.py        (or: python -m pytest lumberjack/tests)

lumberjack.core.input is replaced by a stub before anything imports it, so loading the
skill module can never reach the input agent.
"""
import sys
import tempfile
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

from lumberjack.skills import combat  # noqa: E402
from lumberjack.vision import health, npcs  # noqa: E402
from lumberjack.vision import minimap as MM  # noqa: E402


def blank():
    f = np.zeros((503, 765, 3), np.uint8)
    f[...] = (40, 110, 100)          # olive-ish grass, not a pure colour
    return f


# ---- pure logic -------------------------------------------------------------------------
def test_choose_stat_lowest_first():
    lv = {"attack": 10, "strength": 8, "defence": 9}
    assert combat.choose_stat(lv) == "strength"
    # ties go by order: attack before strength before defence
    assert combat.choose_stat({"attack": 5, "strength": 5, "defence": 5}) == "attack"


def test_choose_stat_hysteresis():
    lv = {"attack": 11, "strength": 10, "defence": 10}
    # keep attack while it's < lowest other + spread (10 + 2)
    assert combat.choose_stat(lv, current="attack", spread=2) == "attack"
    lv["attack"] = 12
    assert combat.choose_stat(lv, current="attack", spread=2) == "strength"
    assert combat.choose_stat(lv, current="attack", spread=0) == "strength"


def test_choose_stat_caps_and_empty():
    lv = {"attack": 40, "strength": 20, "defence": 30}
    assert combat.choose_stat(lv, caps={"strength": 20}) == "defence"
    assert combat.choose_stat(lv, caps={"attack": 40, "strength": 20, "defence": 30}) is None
    assert combat.choose_stat({}) is None


def test_should_eat_and_fight_result():
    assert combat.should_eat(0.3, 0.5)
    assert not combat.should_eat(0.6, 0.5)
    assert not combat.should_eat(None, 0.5)
    assert combat.fight_result(False, None) == "no_engage"
    assert combat.fight_result(True, 0.0) == "kill"
    assert combat.fight_result(True, 0.6) == "ended"
    assert combat.fight_result(True, None) == "ended"


def test_style_slots_defaults():
    assert set(combat.DEFAULT_STYLE_SLOTS) == set(combat.MELEE)
    assert all(0 <= v < len(combat.STYLE_BUTTONS) for v in combat.DEFAULT_STYLE_SLOTS.values())


# ---- health bars / splats -------------------------------------------------------------
def draw_bar(f, x, y, frac, w=30, h=5):
    g = int(round(w * frac))
    f[y:y + h, x:x + g] = (0, 255, 0)
    f[y:y + h, x + g:x + w] = (0, 0, 255)


def test_find_bars_and_player_bar():
    f = blank()
    px, py = health.PLAYER_BAR_POS
    draw_bar(f, px - 15, py - 2, 2 / 3)
    draw_bar(f, 400, 250, 0.2)
    bars = health.find_bars(f)
    assert len(bars) == 2, bars
    mine = health.player_bar(f, bars)
    assert mine is not None and abs(mine.frac - 2 / 3) < 0.05, mine
    tgt = health.target_bar(f, bars)
    assert tgt is not None and tgt.x == 400 and abs(tgt.frac - 0.2) < 0.05, tgt
    assert health.in_combat(f, bars)


def test_no_bars_on_grass_and_far_bar_not_combat():
    f = blank()
    assert health.find_bars(f) == []
    assert not health.in_combat(f)
    draw_bar(f, 60, 290, 1.0)               # a bar far away from us
    assert len(health.find_bars(f)) == 1
    assert not health.in_combat(f)


def test_bar_in_hover_strip_ignored():
    f = blank()
    draw_bar(f, 100, 8, 1.0)                # "(level-3)"-ish green text area
    assert health.find_bars(f) == []


def test_hitsplat():
    f = blank()
    cv2.circle(f, (256, 175), 10, (0, 0, 230), -1)
    f[171:179, 254:258] = (255, 255, 255)   # the damage digit
    sp = health.hitsplats(f)
    assert len(sp) == 1 and sp[0][2] == "red", sp
    assert health.in_combat(f)
    g = blank()
    draw_bar(g, 241, 175, 0.0)              # flat red bar - not a splat
    assert health.hitsplats(g) == []


# ---- HP orb -----------------------------------------------------------------------------
def draw_orb(f, level):
    cx, cy = health.ORB_CENTER
    r = health.ORB_R
    cv2.circle(f, (cx, cy), r, (20, 20, 200), -1)
    top = int(round(cy + r + 1 - level * (2 * r + 1)))
    f[cy - r:top, cx - r:cx + r + 1] = (10, 10, 30)     # drained part: dark


def test_orb_fill():
    for level in (1.0, 0.5, 0.25):
        f = blank()
        draw_orb(f, level)
        got = health.orb_fill(f)
        assert got is not None and abs(got - level) <= 0.1, (level, got)


DIGIT_BITMAPS = {   # crude 5x8 digits, distinct enough for IoU
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "0": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "...#.", "..#..", "..#..", ".#...", ".#..."],
}


def draw_number(f, text, color=(0, 255, 0)):
    x0, y0 = health.ORB_TEXT.x + 5, health.ORB_TEXT.y + 4
    for ch in text:
        for dy, row in enumerate(DIGIT_BITMAPS[ch]):
            for dx, c in enumerate(row):
                if c == "#":
                    f[y0 + dy, x0 + dx] = color
        x0 += 7


def test_learn_and_read_hp():
    old_dir, old_cache = health.DIGITS, health._digits
    with tempfile.TemporaryDirectory() as tmp:
        health.DIGITS, health._digits = Path(tmp), None
        try:
            f = blank()
            f[health.ORB_TEXT.y:health.ORB_TEXT.y + health.ORB_TEXT.h,
              health.ORB_TEXT.x:health.ORB_TEXT.x + health.ORB_TEXT.w] = (115, 108, 90)   # grey box
            draw_number(f, "10")
            assert health.read_hp(f) is None              # nothing learned yet
            assert health.learn_digits(f, 10) == 2
            assert health.read_hp(f) == 10
            assert health.learn_digits(f, 10) == 0        # already known
            health._digits = None                         # reload from disk
            assert health.read_hp(f) == 10
            assert health.hp_fraction(f, max_hp=20) == 0.5
            g = f.copy()
            g[health.ORB_TEXT.y:health.ORB_TEXT.y + health.ORB_TEXT.h,
              health.ORB_TEXT.x:health.ORB_TEXT.x + health.ORB_TEXT.w] = (115, 108, 90)
            draw_number(g, "7")
            assert health.read_hp(g) is None              # unknown digit -> no guess
            assert health.learn_digits(f, 123) == 0       # glyph count mismatch -> no save
        finally:
            health.DIGITS, health._digits = old_dir, old_cache


def test_text_hue_fraction():
    f = blank()
    draw_number(f, "10", (0, 255, 0))
    assert health.text_hue_fraction(f) > 0.9
    g = blank()
    draw_number(g, "10", (0, 0, 255))
    assert health.text_hue_fraction(g) < 0.1


# ---- NPC finder -------------------------------------------------------------------------
def put_dot(f, dx, dy):
    x, y = MM.CENTER[0] + dx, MM.CENTER[1] + dy
    f[y - 2:y + 2, x - 2:x + 2] = (0, 255, 255)


def test_minimap_dots_sorted_and_projected():
    f = blank()
    put_dot(f, 10, -5)
    put_dot(f, -40, 30)
    put_dot(f, 0, 0)                     # would be our own marker area -> ignored
    dots = npcs.minimap_dots(f)
    assert len(dots) == 2, dots
    assert abs(dots[0][0] - 10) <= 1 and abs(dots[0][1] + 5) <= 1, dots
    x, y = npcs.project(*dots[0])
    assert npcs.in_view(x, y)
    assert x > npcs.PLAYER_TILE[0] and y < npcs.PLAYER_TILE[1]
    cands = npcs.find_npcs(f)
    assert len(cands) == 1 and cands[0].source == "minimap"
    far = npcs.far_dots(f)
    assert len(far) == 1 and far[0][0] < -30
    mx, my = npcs.minimap_click_point(*far[0])
    assert np.hypot(mx - MM.CENTER[0], my - MM.CENTER[1]) <= npcs.MAX_WALK_MM + 1


def test_motion_blobs():
    a = blank()
    b = a.copy()
    b[95:115, 95:110] = (200, 200, 200)      # something walked in
    pts = npcs.motion_blobs(a, b)
    assert len(pts) == 1 and abs(pts[0][0] - 102) <= 3, pts
    assert npcs.motion_blobs(None, b) == []


def test_probe_points_near_candidate():
    c = npcs.Candidate(200, 150, 10.0)
    pts = c.probe_points()
    assert len(pts) >= 4
    assert all(abs(x - 200) <= 14 and abs(y - 150) <= 28 for x, y in pts)


# ---- NPC name matching ------------------------------------------------------------------
def word(rng, w=18, h=9):
    """A sparse glyph-like random word (dense noise would match anything at 1px tolerance)."""
    t = (rng.random((h, w)) > 0.8).astype(np.uint8) * 255
    t[h // 2, 0] = 255           # pin the width / height
    t[h // 2, -1] = 255
    t[0, w // 2] = 255
    t[-1, w // 2] = 255
    return t


def test_name_match_with_level_suffix():
    rng = np.random.default_rng(1)
    name = word(rng)
    suffix = word(rng, 30, 11)
    m = np.zeros((18, 200), np.uint8)
    m[3:12, 40:58] = name
    m[2:13, 63:93] = suffix                 # "(level-1)" after a 4px space
    assert npcs.name_match(m, name)
    # name glued to more letters (no space) -> a different, longer name
    g = m.copy()
    g[3:12, 58:62] = 255
    assert not npcs.name_match(g, name)
    # name not at the start (another word before it) -> no match
    h = np.zeros((18, 200), np.uint8)
    h[3:12, 10:30] = word(rng, 20)
    h[3:12, 40:58] = name
    assert not npcs.name_match(h, name)
    # a different word -> no match
    assert not npcs.name_match(m, word(rng))


def test_name_columns_drops_paren_suffix():
    m = np.zeros((16, 120), np.uint8)
    m[3:11, 5:30] = 255                   # "Chicken" (rows 3..10)
    m[1:14, 35:37] = 255                  # "(" taller and lower than the name
    m[3:11, 37:60] = 255                  # "level-1)"
    assert npcs.name_columns(m) == (5, 29)
    two = np.zeros((16, 120), np.uint8)
    two[3:11, 5:25] = 255                 # "Giant"
    two[3:11, 30:45] = 255                # "rat" - same height: kept
    two[1:14, 50:52] = 255
    two[3:11, 52:70] = 255
    assert npcs.name_columns(two) == (5, 44)
    assert npcs.name_columns(m, words=1) == (5, 29)


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


def test_loot_points():
    pts = combat.loot_points()
    assert len(pts) == 9                          # our tile + the 8 around it
    px, py = npcs.PLAYER_TILE
    assert pts[0] == (px, py)                     # our own tile first, on the ground
    assert len(set(pts)) == 9
    kp = combat.loot_points(kill_point=(300, 200))
    assert kp[0] == (300, 200) and len(kp) == 10
    # a kill point on top of our own tile isn't hovered twice
    assert len(combat.loot_points(kill_point=(px + 3, py - 2))) == 9


# ---- targets and fights from the game's own data (a fake GameState) ----------------------
class FakeGS:
    def __init__(self, npcs, me):
        self._npcs, self.me, self.calls = npcs, me, 0

    def npcs(self, name=None):
        self.calls += 1
        return [dict(n) for n in self._npcs]

    def player(self):
        return dict(self.me)


def npc(index, name="Chicken", dist=2, interacting=-1, in_combat=False, hp=255):
    return {"index": index, "name": name, "ops": ["Attack", "Examine"], "tile": [3200 + dist, 3200], "dist": dist,
            "screen": [250, 180], "body": [250, 150], "anim": -1, "hp_bar": hp, "in_combat": in_combat,
            "interacting": interacting}


def fighter(gs, targets=("chicken",)):
    f = combat.Fighter.__new__(combat.Fighter)
    f.gs, f.targets, f.target_index = gs, list(targets), None
    f.kill_point = None
    f.sleep = lambda s: None
    f.grab = lambda: None
    f.ensure_hp = lambda frame: None
    f.ctx = None
    return f


def test_targets_skip_other_players_fights_and_prefer_ours():
    me = {"index": 7, "tile": [3200, 3200], "in_combat": False, "interacting": -1}
    gs = FakeGS([npc(1, dist=1, interacting=32768 + 3),          # fighting another player
                 npc(2, dist=5), npc(3, name="Cow", dist=1),     # not a target
                 npc(4, dist=8, interacting=32768 + 7)], me)     # already on us
    f = fighter(gs)
    targets, _ = f._targets_gs()
    assert [n["index"] for n in targets] == [4, 2]


def test_attacker_found_when_in_combat():
    me = {"index": 7, "tile": [3200, 3200], "in_combat": True, "interacting": -1}
    f = fighter(FakeGS([npc(2), npc(9, interacting=32768 + 7)], me))
    assert f.attacker_gs() == 9
    f.gs.me["in_combat"] = False
    assert f.attacker_gs() is None


def test_fight_outcomes(monkeypatch):
    monkeypatch.setattr(combat.actions, "dismiss_dialog", lambda ctx: False)
    me = {"index": 7, "tile": [3200, 3200], "in_combat": True, "interacting": 2, "moving": False}
    # fought, then it vanished from the NPC list -> kill
    gs = FakeGS([npc(2, interacting=32768 + 7, in_combat=True, hp=100)], me)
    f = fighter(gs)
    f.target_index = 2
    orig = gs.npcs

    def dying(name=None):
        out = orig(name)
        return out if gs.calls < 3 else []
    gs.npcs = dying
    assert f.fight_gs() == "kill"
    # target gone before we ever fought it -> no_engage
    f = fighter(FakeGS([], dict(me, in_combat=False, interacting=-1)))
    f.target_index = 2
    assert f.fight_gs() == "no_engage"


def test_loot_from_ground_list(monkeypatch):
    """Takes only the wanted items on the drop pile - via the menu row when another item is on
    top - and leaves unwanted items and other piles alone."""
    from lumberjack.core import gamestate as G
    pile = [3205, 3200]
    ground = [{"id": 526, "count": 1, "name": "Bones", "tile": pile, "dist": 1, "screen": [270, 190]},
              {"id": 314, "count": 5, "name": "Feather", "tile": pile, "dist": 1, "screen": [270, 190]},
              {"id": 1739, "count": 1, "name": "Cowhide", "tile": pile, "dist": 1, "screen": [270, 190]},
              {"id": 526, "count": 1, "name": "Bones", "tile": [3210, 3204], "dist": 6, "screen": [380, 120]}]
    taken, state = [], {"open": False}
    MENU = {"x": 200, "y": 100, "w": 120}

    def on_pile():
        return [g for g in ground if g["tile"] == pile]

    def entries():                       # the top item (last dropped) is the left-click option
        names = [g["name"] for g in reversed(on_pile())] + ["Walk here"]
        return [{"verb": "Take" if n != "Walk here" else n, "subject": "" if n == "Walk here" else n,
                 "kind": "item", "row": r} for r, n in enumerate(names)]

    class GS:
        def ground(self, radius):
            return [dict(g) for g in ground]

        def menu(self):
            return {**MENU, "h": 100, "open": state["open"], "entries": entries()}

    class Inp:
        pos = (0, 0)

        def move(self, x, y, steps=None):
            self.pos = (x, y)
            state["open"] = False

        def right_click(self, x, y):
            state["open"] = True

        def click(self, x=None, y=None):
            if state["open"]:            # a menu row: work out which from y
                row = round((y - MENU["y"] - G.MENU_FIRST_BASELINE + 4) / G.MENU_ROW_H)
                e = entries()[row]
                state["open"] = False
            else:
                e = entries()[0]
            if e["verb"] == "Take":
                g = next(g for g in reversed(on_pile()) if g["name"] == e["subject"])
                taken.append(g["name"])
                ground.remove(g)

    f = fighter(GS(), targets=("cow",))
    f.inp, f.loot_items, f.kill_tile, f.looted, f.bury_bones = Inp(), ["bones", "feather"], tuple(pile), 0, False
    f.state = ""
    monkeypatch.setattr(combat.inventory, "count", lambda frame: 3)
    f.loot_gs()
    assert sorted(taken) == ["Bones", "Feather"]          # not the Cowhide, not the other pile
    assert [g["name"] for g in ground] == ["Cowhide", "Bones"]
    assert f.looted == 2


def test_fresh_account_food_top_up_makes_room(monkeypatch):
    """A new account carries 2 shrimps + bread and a full starter kit: drop the kit's junk,
    then spawn lobsters up to FOOD_SPAWN."""
    from lumberjack import actions, items
    from lumberjack.core import backpack
    names = ["bronze_axe", "tinderbox", "small_fishing_net", "shrimps", "bucket", "pot", "bread",
             "bronze_pickaxe", "bronze_dagger", "bronze_sword", "wooden_shield", "shortbow",
             "bronze_arrow", "air_rune", "mind_rune", "coins", "mystery_item"] + ["x"] * 9 + [None] * 2
    monkeypatch.setattr(backpack, "slots", lambda: [{"id": 1 if n else -1, "key": n} for n in names])
    dropped, filled = [], []
    monkeypatch.setattr(actions, "drop_known", lambda ctx, gs, slots: dropped.extend(slots))
    monkeypatch.setattr(items, "fill", lambda ctx, key, n: filled.append((key, n)) or n)
    f = combat.Fighter.__new__(combat.Fighter)
    f.foods, f.ctx, f.gs = ["lobster", "shrimps", "bread"], None, object()

    class Inp:
        def move(self, *a):
            pass
    f.inp = Inp()
    assert f.top_up_food() == combat.FOOD_SPAWN - 2
    assert filled == [("lobster", combat.FOOD_SPAWN - 2)]
    kept = {names[i] for i in range(len(names)) if i not in dropped}
    assert {"bronze_axe", "tinderbox", "coins", "mystery_item", "shrimps", "bread"} <= kept
    assert len(dropped) == combat.FOOD_SPAWN - 2 - 2          # just enough room (2 slots were free)
    f.foods = ["lobster"]
    monkeypatch.setattr(backpack, "slots", lambda: [{"id": 1, "key": "lobster"}] * 6 + [{"id": -1, "key": None}] * 22)
    assert f.top_up_food() == 0                                # 6 carried: enough


def test_slayer_sets_the_task_and_renews_it(monkeypatch):
    from lumberjack.skills import slayer_task as S
    typed = []
    f = S.SlayerFighter.__new__(S.SlayerFighter)
    f.targets, f.kills, f.assigned_at, f.gs, f.state = ["hill_giant"], 0, None, object(), ""
    f.log = __import__("logging").getLogger("t")
    f.inp = types.SimpleNamespace(move=lambda *a: None, type_text=lambda t, enter=False: typed.append(t))
    f.sleep = lambda s: None
    monkeypatch.setattr(combat.Fighter, "check_levels", lambda self, force_style=False: None)
    f.check_levels(force_style=True)
    assert typed == ["::setslayertask 117 255"]
    f.kills = 150
    f.check_levels()
    assert len(typed) == 1                       # still plenty left
    f.kills = 200
    f.check_levels()
    assert len(typed) == 2
    assert S.task_npc(["al-kharid_warrior", "goblin"]) == 100 and S.task_npc(["man"]) is None
    f.targets = ["man"]
    f.assigned_at = None
    with __import__("pytest").raises(Exception, match="Slayer task"):
        f.check_levels()
