"""v1 fire finder: bright, saturated orange/yellow flame blobs near our character.

A lit fire is an animated object of orange-yellow flames. Little else in the scene is that
saturated AND that bright: Draynor sand and the willows' trunks are much duller. Candidates
are only guesses - the cooking code always confirms with the hover text
("Use Raw shrimps -> Fire") before clicking.

Fires we light ourselves sit on the tile next to us (the character steps off the fire), so
candidates are sorted by distance from the character and far blobs are dropped.

Optional second frame: flames flicker, so a blob whose pixels changed gets a ranking bonus.
"""
from dataclasses import dataclass

import cv2
import numpy as np

from lumberjack.core import regions as R

# flame pixels (OpenCV HSV: H 0-179)
FLAME_LO = np.array([5, 150, 190])
FLAME_HI = np.array([32, 255, 255])
GROUP_PX = 5                  # flame pixels this close together form one fire
MIN_PIXELS = 12
MAX_PIXELS = 1500
MAX_DIST = 170                # px from the character; our own fire is a tile or two away
MOVE_DIFF = 40                # summed BGR change that counts as "this pixel flickered"
MOVING_BONUS_PX = 30


@dataclass
class Candidate:
    x: int
    y: int
    area: int
    bbox: tuple       # (x, y, w, h), canvas coords
    dist: float
    moving: bool = False


def flame_mask(frame):
    """Viewport-sized mask of flame-coloured pixels (the hover-text strip excluded)."""
    vp = R.VIEWPORT.crop(frame)
    hsv = cv2.cvtColor(vp, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, FLAME_LO, FLAME_HI)
    t = R.MOUSEOVER_TEXT
    m[t.y - R.VIEWPORT.y:t.y - R.VIEWPORT.y + t.h, t.x - R.VIEWPORT.x:t.x - R.VIEWPORT.x + t.w] = 0
    return m


def find_fires(frame, prev=None):
    """Fire candidates, nearest the character first (flickering ones get a bonus)."""
    m = flame_mask(frame)
    if not m.any():
        return []
    grouped = cv2.dilate(m, np.ones((GROUP_PX, GROUP_PX), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grouped)
    moved = None
    if prev is not None:
        d = np.abs(R.VIEWPORT.crop(frame).astype(np.int16) - R.VIEWPORT.crop(prev).astype(np.int16)).sum(axis=2)
        moved = d > MOVE_DIFF
    px, py = R.PLAYER.center
    out = []
    for i in range(1, n):
        comp = (labels == i) & (m > 0)
        area = int(comp.sum())
        if not MIN_PIXELS <= area <= MAX_PIXELS:
            continue
        ys, xs = np.where(comp)
        cx, cy = int(xs.mean()) + R.VIEWPORT.x, int(ys.mean()) + R.VIEWPORT.y
        dist = float(np.hypot(cx - px, cy - py))
        if dist > MAX_DIST:
            continue
        bx, by, bw, bh = (int(v) for v in stats[i][:4])
        moving = bool(moved is not None and moved[comp].mean() > 0.15)
        out.append(Candidate(cx, cy, area, (bx + R.VIEWPORT.x, by + R.VIEWPORT.y, bw, bh), dist, moving))
    out.sort(key=lambda c: c.dist - (MOVING_BONUS_PX if c.moving else 0))
    return out


def draw(frame, cands):
    img = frame.copy()
    for c in cands:
        x, y, w, h = c.bbox
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 140, 255), 1)
    return img
