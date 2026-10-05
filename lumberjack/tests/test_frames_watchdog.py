"""The add-on frame source (binary protocol) and the panel's freeze watchdog."""
import socket
import threading
import types

import numpy as np
import pytest

from lumberjack.core import frames


def serve_frames(replies):
    """A fake add-on: answers each 'frame' request with the next reply (bytes)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        c, _ = srv.accept()
        f = c.makefile("rb")
        for r in replies:
            if f.readline().strip() != b"frame":
                return
            c.sendall(r)
        c.close()
    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_frame_from_addon(monkeypatch):
    w, h = 765, 503
    px = np.full((h, w), 0x112233, dtype="<u4")
    port = serve_frames([f"ok {w} {h}\n".encode() + px.tobytes(), b"err frame not ready\n"])
    monkeypatch.setattr(frames, "PORT", port)
    monkeypatch.setattr(frames, "_off_until", 0.0)
    frames._close()
    img = frames.grab()
    assert img.shape == (h, w, 3) and tuple(img[0, 0]) == (0x33, 0x22, 0x11)    # B, G, R
    assert frames.grab() is None                                               # an error: screen grab instead
    frames._close()


def test_old_addon_is_asked_again_later(monkeypatch):
    port = serve_frames([b"err unknown command\n"])
    monkeypatch.setattr(frames, "PORT", port)
    monkeypatch.setattr(frames, "_off_until", 0.0)
    frames._close()
    assert frames.grab() is None and frames._off_until > 0
    assert frames.grab() is None                                               # not even asked now


class Stop(Exception):
    pass


def test_watchdog_restarts_a_frozen_game(monkeypatch):
    from lumberjack.core import gamestate
    from lumberjack.web import server
    clock = {"t": 0.0, "sleeps": 0}

    def sleep(s):
        clock["t"] += s
        clock["sleeps"] += 1
        if clock["sleeps"] > 40:
            raise Stop

    monkeypatch.setattr(server.time, "sleep", sleep)
    monkeypatch.setattr(server.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(type(server.ctl), "running", property(lambda self: True))
    monkeypatch.setattr(server, "window", lambda: types.SimpleNamespace(minimized=lambda: False))
    loops = iter([1, 2, 3] + [3] * 100)                       # moves, then stops moving
    monkeypatch.setattr(gamestate.GameState, "_q", lambda self, cmd: {"loop": next(loops)})
    froze = []
    monkeypatch.setattr(server, "handle_freeze", lambda log: froze.append(clock["t"]))
    with pytest.raises(Stop):
        server.freeze_watchdog()
    assert froze and froze[0] >= 30 + server.FREEZE_S          # only after FREEZE_S without progress
