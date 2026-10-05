"""v1 fishing-spot finder: water mask + bright ripple clusters on/next to the water.

Fishing spots are NPCs drawn as small white / pale-blue ripple rings on the water. In SD
mode the Draynor water is flat-ish grey-blue (HSV H~108, S~90-115, V~105-125) - very
different from the ripples (V>=160, S<=70). So:

  1. water   = blue hue, mid saturation, in a big connected body (not blue clothing)
  2. ripples = bright, low-saturation pixels in the viewport
  3. keep ripple pixels inside / right next to water, group them into clusters
  4. a cluster must be mostly surrounded by water (rejects white things on the shore)

Optional second frame: ripples animate, so a cluster whose pixels changed between two
frames gets a small ranking bonus (`moving`). It's only a tie-breaker - the water's own
texture and camera drift also produce differences, and seagulls (white, also over water)
move too. Candidates are only guesses: the bot always confirms with the hover text
("Net Fishing spot") before clicking.
"""
from dataclasses import dataclass

import cv2
import numpy as np

from lumberjack.core import regions as R

# water (OpenCV HSV: H 0-179)
WATER_LO = np.array([98, 45, 50])
WATER_HI = np.array([124, 190, 215])
WATER_MIN_AREA = 1500        # px; real water bodies are thousands of px, blue cloth is small
# ripple / foam pixels
RIPPLE_MIN_V = 160
RIPPLE_MAX_S = 75
NEAR_WATER_PX = 6            # ripple pixels may sit on the shoreline, just outside the mask
GROUP_PX = 7                 # ripple pixels this close together form one spot
# cluster filters
MIN_PIXELS = 6               # bright px in a cluster (single specks are noise)
MAX_PIXELS = 700
MIN_SIZE, MAX_SIZE = 4, 70   # bbox side, px
MAX_FILL = 0.75              # bright px / bbox area: ripples are rings, not solid white blobs
MIN_WATER_AROUND = 0.35      # share of a cluster's surroundings (bbox + margin) that is water
AROUND_MARGIN = 6
MOVE_DIFF = 40               # summed BGR change that counts as "this pixel animated"
MOVING_BONUS_PX = 25         # moving clusters sort as if this much closer to us


@dataclass
class Candidate:
    x: int          # suggested hover/click point (canvas coords)
    y: int
    area: int       # bright ripple pixels in the cluster
    bbox: tuple     # (x, y, w, h), canvas coords
    dist: float     # distance from our character
    moving: bool = False


def water_mask(frame):
    """Viewport-sized mask of large blue water bodies."""
    vp = R.VIEWPORT.crop(frame)
    hsv = cv2.cvtColor(vp, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, WATER_LO, WATER_HI)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))   # fill ripple holes
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= WATER_MIN_AREA
    return (keep[labels] * 255).astype(np.uint8)


def ripple_mask(frame, water=None):
    """Viewport-sized mask of bright, low-saturation pixels on or beside water."""
    vp = R.VIEWPORT.crop(frame)
    hsv = cv2.cvtColor(vp, cv2.COLOR_BGR2HSV)
    bright = ((hsv[..., 2] >= RIPPLE_MIN_V) & (hsv[..., 1] <= RIPPLE_MAX_S)).astype(np.uint8) * 255
    if water is None:
        water = water_mask(frame)
    k = 2 * NEAR_WATER_PX + 1
    near = cv2.dilate(water, np.ones((k, k), np.uint8))
    return cv2.bitwise_and(bright, near)


def _moving_mask(frame, prev):
    if prev is None or prev.shape != frame.shape:
        return None
    a = R.VIEWPORT.crop(frame).astype(np.int16)
    b = R.VIEWPORT.crop(prev).astype(np.int16)
    return np.abs(a - b).sum(axis=2) > MOVE_DIFF


def find_fishing_spots(frame, prev=None):
    """Likely fishing spots, nearest to our character first.

    `prev`: an optional earlier frame (~0.2-0.6 s before, same camera) - clusters that
    animated between the two get a ranking bonus.
    """
    water = water_mask(frame)
    if not water.any():
        return []
    rip = ripple_mask(frame, water)
    groups = cv2.dilate(rip, np.ones((GROUP_PX, GROUP_PX), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(groups)
    moving = _moving_mask(frame, prev)
    px, py = R.PLAYER.center
    H, W = water.shape
    out = []
    for i in range(1, n):
        member = (labels == i) & (rip > 0)
        ys, xs = np.where(member)
        cnt = len(xs)
        if not (MIN_PIXELS <= cnt <= MAX_PIXELS):
            continue
        x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
        w, h = int(x1 - x0 + 1), int(y1 - y0 + 1)
        if max(w, h) < MIN_SIZE or max(w, h) > MAX_SIZE:
            continue
        if cnt / float(w * h) > MAX_FILL:
            continue
        ax0, ay0 = max(0, x0 - AROUND_MARGIN), max(0, y0 - AROUND_MARGIN)
        ax1, ay1 = min(W, x1 + AROUND_MARGIN + 1), min(H, y1 + AROUND_MARGIN + 1)
        around = water[ay0:ay1, ax0:ax1] > 0
        if around.mean() < MIN_WATER_AROUND:
            continue
        mov = bool(moving is not None and moving[member].mean() > 0.2)
        # hover the middle of the ripple box - the spot NPC's model covers the rings
        cx, cy = int((x0 + x1) // 2) + R.VIEWPORT.x, int((y0 + y1) // 2) + R.VIEWPORT.y
        out.append(Candidate(cx, cy, int(cnt), (int(x0) + R.VIEWPORT.x, int(y0) + R.VIEWPORT.y, w, h),
                             float(np.hypot(cx - px, cy - py)), mov))
    out.sort(key=lambda c: c.dist - (MOVING_BONUS_PX if c.moving else 0))
    return out


MERGE_PX = 15                # candidates from different frames this close are the same spot


def merge(*cand_lists):
    """Union of candidate lists (e.g. from two frames - a ripple can be invisible in one
    animation frame), de-duplicated by position, nearest first."""
    out = []
    for cands in cand_lists:
        for c in cands:
            dup = next((o for o in out if abs(o.x - c.x) <= MERGE_PX and abs(o.y - c.y) <= MERGE_PX), None)
            if dup is None:
                out.append(c)
            elif c.moving and not dup.moving:
                dup.moving = True
    out.sort(key=lambda c: c.dist - (MOVING_BONUS_PX if c.moving else 0))
    return out


def has_water(frame, min_px=WATER_MIN_AREA):
    """Is any sizeable water in view? (scan the camera if not)"""
    return int((water_mask(frame) > 0).sum()) >= min_px


def draw(frame, cands, show_water=False):
    img = frame.copy()
    if show_water:
        w = water_mask(frame)
        vp = R.VIEWPORT.crop(img)
        vp[w > 0] = (vp[w > 0] * 0.6 + np.array([255, 120, 0]) * 0.4).astype(np.uint8)
    for k, c in enumerate(cands):
        x, y, w, h = c.bbox
        color = (0, 255, 0) if k == 0 else (0, 200, 255)
        cv2.rectangle(img, (x - 2, y - 2), (x + w + 2, y + h + 2), color, 1)
        cv2.drawMarker(img, (c.x, c.y), color, cv2.MARKER_CROSS, 8, 1)
        cv2.putText(img, f"{k}{'*' if c.moving else ''}", (x, y - 4), cv2.FONT_HERSHEY_PLAIN, 0.9, color, 1)
    return img
