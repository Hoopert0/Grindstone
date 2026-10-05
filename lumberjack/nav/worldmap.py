"""A north-up map stitched from minimap captures (localization lives in localizer.py).

Coordinates are map pixels (minimap scale, ~4 px per tile). The map grows as you walk:
each capture is rotated to north-up using the compass needle, placed by matching it
against the previous capture (small search, very reliable), then blended in.
"""
import json
from pathlib import Path

import cv2
import numpy as np

from lumberjack.vision import minimap as MM

MAPS = Path(__file__).resolve().parents[1] / "assets" / "maps"
CANVAS = 3000           # px; ~750 tiles across - plenty for a town + surroundings
START = CANVAS // 2
SEARCH = 60             # px (~15 tiles) we may have moved since the last placed capture
ACCEPT = 0.5            # min normalized correlation to place a capture


class WorldMap:
    def __init__(self, name):
        self.name = name
        self.img = np.zeros((CANVAS, CANVAS, 3), np.float32)
        self.weight = np.zeros((CANVAS, CANVAS), np.float32)
        self.path = []        # walked positions (map px) - known-walkable route
        self.spots = {}       # name -> {"x", "y", "trees": [...]}
        self._prev = None     # (img, mask, x, y) of the last placed capture
        self.lost_frames = 0  # consecutive captures we couldn't place

    # ---- recording -------------------------------------------------------------------
    def add(self, frame):
        """Rotate a frame's minimap to north-up and place it in the map. Returns (x, y) or None.

        The camera may turn freely while recording - the compass needle tells us how much.
        """
        from lumberjack.nav.localizer import FINE, rotate_patch
        raw, rmask = MM.patch(frame)
        needle = MM.heading(frame)      # exact from game data, else the compass needle
        if needle is None:
            self.lost_frames += 1
            return None
        if self._prev is None:
            x, y, angle = START, START, needle
        else:
            # Match the centre of the new capture against everything stitched so far,
            # in a wide window around the last position. Comparing with the whole map
            # (not just the previous frame) means a few rejected frames don't lose us.
            _, _, px, py = self._prev
            c = 45                      # template radius (inner part of the disc)
            win = SEARCH + c
            x0, y0 = px - win, py - win
            region_img = self.img[y0:y0 + 2 * win + 1, x0:x0 + 2 * win + 1]
            region_w = self.weight[y0:y0 + 2 * win + 1, x0:x0 + 2 * win + 1]
            region = (region_img / np.maximum(region_w, 1e-6)[..., None]).clip(0, 255).astype(np.uint8)
            r0 = MM.R_USE - c
            best = (-2.0, 0, 0, needle)
            for a in [needle + d for d in FINE]:
                ri, rm = rotate_patch(raw, rmask, a)
                t, tm = ri[r0:r0 + 2 * c + 1, r0:r0 + 2 * c + 1], rm[r0:r0 + 2 * c + 1, r0:r0 + 2 * c + 1]
                s = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED, mask=tm)
                s[~np.isfinite(s)] = -1
                _, mx, _, (bx, by) = cv2.minMaxLoc(s)
                if mx > best[0]:
                    best = (float(mx), bx, by, a)
            score, mx, my, angle = best
            if score < ACCEPT:   # blurry / mid-turn frame - skip it
                self.lost_frames += 1
                return None
            x, y = x0 + mx + c, y0 + my + c
        self.lost_frames = 0
        img, mask = rotate_patch(raw, rmask, angle)
        mask = cv2.bitwise_and(mask, MM._circle)
        self._blend(img, mask, x, y)
        self._prev = (img, mask, x, y)
        if not self.path or abs(self.path[-1][0] - x) + abs(self.path[-1][1] - y) >= 8:
            self.path.append((int(x), int(y)))
        return x, y

    def resume_at(self, frame):
        """Continue recording an existing map: find where we are and use that as the
        starting point, so new captures join up with the old ones."""
        from lumberjack.nav.localizer import Localizer, rotate_patch
        fix = Localizer(self).locate(frame)
        if not fix.ok:
            return None
        raw, rmask = MM.patch(frame)
        img, mask = rotate_patch(raw, rmask, fix.angle)
        mask = cv2.bitwise_and(mask, MM._circle)
        self._prev = (img, mask, fix.x, fix.y)
        return fix.x, fix.y

    def _blend(self, img, mask, x, y):
        r = MM.R_USE
        ys, xs = slice(y - r, y + r + 1), slice(x - r, x + r + 1)
        m = (mask > 0).astype(np.float32)
        self.img[ys, xs] += img.astype(np.float32) * m[..., None]
        self.weight[ys, xs] += m

    def image(self):
        w = np.maximum(self.weight, 1e-6)[..., None]
        out = (self.img / w).clip(0, 255).astype(np.uint8)
        out[self.weight == 0] = 0
        return out

    # ---- persistence -----------------------------------------------------------------
    def save(self):
        MAPS.mkdir(parents=True, exist_ok=True)
        ys, xs = np.where(self.weight > 0)
        cv2.imwrite(str(MAPS / f"{self.name}.png"), self.image())
        meta = {"path": self.path, "spots": self.spots,
                "bounds": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None}
        (MAPS / f"{self.name}.json").write_text(json.dumps(meta, indent=1))

    @classmethod
    def load(cls, name):
        m = cls(name)
        img = cv2.imread(str(MAPS / f"{name}.png"))
        if img is None:
            raise FileNotFoundError(f"no map named '{name}'")
        m.img = img.astype(np.float32)
        m.weight = (img.max(axis=2) > 0).astype(np.float32)
        meta = json.loads((MAPS / f"{name}.json").read_text())
        m.path = [tuple(p) for p in meta.get("path", [])]
        m.spots = meta.get("spots", {})
        return m
