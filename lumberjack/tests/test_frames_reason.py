"""frames.grab() says why there's no usable frame (shown when the window is minimized)."""
import io

from lumberjack.core import frames


class FakeSock:
    def __init__(self, reply):
        self.reply = reply

    def sendall(self, data):
        pass

    def makefile(self, mode):
        return io.BytesIO(self.reply)

    def close(self):
        pass


def grab_with(monkeypatch, reply):
    monkeypatch.setattr(frames, "_sock", None)
    monkeypatch.setattr(frames, "_off_until", 0.0)
    monkeypatch.setattr(frames.socket, "create_connection", lambda *a, **k: FakeSock(reply))
    return frames.grab()


def test_no_picture_is_named(monkeypatch):
    assert grab_with(monkeypatch, b"err java.lang.IllegalStateException: no frame buffer (HD mode?)\n") is None
    assert "no picture" in frames.last_error and "restart the game" in frames.last_error


def test_wrong_size_is_named_and_a_good_frame_clears_it(monkeypatch):
    img = grab_with(monkeypatch, b"ok 4 2\n" + bytes(4 * 2 * 4))
    assert img.shape == (2, 4, 3) and "Fixed" in frames.last_error
    img = grab_with(monkeypatch, b"ok 765 503\n" + bytes(765 * 503 * 4))
    assert img.shape == (503, 765, 3) and frames.last_error is None


def test_old_addon(monkeypatch):
    assert grab_with(monkeypatch, b"err unknown command 'frame'\n") is None
    assert frames.grab() is None and "too old" in frames.last_error
