"""v1 tree finder: HSV color mask + blob filtering.

Normal-tree canopies in SD mode are green (H~35-38) with *textured* saturation (~110-200),
whereas grass at the same hue is flat-shaded with saturation pinned around 203. So
"canopy green but not grass-saturated" isolates leaves; blobs are then size-filtered.
Candidates are only guesses - the bot always confirms with the hover text before clicking.
"""
from dataclasses import dataclass

import cv2
import numpy as np

from lumberjack.core import regions as R

CANOPY_LO = np.array([30, 80, 20])
CANOPY_HI = np.array([46, 215, 170])
MIN_AREA = 900          # px of mask inside a blob (filters bushes / grass streaks)
MAX_AREA = 40000
MIN_FILL = 0.45         # blob area / bbox area; canopies measure ~0.65, ferns & edges ~0.2-0.3


@dataclass
class Candidate:
    x: int          # suggested click point (canvas coords)
    y: int
    area: int
    bbox: tuple     # (x, y, w, h)
    dist: float     # distance from our character


TEXTURE_MIN = 14.0     # local brightness std-dev; canopies are speckled, grass is flat


def canopy_mask(frame):
    """Leaf pixels: canopy-green hue AND locally textured.

    Grass shades differ between areas (Lumbridge S~203, Draynor S~178/204), but grass is
    always flat-shaded while leaves are speckled - so texture, not a fixed saturation
    band, separates them anywhere.
    """
    vp = R.VIEWPORT.crop(frame)
    hsv = cv2.cvtColor(vp, cv2.COLOR_BGR2HSV)
    hue = cv2.inRange(hsv, CANOPY_LO, CANOPY_HI)
    v = hsv[..., 2].astype(np.float32)
    mean = cv2.blur(v, (7, 7))
    var = cv2.blur(v * v, (7, 7)) - mean * mean
    textured = (np.sqrt(np.maximum(var, 0)) > TEXTURE_MIN).astype(np.uint8) * 255
    mask = cv2.bitwise_and(hue, textured)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    # cut thin bridges (trunks, branches, ground clutter) that merge neighbouring canopies
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
    return mask


def find_trees(frame):
    mask = canopy_mask(frame)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask)
    px, py = R.PLAYER.center
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (MIN_AREA <= area <= MAX_AREA):
            continue
        if w < 25 or h < 25:
            continue
        if area / (w * h) < MIN_FILL:  # canopies are dense blobs; ferns/edges are sparse
            continue
        # click the blob's centroid (canopy centre is reliably on the tree model)
        cx, cy = int(cents[i][0]) + R.VIEWPORT.x, int(cents[i][1]) + R.VIEWPORT.y
        out.append(Candidate(cx, cy, int(area), (x + R.VIEWPORT.x, y + R.VIEWPORT.y, w, h),
                             float(np.hypot(cx - px, cy - py))))
    out.sort(key=lambda c: c.dist)
    return out


def draw(frame, cands):
    img = frame.copy()
    for k, c in enumerate(cands):
        x, y, w, h = c.bbox
        color = (0, 255, 0) if k == 0 else (0, 200, 255)
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 1)
        cv2.drawMarker(img, (c.x, c.y), color, cv2.MARKER_CROSS, 10, 2)
        cv2.putText(img, f"{k}", (x + 2, y + 12), cv2.FONT_HERSHEY_PLAIN, 1, color, 1)
    return img
