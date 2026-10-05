"""Lets the offline tests run off Windows (e.g. a cloud session): if pywin32 isn't
installed, empty stand-ins are registered so modules that import win32api etc. load.
On the PC the real modules import and nothing here changes."""
import sys
import types

for _name in ("win32api", "win32con", "win32gui", "win32ui"):
    try:
        __import__(_name)
    except ImportError:
        _m = types.ModuleType(_name)
        _m.GetAsyncKeyState = lambda key: 0
        _m.__getattr__ = lambda attr: 0          # constants (win32con.VK_LEFT...) read as 0
        sys.modules[_name] = _m

# the window module calls Windows' DPI API at import time
if sys.platform != "win32" and "lumberjack.core.window" not in sys.modules:
    _w = types.ModuleType("lumberjack.core.window")

    class GameWindow:
        def __init__(self, *a, **k):
            raise RuntimeError("tests must not open the game window")

    _w.GameWindow = GameWindow
    sys.modules["lumberjack.core.window"] = _w
