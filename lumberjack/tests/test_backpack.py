"""core.backpack: item kinds by name, and reading the backpack through a fake add-on."""
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lumberjack.core import backpack as B  # noqa: E402
from lumberjack.core import gamestate as G  # noqa: E402

INV = ([{"id": 590, "count": 1, "name": "Tinderbox"}, {"id": 1351, "count": 1, "name": "Bronze axe"},
        {"id": 303, "count": 1, "name": "Small fishing net"}, {"id": 317, "count": 1, "name": "Raw shrimps"},
        {"id": 315, "count": 1, "name": "Shrimps"}, {"id": 7954, "count": 1, "name": "Burnt shrimp"},
        {"id": 1511, "count": 1, "name": "Logs"}, {"id": 841, "count": 1, "name": "Shortbow (u)"},
        {"id": 436, "count": 1, "name": "Copper ore"}, {"id": 1323, "count": 1, "name": "Iron scimitar"}]
       + [{"id": -1, "count": 0, "name": None}] * 18)


def test_keys_and_kinds():
    assert B.key("Raw shrimps") == "raw_shrimps" and B.key("Oak shortbow (u)") == "oak_shortbow_(u)"
    kinds = {s["name"]: B.kind(B.key(s["name"])) for s in INV if s["name"]}
    assert kinds == {"Tinderbox": "tool", "Bronze axe": "tool", "Small fishing net": "tool", "Raw shrimps": "raw",
                     "Shrimps": "cooked", "Burnt shrimp": "burnt", "Logs": "log", "Shortbow (u)": "product",
                     "Copper ore": "product", "Iron scimitar": None}
    drop = [s["name"] for s in INV if s["name"] and B.is_product(B.key(s["name"]))]
    assert drop == ["Raw shrimps", "Burnt shrimp", "Logs", "Shortbow (u)", "Copper ore"]   # tools, food, weapon kept
    assert B.is_product("shrimps", food=True) and not B.is_product("shrimps")
    assert not B.is_product("rune_pickaxe") and not B.is_product("coins")


def _fake_agent():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def serve():
        while True:
            conn, _ = srv.accept()
            for line in conn.makefile("r"):
                cmd = line.strip()[len("state "):]
                reply = {"probe": {"missing": []}, "inv": INV}.get(cmd)
                conn.sendall((("ok " + json.dumps(reply)) if reply is not None else "err ?").encode() + b"\n")

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def test_slots_find_occupied_with_game_data():
    G._shared = G.GameState(port=_fake_agent())
    try:
        inv = B.slots()
        assert inv[3]["key"] == "raw_shrimps" and inv[20]["key"] is None
        assert B.find("tinderbox") == [0] and B.find("raw_shrimps") == [3] and B.find("knife") == []
        occ = B.occupied()
        assert sum(occ) == 10 and occ[9] and not occ[10]
        from lumberjack.ui import inventory
        assert inventory.occupied(None) == occ          # the pixel path isn't even needed
    finally:
        G._shared = None
        G._shared_retry_at = 0.0


def test_use_option_left_click_or_menu_row():
    from lumberjack.core import interact

    class Ctx:
        def __init__(self, entries):
            self.entries, self.clicks, self.opened = entries, [], False
            self.inp = self
            self.pos = (0, 0)

        def move(self, x, y, steps=None):
            self.pos = (x, y)
            self.opened = False

        def click(self, x=None, y=None):
            self.clicks.append(("menu" if self.opened else "left", y))

        def right_click(self, x, y):
            self.opened = True

        def sleep(self, s):
            pass

    class GS:
        def __init__(self, ctx):
            self.ctx = ctx

        def menu(self):
            return {"open": self.ctx.opened, "x": 100, "y": 50, "w": 100, "h": 80,
                    "entries": [dict(e, row=i) for i, e in enumerate(self.ctx.entries)]}

    tree = {"verb": "Chop down", "subject": "Oak", "kind": "object"}
    walk = {"verb": "Walk here", "subject": "", "kind": None}
    c = Ctx([tree, walk])
    assert interact.use_option(c, GS(c), [(250, 180)], "Chop down", "Oak")
    assert c.clicks == [("left", None)]
    c = Ctx([walk, {"verb": "Attack", "subject": "Cow  (level-2)", "kind": "npc"}])
    assert interact.use_option(c, GS(c), [(250, 180)], "Attack", "Cow")
    assert c.clicks[0][0] == "menu" and c.clicks[0][1] in range(50 + 31 + 15 - 6, 50 + 31 + 15)
    c = Ctx([walk])
    assert interact.use_option(c, GS(c), [(250, 180), (5000, 5000)], "Mine", "Rocks") is None and not c.clicks


