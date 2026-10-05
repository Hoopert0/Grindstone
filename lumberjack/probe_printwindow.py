"""Probe: capture the game window via PrintWindow (works even if the window is covered)."""
import ctypes
from pathlib import Path

import cv2
import numpy as np
import win32gui
import win32ui

ctypes.windll.shcore.SetProcessDpiAwareness(2)
PW_RENDERFULLCONTENT = 0x2
OUT = Path(__file__).parent / "captures"


def find_game():
    hits = []
    win32gui.EnumWindows(
        lambda h, _: hits.append(h) if win32gui.IsWindowVisible(h) and "2009scape [local]" in win32gui.GetWindowText(h).lower() else None,
        None,
    )
    return hits[0] if hits else None


def printwindow(hwnd, flags):
    l, t, r, b = win32gui.GetClientRect(hwnd)
    w, h = r - l, b - t
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    ok = ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), flags | 0x1)  # 0x1 = PW_CLIENTONLY
    img = np.frombuffer(bmp.GetBitmapBits(True), dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC(); src.DeleteDC(); win32gui.ReleaseDC(hwnd, hdc)
    return ok, img


hwnd = find_game()
print("hwnd", hwnd, win32gui.GetWindowText(hwnd))
OUT.mkdir(exist_ok=True)
for name, flags in [("plain", 0), ("fullcontent", PW_RENDERFULLCONTENT)]:
    ok, img = printwindow(hwnd, flags)
    m = img.max(axis=2) > 8
    ys, xs = np.where(m)
    bbox = (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None
    cv2.imwrite(str(OUT / f"pw_{name}.png"), img)
    print(f"{name}: ok={ok} shape={img.shape} mean={img.mean():.1f} content_bbox={bbox}")
