"""v1 rock finder: grey-brown textured boulders + ore-coloured speck scoring.

2009 ore rocks are low-saturation grey/brown boulders, shaded and lumpy (so locally
textured), with small specks in the ore's colour: copper orange-brown, tin pale grey,
iron dark red-brown, coal black, silver near-white, gold yellow, mithril blue, adamant
green, rune cyan. A depleted rock is the same boulder without specks.

In the 530 client every ore rock - full or empty - hovers as "Mine Rocks", so the hover
text only proves "this is a rock"; which ore it holds comes from the specks here.

All thresholds are first guesses (no rock was on screen when this was written) - tune
them with draw() on a capture taken at a mine. HSV is OpenCV's: H 0-180, S/V 0-255.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

from lumberjack.core import regions as R
from lumberjack.vision.trees import Candidate

# ---- rock body ------------------------------------------------------------------------
# Measured on a plain boulder near Draynor (SD, top-down camera): S ~20-24, V 64-115,
# local std-dev median ~5; the grass/dirt around it is S 178-206 with std-dev < 2.
ROCK_S_MAX = 70          # boulders are greyish: low saturation (terrain here is S > 170)
ROCK_V_MIN = 45          # ... not shadow-black
ROCK_V_MAX = 185         # ... not sky/UI white
ROCK_TEXTURE_MIN = 3.0   # local brightness std-dev; shaded rocks > flat walls/floors
MIN_BODY_SHARE = 0.5     # a rock blob is mostly grey body, specks are the minority
TEXTURE_K = 7            # window for the texture measure
MIN_AREA = 250           # px of mask inside a blob
MAX_AREA = 15000
MIN_SIDE = 14            # bbox width/height
MIN_FILL = 0.40          # blob area / bbox area
MAX_ASPECT = 3.0         # w/h or h/w - long thin things are walls/fences/paths
SPECK_REACH = 5          # px around the rock body still searched for specks

# ---- ore specks: ore -> list of (HSV lo, HSV hi) ranges -------------------------------
ORE_SPECKS = {
    "clay":    [((14, 40, 150), (26, 120, 235))],
    "copper":  [((8, 110, 110), (20, 255, 235))],
    "tin":     [((0, 0, 170), (180, 35, 235))],
    "iron":    [((0, 90, 50), (8, 255, 150)), ((170, 90, 50), (180, 255, 150))],
    "silver":  [((0, 0, 215), (180, 25, 255))],
    "coal":    [((0, 0, 0), (180, 255, 38))],
    "gold":    [((20, 120, 150), (32, 255, 255))],
    "mithril": [((113, 60, 60), (135, 255, 230))],   # water is H ~109: keep clear of it
    "adamant": [((50, 60, 50), (80, 255, 210))],
    "rune":    [((84, 80, 120), (104, 255, 255))],
}
# share of a rock's pixels that must be speck-coloured to call it that ore. Ores whose
# colour also shows up as ordinary rock shading/highlights (tin, silver, coal) need more.
SPECK_MIN_FRAC = {"tin": 0.06, "silver": 0.05, "coal": 0.10, "clay": 0.05}
DEFAULT_SPECK_MIN_FRAC = 0.025
SPECK_MIN_PX = 6         # and at least this many pixels


@dataclass
class RockCandidate(Candidate):
    ore: str = None                              # best-scoring ore, None = empty/unknown
    scores: dict = field(default_factory=dict)   # ore -> speck fraction


def _texture(v):
    v = v.astype(np.float32)
    mean = cv2.blur(v, (TEXTURE_K, TEXTURE_K))
    var = cv2.blur(v * v, (TEXTURE_K, TEXTURE_K)) - mean * mean
    return np.sqrt(np.maximum(var, 0))


def speck_mask(hsv, ore):
    out = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in ORE_SPECKS[ore]:
        out |= cv2.inRange(hsv, np.array(lo), np.array(hi))
    return out


def rock_mask(frame):
    """Rock-body pixels in the viewport (viewport-relative mask), specks included."""
    return _masks(cv2.cvtColor(R.VIEWPORT.crop(frame), cv2.COLOR_BGR2HSV))[0]


def _masks(hsv):
    """(blob mask incl. specks, plain rock-body mask) for a viewport HSV image."""
    s, v = hsv[..., 1], hsv[..., 2]
    body = (s <= ROCK_S_MAX) & (v >= ROCK_V_MIN) & (v <= ROCK_V_MAX) & (_texture(v) > ROCK_TEXTURE_MIN)
    body = body.astype(np.uint8) * 255
    body = cv2.morphologyEx(body, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # specks are saturated (or very dark/bright) so they punch holes in the body: add back
    # the ones that sit on/next to rock
    near = cv2.dilate(body, np.ones((2 * SPECK_REACH + 1,) * 2, np.uint8))
    specks = np.zeros_like(body)
    for ore in ORE_SPECKS:
        specks |= speck_mask(hsv, ore)
    mask = body | (specks & near)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    return mask, body


def ore_scores(hsv_patch, blob_mask):
    """ore -> share of the blob's pixels that are that ore's speck colour."""
    area = max(int((blob_mask > 0).sum()), 1)
    out = {}
    for ore in ORE_SPECKS:
        n = int((speck_mask(hsv_patch, ore) & blob_mask).astype(bool).sum())
        out[ore] = n / area if n >= SPECK_MIN_PX else 0.0
    return out


def classify(scores, ores=None):
    """Best ore whose speck share clears its threshold (restricted to `ores` if given)."""
    best, best_ratio = None, 1.0
    for ore, frac in scores.items():
        if ores is not None and ore not in ores:
            continue
        need = SPECK_MIN_FRAC.get(ore, DEFAULT_SPECK_MIN_FRAC)
        ratio = frac / need
        if ratio >= best_ratio:   # how far past its own threshold - fair across ores
            best, best_ratio = ore, ratio
    return best


def find_rocks(frame, ores=None):
    """Rock candidates nearest-first. `ores` limits which ore types classification may
    return (None = all known); `.ore` is None for empty or unrecognised rocks."""
    hsv = cv2.cvtColor(R.VIEWPORT.crop(frame), cv2.COLOR_BGR2HSV)
    mask, body = _masks(hsv)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask)
    px, py = R.PLAYER.center
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not (MIN_AREA <= area <= MAX_AREA) or w < MIN_SIDE or h < MIN_SIDE:
            continue
        if area / (w * h) < MIN_FILL or max(w / h, h / w) > MAX_ASPECT:
            continue
        cx, cy = int(cents[i][0]) + R.VIEWPORT.x, int(cents[i][1]) + R.VIEWPORT.y
        if R.PLAYER.contains(cx, cy):   # our own character (grey armour, pickaxe)
            continue
        blob = (labels[y:y + h, x:x + w] == i).astype(np.uint8) * 255
        if (body[y:y + h, x:x + w] & blob).astype(bool).sum() < MIN_BODY_SHARE * area:
            continue   # mostly "speck" colour: water, an NPC, a coloured object - not a rock
        scores = ore_scores(hsv[y:y + h, x:x + w], blob)
        out.append(RockCandidate(cx, cy, area, (x + R.VIEWPORT.x, y + R.VIEWPORT.y, w, h),
                                 float(np.hypot(cx - px, cy - py)), classify(scores, ores), scores))
    out.sort(key=lambda c: c.dist)
    return out


ORE_DRAW_COLORS = {"clay": (150, 200, 230), "copper": (40, 120, 220), "tin": (200, 200, 200),
                   "iron": (40, 40, 150), "silver": (255, 255, 255), "coal": (60, 60, 60),
                   "gold": (0, 215, 255), "mithril": (200, 80, 60), "adamant": (60, 160, 60),
                   "rune": (220, 220, 60)}


def draw(frame, cands):
    img = frame.copy()
    for k, c in enumerate(cands):
        x, y, w, h = c.bbox
        ore = getattr(c, "ore", None)
        color = ORE_DRAW_COLORS.get(ore, (0, 0, 255))
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2 if k == 0 else 1)
        cv2.drawMarker(img, (c.x, c.y), color, cv2.MARKER_CROSS, 8, 2)
        cv2.putText(img, f"{k} {ore or 'empty'}", (x + 2, y - 3), cv2.FONT_HERSHEY_PLAIN, 0.9, color, 1)
    return img
