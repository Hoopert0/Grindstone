"""core/window.py with fake Windows APIs: a stale canvas handle is re-found, not fatal."""
import ctypes
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np


class WinErr(Exception):
    pass


def load_window(monkeypatch, state):
    g = types.ModuleType("win32gui")
    g.error = WinErr
    g.IsWindowVisible = lambda h: True
    g.GetWindowText = lambda h: "2009Scape [Local]"
    g.EnumWindows = lambda cb, _: cb(100, None)
    g.EnumChildWindows = lambda h, cb, _: cb(state["canvas"], None)
    g.GetClassName = lambda h: "SunAwtCanvas"
    g.IsIconic = lambda h: False
    g.IsWindow = lambda h: h == 100

    def rect(h):
        if h not in (100, state["canvas"]):
            raise WinErr(1400, "GetClientRect", "Invalid window handle.")
        return (0, 0, 765, 503)
    g.GetClientRect = rect
    g.GetWindowRect = lambda h: (0, 0, 765, 503)
    g.ScreenToClient = lambda h, p: (0, 0)
    g.GetDC = lambda h: 1
    g.ReleaseDC = lambda h, dc: None
    g.DeleteObject = lambda h: None
    u = types.ModuleType("win32ui")

    class DC:
        def CreateCompatibleDC(self):
            return self

        def SelectObject(self, b):
            pass

        def BitBlt(self, *a):
            pass

        def DeleteDC(self):
            pass

        def GetSafeHdc(self):
            return 1

    class Bmp:
        def CreateCompatibleBitmap(self, dc, w, h):
            self.w, self.h = w, h

        def GetBitmapBits(self, _):
            return (np.full((self.h, self.w, 4), 50, np.uint8)).tobytes()

        def GetHandle(self):
            return 1
    u.CreateDCFromHandle = lambda h: DC()
    u.CreateBitmap = Bmp
    c = types.ModuleType("win32con")
    c.SRCCOPY = 0
    monkeypatch.setitem(sys.modules, "win32gui", g)
    monkeypatch.setitem(sys.modules, "win32ui", u)
    monkeypatch.setitem(sys.modules, "win32con", c)
    fake_windll = types.SimpleNamespace(shcore=types.SimpleNamespace(SetProcessDpiAwareness=lambda v: 0),
                                        user32=types.SimpleNamespace(PrintWindow=lambda *a: 1))
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)
    path = Path(__file__).resolve().parents[1] / "core" / "window.py"
    spec = importlib.util.spec_from_file_location("window_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.time.sleep = lambda s: None
    return mod


def test_stale_canvas_is_refound(monkeypatch):
    state = {"canvas": 200}
    W = load_window(monkeypatch, state)
    win = W.GameWindow()
    assert win.canvas_hwnd == 200 and win.grab().shape == (503, 765, 3)
    state["canvas"] = 300                 # the client swapped its canvas: 200 is gone
    W._last = None
    assert win.grab().shape == (503, 765, 3)
    assert win.canvas_hwnd == 300
