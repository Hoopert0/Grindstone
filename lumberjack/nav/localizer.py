"""Where am I? - from the minimap alone.

The minimap's heading (vision/minimap.heading: exact from the game's data - the client
rotates the minimap up to ~10 degrees past the compass at login - else the compass needle)
lets the live minimap be rotated to north-up, then matched against the north-up stitched
map. A few nearby angles are tried too.

Conventions: `angle` = degrees clockwise from screen-up that north points on the live
minimap (rotating the live minimap CCW by `angle` makes it north-up). Map px ~ minimap px.
"""
from dataclasses import dataclass

import cv2
import numpy as np

from lumberjack.vision import minimap as MM

# Trust rules (normalized correlation). Parts of a stitched map are blurrier than others,
# so a modest score is fine if the match clearly beats every other place on the map, or
# if it's right next to our last good fix.
STRONG = 0.60
WEAK = 0.38
MARGIN = 0.08
FINE = (-6, -3, 0, 3, 6)
_C = (MM.R_USE, MM.R_USE)


@dataclass
class Fix:
    x: int
    y: int
    angle: float
    score: float
    margin: float = 0.0     # best minus runner-up elsewhere on the map
    local: bool = False     # found by searching near the previous fix

    @property
    def ok(self):
        if self.score >= STRONG:
            return True
        if self.score >= WEAK and (self.local or self.margin >= MARGIN):
            return True
        # a blurry patch of map can score low but still stand far above every rival
        return self.score >= 0.30 and self.margin >= 0.12


def rotate_patch(img, mask, angle):
    M = cv2.getRotationMatrix2D(_C, angle, 1.0)
    ri = cv2.warpAffine(img, M, (MM.SIZE, MM.SIZE))
    rm = cv2.warpAffine(mask, M, (MM.SIZE, MM.SIZE), flags=cv2.INTER_NEAREST)
    return ri, rm


def best_match(search, img, mask, angles):
    """(score, x, y, angle, margin): best top-left of the rotated patch inside `search`."""
    best = (-2.0, 0, 0, 0.0, 0.0)
    if search.shape[0] < MM.SIZE or search.shape[1] < MM.SIZE:
        return best
    for a in angles:
        ri, rm = rotate_patch(img, mask, a)
        s = cv2.matchTemplate(search, ri, cv2.TM_CCOEFF_NORMED, mask=rm)
        s[~np.isfinite(s)] = -1
        _, mx, _, (x, y) = cv2.minMaxLoc(s)
        if mx > best[0]:
            s[max(y - 15, 0):y + 16, max(x - 15, 0):x + 16] = -1
            best = (float(mx), x, y, float(a), float(mx - s.max()))
    return best


class Localizer:
    def __init__(self, worldmap):
        self.map = worldmap
        self.img = worldmap.image()
        ys, xs = np.where(worldmap.weight > 0)
        self.bounds = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        self.last = None   # last good Fix

    def _search(self, img, mask, box, angles, local=False):
        x0, y0, x1, y1 = box
        r = MM.R_USE
        h, w = self.img.shape[:2]
        sx0, sy0 = max(int(x0) - r, 0), max(int(y0) - r, 0)
        sx1, sy1 = min(int(x1) + r + 1, w), min(int(y1) + r + 1, h)
        score, x, y, a, margin = best_match(self.img[sy0:sy1, sx0:sx1], img, mask, angles)
        return Fix(sx0 + x + r, sy0 + y + r, a, score, margin, local)

    def locate(self, frame, radius=60):
        img, mask = MM.patch(frame)
        needle = MM.heading(frame)      # exact from game data, else the compass needle
        angles = [needle + d for d in FINE] if needle is not None else range(0, 360, 10)
        fix = None
        if self.last is not None:
            lx, ly = self.last.x, self.last.y
            fix = self._search(img, mask, (lx - radius, ly - radius, lx + radius, ly + radius), angles, local=True)
        if fix is None or not fix.ok:
            fix = self._search(img, mask, self.bounds, angles)
        if fix.ok:
            self.last = fix
        return fix

    # ---- map vector <-> minimap click ---------------------------------------------------
    @staticmethod
    def map_to_minimap(dx, dy, angle):
        """A move of (dx, dy) map px (north-up) -> offset to click on the live minimap."""
        M = cv2.getRotationMatrix2D((0, 0), angle, 1.0)[:, :2]  # live minimap -> map
        v = np.linalg.solve(M, np.array([dx, dy], float))
        return float(v[0]), float(v[1])
