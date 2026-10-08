"""core.gamestate against a fake input add-on (a local socket answering 'state ...')."""
import json
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lumberjack.core import gamestate as G  # noqa: E402

REPLIES = {
    "probe": {"missing": []},
    "menu": {"open": True, "targeting": False, "x": 100, "y": 50, "w": 120, "h": 64, "entries": [
        {"verb": "Net", "subject": "<col=ffff00>Fishing spot", "action": 9, "row": 0},
        {"verb": "Bait", "subject": "<col=ffff00>Fishing spot", "action": 10, "row": 1},
        {"verb": "Walk here", "subject": "", "action": 23, "row": 2}]},
    "npcs Fishing spot": [{"index": 5, "name": "Fishing spot", "id": 316, "ops": ["Net", None, "Bait", None, None],
                           "tile": [3086, 3227], "dist": 2, "screen": [280, 180], "body": [280, 120],
                           "anim": -1, "hp_bar": 0}],
    "skills": {"base": [1] * 25, "boosted": [1] * 25, "xp": list(range(25))},
}


def fake_agent():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        f = conn.makefile("r")
        for line in f:
            cmd = line.strip()[len("state "):]
            reply = REPLIES.get(cmd)
            conn.sendall((("ok " + json.dumps(reply)) if reply is not None else "err unknown").encode() + b"\n")
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def test_queries_against_fake_agent():
    gs = G.GameState(port=fake_agent())
    assert gs.available()
    m = gs.menu()
    top = G.top_entry(m)
    assert top["verb"] == "Net" and top["subject"] == "Fishing spot" and top["kind"] == "npc"
    e = G.find_entry(m, "bait", "fishing spot")
    assert e["row"] == 1
    assert G.menu_row_point(m, 1) == (160, 50 + 31 + 15 - 4)
    spots = gs.npcs("Fishing spot")
    assert spots[0]["ops"] == ["Net", "Bait"]
    sk = gs.skills()
    assert sk["fishing"]["xp"] == G.SKILLS.index("fishing") and sk["attack"]["level"] == 1
    try:
        gs.inv()
        assert False, "expected an error reply"
    except G.GameStateError:
        pass
    gs.close()


def test_unreachable_agent_is_unavailable():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                       # nothing listens there now
    assert not G.GameState(port=port).available()


def test_clean_tags():
    assert G.clean("<col=ff9040>Raw shrimps") == ("Raw shrimps", "item")
    assert G.clean("<col=ffff00>Chicken<col=00ff00>  (level-1)") == ("Chicken  (level-1)", "npc")
    assert G.clean("") == ("", None)


def test_xp_table_and_skill_from_game():
    assert G.xp_for_level(2) == 83 and G.xp_for_level(50) == 101333 and G.xp_for_level(99) == 13034431
    REPLIES["probe"] = {"missing": []}
    base = [1] * 25
    boosted = [1] * 25
    xp = [0] * 25
    fi = G.SKILLS.index("fishing")
    base[fi], boosted[fi], xp[fi] = 10, 12, 1200
    REPLIES["skills"] = {"base": base, "boosted": boosted, "xp": xp}
    G._shared = G.GameState(port=fake_agent())
    try:
        s = G.skill("fishing")
        assert s == {"level": 12, "base": 10, "xp": 1200, "next": G.xp_for_level(11), "remainder": G.xp_for_level(11) - 1200}
        from lumberjack.ui import stats
        assert stats.read_level(None, "fishing") == (12, 10)        # no tab, no hovering
        assert stats.read_skill(None, "fishing")["xp"] == 1200
    finally:
        G._shared = None
        G._shared_retry_at = 0.0


def test_reset_and_rotate_camera_through_game_data():
    from lumberjack import actions
    cam = {"yaw": 300, "pitch": 200, "yaw_target": 300.0, "pitch_target": 200.0, "zoom": 600}

    class GS:
        def camera(self, yaw=None, pitch=None, zoom=None):
            if yaw is not None:
                cam["yaw"] = cam["yaw_target"] = yaw          # (the real camera glides there)
            if pitch is not None:
                cam["pitch"] = cam["pitch_target"] = pitch
            return dict(cam)

    class Ctx:
        sleep = staticmethod(lambda s: None)

        class inp:
            keys = []

            @staticmethod
            def hold_key(vk, s):
                Ctx.inp.keys.append(vk)

            @staticmethod
            def move(x, y):
                pass

    G._shared = GS()
    try:
        actions.reset_camera(Ctx)
        assert cam["yaw"] == 0 and cam["pitch"] == actions.CAMERA_TOP_DOWN and not Ctx.inp.keys
        actions.rotate_camera(Ctx)
        actions.rotate_camera(Ctx, quarters=4)
        assert cam["yaw"] == 512 and not Ctx.inp.keys        # exact quarter turns, no arrow keys
    finally:
        G._shared = None
        G._shared_retry_at = 0.0
    G._shared_retry_at = 1e18                                 # no game data: arrow keys instead
    try:
        actions.rotate_camera(Ctx)
        assert Ctx.inp.keys == [actions.VK_LEFT]
    finally:
        G._shared_retry_at = 0.0


def test_player_waits_out_loading_then_says_logged_out(monkeypatch):
    from lumberjack.core import gamestate as G
    answers = {"player": [{"logged_in": False}, {"logged_in": False}, {"logged_in": True, "tile": [1, 2]}],
               "tick": [{"state": 25}, {"state": 25}]}
    gs = G.GameState.__new__(G.GameState)
    gs._q = lambda cmd: answers[cmd].pop(0)
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    assert gs.player()["tile"] == [1, 2]                       # loading a new area: waited
    answers.update(player=[{"logged_in": False}], tick=[{"state": 10}])
    import pytest
    with pytest.raises(G.GameStateError, match="not in the game"):
        gs.player()                                             # the login screen: a clear error
    answers.update(player=[{"logged_in": False}])
    assert gs.player(raw=True) == {"logged_in": False}


def test_a_game_that_isnt_drawing_pauses_frame_queries_but_keeps_game_data(monkeypatch):
    """23:25 "Game data query 'widgets click here to continue' took 3.0 s": minimized, the game
    drew no frames and each interface/backpack read waited out the add-on's timeout - and a
    backpack read that failed that way switched game data off for 20 s."""
    from lumberjack.core import backpack
    asked = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        for line in conn.makefile("r"):
            asked.append(line.strip())
            conn.sendall(b"err java.lang.IllegalStateException: game busy (no frame drawn in 1000 ms)\n")
    threading.Thread(target=serve, daemon=True).start()
    gs = G.GameState(port=srv.getsockname()[1])
    try:
        gs._q("widgets click here to continue")
        assert False, "expected GameBusy"
    except G.GameBusy:
        pass
    try:
        gs.inv()                                # within the pause: not even asked
        assert False, "expected GameBusy"
    except G.GameBusy:
        pass
    assert asked == ["state widgets click here to continue"]
    monkeypatch.setattr(G, "_shared", gs)
    dropped = []
    monkeypatch.setattr(G, "drop_shared", lambda: dropped.append(1))
    monkeypatch.setattr(backpack, "source", lambda: gs)
    assert backpack.slots() is None and not dropped          # the link stays up
    bot = type("B", (), {})()
    assert G.tolerant_call(bot, gs.inv) is None and getattr(bot, "_gs_failures", 0) == 0
    gs.close()