class _World:
    """A fake player who walks a few tiles toward each minimap click, plus a bank booth."""

    def __init__(self, me, booth):
        import numpy as np
        self.me, self.booth, self.bank_open, self.frame = list(me), booth, False, np.zeros((503, 765, 3), np.uint8)
        self.inp = self
        self.clicks = []
        self.pos = (0, 0)

    # ctx
    def grab(self):
        return self.frame

    def sleep(self, s):
        pass

    # input: a minimap click walks up to 5 tiles that way (north-up camera: screen up = north)
    def click(self, x=None, y=None):
        from lumberjack.vision import minimap as MM
        if x is not None and abs(x - MM.CENTER[0]) < 70 and abs(y - MM.CENTER[1]) < 70:
            dx, dy = (x - MM.CENTER[0]) / 4, -(y - MM.CENTER[1]) / 4
            step = lambda d: max(-5, min(5, round(d)))
            self.me = [self.me[0] + step(dx), self.me[1] + step(dy)]
        else:
            self.clicks.append((x, y))

    def move(self, x, y, steps=None):
        self.pos = (x, y)

    def right_click(self, x, y):
        pass

    # game data
    def player(self):
        return {"tile": list(self.me), "moving": False}

    def locs(self, radius, name=None):
        d = max(abs(self.booth[0] - self.me[0]), abs(self.booth[1] - self.me[1]))
        return [{"id": 2213, "name": "Bank booth", "ops": ["Use", "Use-quickly"], "tile": list(self.booth),
                 "dist": d, "screen": [250, 160] if d <= 6 else [-1, -1], "body": [250, 120] if d <= 6 else [-1, -1]}]

    def npcs(self, name=None):
        return []

    def menu(self):
        return {"open": False, "x": 0, "y": 0, "w": 0, "h": 0,
                "entries": [{"verb": "Use-quickly", "subject": "Bank booth", "kind": "object", "row": 0}]}


def test_walk_to_tile_and_bank_trip(monkeypatch):
    from lumberjack import bank
    from lumberjack.core import interact
    w = _World(me=(3092, 3245), booth=(3092, 3230))          # 15 tiles south
    assert interact.walk_to_tile(w, w, (3092, 3232), arrive=2)
    assert interact.tiles_apart(w.me, (3092, 3232)) <= 2
    # the bank trip: booth, deposit (stubbed), and back to the exact start tile
    w = _World(me=(3092, 3245), booth=(3092, 3230))
    deposited = []
    monkeypatch.setattr(bank, "_gs", lambda: w)
    monkeypatch.setattr(bank, "_wait_open", lambda ctx, t: True)
    monkeypatch.setattr(bank, "deposit_all", lambda ctx, keep_slots=(): deposited.append(set(keep_slots)) or True)
    monkeypatch.setattr(bank, "close", lambda ctx: None)
    assert bank.gs_bank_trip(w, keep_slots={0, 1})
    assert deposited == [{0, 1}]
    assert w.me == [3092, 3245]                               # back where we started


def test_loot_and_task_needs():
    from lumberjack.core import backpack as B
    assert B.kind("bones") == "loot" and B.is_product("cowhide") and B.is_product("grimy_guam")
    assert not B.is_product("bronze_scimitar") and not B.is_product("lobster")
    assert B.needed_for("bronze_axe", "fishing") and B.needed_for("bronze_axe", "woodcutting")
    assert not B.needed_for("bronze_pickaxe", "woodcutting") and B.needed_for("rune_pickaxe", "mining")
    assert B.needed_for("small_fishing_net", "fishing") and not B.needed_for("small_fishing_net", "mining")
    assert B.needed_for("lobster", "combat") and not B.needed_for("lobster", "fishing")
    assert B.needed_for("coins", "mining") and not B.needed_for("bronze_scimitar", "mining")


def test_clear_materials_drops_products_keeps_tools(monkeypatch):
    import types
    from lumberjack import actions
    from lumberjack.core import backpack
    names = ["bronze_axe", "logs", "raw_shrimps", "shrimps", "bones", "tinderbox", "bronze_scimitar"] + [None] * 21
    inv = [{"id": 1 if n else -1, "key": n} for n in names]
    monkeypatch.setattr(backpack, "slots", lambda: inv)
    dropped = []
    monkeypatch.setattr(actions, "drop_all", lambda ctx, keep=(), food=False: dropped.extend(
        i for i, s in enumerate(inv) if s["id"] >= 0 and backpack.is_product(s["key"], food)))
    ctx = types.SimpleNamespace()
    assert actions.clear_materials(ctx, food=True) == 4 and dropped == [1, 2, 3, 4]
    dropped.clear()
    assert actions.clear_materials(ctx, food=False) == 3 and dropped == [1, 2, 4]   # combat keeps food
    monkeypatch.setattr(backpack, "slots", lambda: [{"id": 1, "key": "bronze_axe"}] + [{"id": -1, "key": None}] * 27)
    dropped.clear()
    assert actions.clear_materials(ctx) == 0 and dropped == []


def test_clear_materials_with_game_data_drops_by_name(monkeypatch):
    """With game data the slots are known by name: drop exactly those (the hover check said
    "Not dropping slot N - it isn't a product" for everything it couldn't read)."""
    import types
    from lumberjack import actions
    from lumberjack.core import backpack, gamestate
    names = ["bronze_axe", "logs", "raw_shrimps", "bones"] + [None] * 24
    monkeypatch.setattr(backpack, "slots", lambda: [{"id": 1 if n else -1, "key": n} for n in names])
    gs = object()
    monkeypatch.setattr(gamestate, "shared", lambda: gs)
    got = []
    monkeypatch.setattr(actions, "drop_known", lambda ctx, g, slots: got.append((g, list(slots))) or len(slots))
    monkeypatch.setattr(actions, "drop_all", lambda *a, **k: pytest.fail("generic drop with game data"))
    assert actions.clear_materials(types.SimpleNamespace(), food=True) == 3
    assert got == [(gs, [1, 2, 3])]
