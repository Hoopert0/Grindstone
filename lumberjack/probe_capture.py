"""M1 probe: find the 2009scape game window, capture its client area, sanity-check it.

Run:  %USERPROFILE%\\.venvs\\lumberjack\\Scripts\\python.exe lumberjack\\probe_capture.py
"""
import ctypes
import sys
from pathlib import Path

import cv2
import mss
import numpy as np
import win32gui

# Make window coordinates match real screen pixels on scaled (125%/150%) displays.
ctypes.windll.shcore.SetProcessDpiAwareness(2)

OUT = Path(__file__).parent / "captures"


def find_game_windows():
    hits = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        t = title.lower()
        if ("2009scape" in t or "runescape" in t) and "launcher" not in t and "chrome" not in t:
            hits.append((hwnd, title))

    win32gui.EnumWindows(cb, None)
    return hits


def client_rect_on_screen(hwnd):
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    x, y = win32gui.ClientToScreen(hwnd, (left, top))
    return {"left": x, "top": y, "width": right - left, "height": bottom - top}


def main():
    wins = find_game_windows()
    if not wins:
        print("No game window found. Is 2009scape Singleplayer running (not just the launcher)?")
        all_titles = []
        win32gui.EnumWindows(
            lambda h, _: all_titles.append(win32gui.GetWindowText(h)) if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None,
            None,
        )
        print("Visible windows:", all_titles)
        sys.exit(1)

    OUT.mkdir(exist_ok=True)
    with mss.mss() as sct:
        for hwnd, title in wins:
            rect = client_rect_on_screen(hwnd)
            img = np.array(sct.grab(rect))[:, :, :3]  # BGRA -> BGR
            mean = img.mean()
            black = mean < 5
            path = OUT / f"client_{hwnd}.png"
            cv2.imwrite(str(path), img)
            print(f"[{hwnd}] '{title}'  client={rect['width']}x{rect['height']} at ({rect['left']},{rect['top']})  "
                  f"mean={mean:.1f}  {'BLACK FRAME!' if black else 'looks OK'}  -> {path.name}")


if __name__ == "__main__":
    main()
