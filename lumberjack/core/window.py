"""Locate the 2009scape client window and grab frames from it (even when covered)."""
import ctypes
import threading
import time

import numpy as np
import win32con
import win32gui
import win32ui

ctypes.windll.shcore.SetProcessDpiAwareness(2)

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
CANVAS_W, CANVAS_H = 765, 503  # fixed-mode game canvas
FRAME_REUSE_S = 0.04

_grab_lock = threading.Lock()
_last = None  # (hwnd or "agent", time, image) of the most recent capture
_full_at = 0.0  # last full-content PrintWindow


class GameMinimized(RuntimeError):
    """The game window is minimized - Windows gives it no size, so there's nothing to capture."""


class GameWindow:
    def __init__(self, title_match="2009scape [local]"):
        self.title_match = title_match.lower()
        self.hwnd = self._find(self.title_match)
        if not self.hwnd:
            raise RuntimeError("2009scape Singleplayer window not found - is the game running and logged in?")
        self.canvas_offset = None  # (x, y) of the 765x503 canvas inside the client area
        self.canvas_hwnd = self._find_canvas()  # Java's SunAwtCanvas child; receives our input

    def _find_canvas(self):
        kids = []
        win32gui.EnumChildWindows(
            self.hwnd, lambda h, _: kids.append(h) if win32gui.GetClassName(h) == "SunAwtCanvas" else None, None
        )
        return kids[0] if kids else self.hwnd

    @staticmethod
    def _find(match):
        hits = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and match in win32gui.GetWindowText(h).lower():
                hits.append(h)

        win32gui.EnumWindows(cb, None)
        return hits[0] if hits else None

    @property
    def title(self):
        return win32gui.GetWindowText(self.hwnd)

    def grab_client(self):
        """Full client area as a BGR array, via PrintWindow.

        Captures are serialized process-wide and reused for ~40 ms, so the bot and the
        control panel's live view share frames instead of hammering the game window.
        """
        global _last
        with _grab_lock:
            now = time.monotonic()
            if _last and _last[0] == self.hwnd and now - _last[1] < FRAME_REUSE_S:
                return _last[2]
            img = self._printwindow(PW_CLIENTONLY)
            global _full_at
            if img.max() < 8 and now - _full_at > 30:   # some renderers only show up with full-content
                _full_at = now                         # capture - rarely: it's the heavy, risky one
                img = self._printwindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT)
            _last = (self.hwnd, time.monotonic(), img)
            return img

    def _printwindow(self, flags):
        l, t, r, b = win32gui.GetClientRect(self.hwnd)
        w, h = r - l, b - t
        hdc = win32gui.GetWindowDC(self.hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        try:
            ctypes.windll.user32.PrintWindow(self.hwnd, mem.GetSafeHdc(), flags)
            img = np.frombuffer(bmp.GetBitmapBits(True), dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(self.hwnd, hdc)
        return img

    def _locate_canvas(self, client):
        ys, xs = np.where(client.max(axis=2) > 8)
        if not len(xs):
            raise RuntimeError("Client area is all black - game minimized or not rendering?")
        x, y = int(xs.min()), int(ys.min())
        w, h = int(xs.max()) - x + 1, int(ys.max()) - y + 1
        if (w, h) != (CANVAS_W, CANVAS_H):
            raise RuntimeError(f"Game canvas is {w}x{h}, expected {CANVAS_W}x{CANVAS_H}. Switch the client to Fixed mode.")
        return x, y

    def _bitblt_canvas(self):
        """Copy the canvas pixels Windows already holds - no repaint request to the game.

        (PrintWindow makes the game redraw itself into our buffer; with the GDI pipeline
        that shows up as visible flicker.)
        """
        h = self.canvas_hwnd
        l, t, r, b = win32gui.GetClientRect(h)
        w, ht = r - l, b - t
        hdc = win32gui.GetDC(h)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, ht)
        mem.SelectObject(bmp)
        try:
            mem.BitBlt((0, 0), (w, ht), src, (0, 0), win32con.SRCCOPY)
            img = np.frombuffer(bmp.GetBitmapBits(True), dtype=np.uint8).reshape(ht, w, 4)[:, :, :3].copy()
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(h, hdc)
        return img

    def minimized(self):
        return bool(win32gui.IsIconic(self.hwnd))

    def refind(self):
        """The client replaced its canvas (or the whole window): look both up again."""
        if not win32gui.IsWindow(self.hwnd):
            hwnd = self._find(self.title_match)
            if not hwnd:
                raise RuntimeError("2009scape window not found any more - was the game closed?")
            self.hwnd = hwnd
        self.canvas_hwnd = self._find_canvas()
        self.canvas_offset = None

    def grab(self):
        """The 765x503 game canvas. All bot coordinates are relative to this. Survives the Java
        client swapping its canvas window (a stale handle -> 'Invalid window handle')."""
        for attempt in range(4):
            try:
                return self._grab_once()
            except win32gui.error:
                if attempt == 3:
                    raise
                time.sleep(0.2 * (attempt + 1))
                self.refind()

    def _grab_once(self):
        global _last
        if self.minimized():
            raise GameMinimized("the game window is minimized - restore it so the bot can see the game")
        with _grab_lock:                      # first choice: the frame from inside the game (no screen grab)
            now = time.monotonic()
            if _last and _last[0] == "agent" and now - _last[1] < FRAME_REUSE_S:
                return _last[2]
        from lumberjack.core import frames
        img = frames.grab()
        if img is not None and img.shape[:2] == (CANVAS_H, CANVAS_W):
            with _grab_lock:
                _last = ("agent", time.monotonic(), img)
            if self.canvas_offset is None:
                try:
                    x, y = win32gui.ScreenToClient(self.hwnd, win32gui.GetWindowRect(self.canvas_hwnd)[:2])
                    self.canvas_offset = (x, y)
                except win32gui.error:
                    pass
            return img
        if self.canvas_hwnd != self.hwnd:
            with _grab_lock:
                now = time.monotonic()
                if _last and _last[0] == self.canvas_hwnd and now - _last[1] < FRAME_REUSE_S:
                    return _last[2]
                img = self._bitblt_canvas()
                if img.shape[:2] == (CANVAS_H, CANVAS_W) and img.max() >= 8:
                    if self.canvas_offset is None:
                        x, y = win32gui.ScreenToClient(self.hwnd, win32gui.GetWindowRect(self.canvas_hwnd)[:2])
                        self.canvas_offset = (x, y)
                    _last = (self.canvas_hwnd, time.monotonic(), img)
                    return img
        # fallback: PrintWindow the whole client and crop the canvas out of it
        client = self.grab_client()
        if self.canvas_offset is None:
            self.canvas_offset = self._locate_canvas(client)
        x, y = self.canvas_offset
        return client[y:y + CANVAS_H, x:x + CANVAS_W]

    def canvas_to_screen(self, cx, cy):
        """Canvas coords -> absolute screen coords (for mouse input later)."""
        ox, oy = self.canvas_offset or (0, 0)
        return win32gui.ClientToScreen(self.hwnd, (ox + cx, oy + cy))
