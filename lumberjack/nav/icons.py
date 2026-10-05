"""Walking by minimap icons - banking without a recorded map.

The bank's "$" icon is drawn upright on the minimap wherever the camera points, so it can
be found by template matching. Its offset from our marker, turned north-up with the compass
heading, is a landmark:
    to the bank:   click the minimap toward the icon until we're next to it
    back again:    we remembered where the icon was (north-up) while standing at the
                   fishing spot; walk until it's there again
The icon has to be on the minimap from the fishing spot (~16 tiles), e.g. Draynor shrimps.
"""
import logging
import math
import time
from pathlib import Path

import cv2
import numpy as np

from lumberjack.nav.localizer import Localizer
from lumberjack.vision import minimap as MM

log = logging.getLogger("walker")

BANK_ICON = Path(__file__).resolve().parents[1] / "assets" / "templates" / "minimap_bank.png"
MATCH_MIN = 0.75
STEP = 44            # minimap px per click at most (stays inside the disc)
AT_BANK = 10         # minimap px (~2-3 tiles) from the icon counts as "at the bank"
BACK_OK = 6          # minimap px from the remembered spot counts as "back"
MAX_CLICKS = 10

_tmpl = None


def find_icons(frame, tmpl=None, min_score=MATCH_MIN):
    """[(dx, dy)] live-minimap offsets (from our marker) of the icon, nearest first."""
    global _tmpl
    if tmpl is None:
        if _tmpl is None:
            _tmpl = cv2.imread(str(BANK_ICON))
        tmpl = _tmpl
    cx, cy = MM.CENTER
    r = MM.R_USE
    disc = frame[cy - r:cy + r + 1, cx - r:cx + r + 1]
    res = cv2.matchTemplate(disc, tmpl, cv2.TM_CCOEFF_NORMED)
    th, tw = tmpl.shape[:2]
    out = []
    while True:
        _, score, _, (x, y) = cv2.minMaxLoc(res)
        if score < min_score:
            break
        dx, dy = x + tw / 2 - r, y + th / 2 - r
        if math.hypot(dx, dy) <= r - 4:
            out.append((float(dx), float(dy)))
        res[max(0, y - th):y + th, max(0, x - tw):x + tw] = -1   # suppress this one
    return sorted(out, key=lambda p: math.hypot(*p))


def to_north(dx, dy, angle):
    """Live-minimap offset -> north-up offset (angle = the minimap's heading, MM.heading)."""
    M = cv2.getRotationMatrix2D((0, 0), angle, 1.0)[:, :2]
    v = M @ np.array([dx, dy], float)
    return float(v[0]), float(v[1])


def step_toward(dx, dy, step=STEP):
    """Clamp a click offset to `step` px."""
    d = math.hypot(dx, dy)
    return (dx, dy) if d <= step else (dx * step / d, dy * step / d)


class IconWalker:
    def __init__(self, ctx):
        self.ctx = ctx

    def bank_offset(self):
        """North-up minimap offset of the nearest bank icon, or None."""
        frame = self.ctx.grab()
        icons = find_icons(frame)
        angle = MM.heading(frame)
        if not icons or angle is None:
            return None
        return to_north(*icons[0], angle)

    def _click(self, ndx, ndy):
        angle = MM.heading(self.ctx.grab()) or 0.0
        sx, sy = step_toward(ndx, ndy)
        cx, cy = Localizer.map_to_minimap(sx, sy, angle)   # (offsets were measured on the minimap itself)
        self.ctx.inp.click(MM.CENTER[0] + cx, MM.CENTER[1] + cy)
        self._wait_until_still()

    def _wait_until_still(self, timeout=10.0):
        prev, still_since, start = None, None, time.monotonic()
        self.ctx.sleep(0.6)
        while time.monotonic() - start < timeout:
            img, mask = MM.patch(self.ctx.grab())
            if prev is not None:
                m = (mask > 0) & (prev[1] > 0)
                diff = float(np.abs(img.astype(np.int16) - prev[0].astype(np.int16))[m].mean()) if m.any() else 99
                if diff < 1.5:
                    still_since = still_since or time.monotonic()
                    if time.monotonic() - still_since > 0.9:
                        return
                else:
                    still_since = None
            prev = (img, mask)
            self.ctx.sleep(0.15)

    def to_bank(self):
        for _ in range(MAX_CLICKS):
            off = self.bank_offset()
            if off is None:
                log.warning("No bank icon on the minimap")
                return False
            if math.hypot(*off) <= AT_BANK:
                return True
            self._click(*off)
        return math.hypot(*(self.bank_offset() or (99, 99))) <= AT_BANK * 2

    def back_to(self, anchor):
        """Walk until the bank icon sits at `anchor` (north-up offset) again."""
        for _ in range(MAX_CLICKS):
            off = self.bank_offset()
            if off is None:
                log.warning("Lost the bank icon on the way back")
                return False
            move = (off[0] - anchor[0], off[1] - anchor[1])
            if math.hypot(*move) <= BACK_OK:
                return True
            self._click(*move)
        return False
