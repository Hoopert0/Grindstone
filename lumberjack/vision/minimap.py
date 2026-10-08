"""Minimap reading for navigation.

The minimap is a north-up-when-the-camera-faces-north circle of flat-coloured tiles
(~4 px per tile) with tree/rock icons and paths drawn in. That static content is what
we match against a stitched map. Moving things - NPC (yellow), player (white) and item
(red) dots - plus our own marker in the centre are masked out.

The minimap rotates with the camera, so navigation always clicks the compass first.
"""
from pathlib import Path

import cv2
import numpy as np

CENTER = (627, 85)   # our white marker sits exactly here (canvas coords)
R_USE = 66           # radius we trust (the outer ring is frame + overlapping orbs)
SIZE = 2 * R_USE + 1

COMPASS_BOX = (527, 6, 31, 31)  # x, y, w, h around the compass needle
NORTH_REF = Path(__file__).resolve().parents[1] / "assets" / "templates" / "compass_north.png"

_circle = np.zeros((SIZE, SIZE), np.uint8)
cv2.circle(_circle, (R_USE, R_USE), R_USE, 255, -1)


def patch(frame):
    """(image, mask) of the usable minimap disc, SIZE x SIZE, centred on our marker."""
    cx, cy = CENTER
    img = frame[cy - R_USE:cy + R_USE + 1, cx - R_USE:cx + R_USE + 1].copy()
    b, g, r = [img[..., i].astype(np.int16) for i in range(3)]
    yellow = (r > 190) & (g > 190) & (b < 110)
    white = (r > 225) & (g > 225) & (b > 225)
    red = (r > 190) & (g < 90) & (b < 90)
    dots = ((yellow | white | red) * 255).astype(np.uint8)
    dots = cv2.dilate(dots, np.ones((5, 5), np.uint8))
    mask = _circle.copy()
    mask[dots > 0] = 0
    mask[R_USE - 3:R_USE + 4, R_USE - 3:R_USE + 4] = 0  # our own marker
    return img, mask


COMPASS_CENTER = (543, 23)
NEEDLE_R = 7   # the needle; red tick marks on the dial sit further out


def needle_angle(frame):
    """Camera heading from the compass needle: degrees clockwise from screen-up that
    north currently points (0 = camera facing north). None if the needle isn't found."""
    cx, cy = COMPASS_CENTER
    r = NEEDLE_R
    roi = frame[cy - r:cy + r + 1, cx - r:cx + r + 1].astype(np.int16)
    b, g, rr = roi[..., 0], roi[..., 1], roi[..., 2]
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    inside = xx * xx + yy * yy <= r * r
    red = inside & (rr > 140) & (g < 90) & (b < 90)
    blue = inside & (b > 140) & (rr < 90) & (g < 120)
    if red.sum() < 3 or blue.sum() < 3:
        return None
    dx = xx[red].mean() - xx[blue].mean()
    dy = yy[red].mean() - yy[blue].mean()
    return float(np.degrees(np.arctan2(dx, -dy)) % 360)


def _game_minimap():
    """The game's own {'angle', 'scale'} for the minimap, or None without game data."""
    from lumberjack.core import gamestate
    gs = gamestate.shared()
    if gs is None:
        return None
    try:
        return gs.minimap()
    except gamestate.GameBusy:
        return None
    except gamestate.GameStateError:
        gamestate.drop_shared()
        return None


def heading(frame=None):
    """Degrees clockwise from screen-up that north points ON THE MINIMAP. From the game's data
    when readable - exact, and including the rotation the client adds to the minimap at login
    (up to ~10 degrees the compass needle doesn't show) - else read from the compass needle."""
    mm = _game_minimap()
    if mm is not None:
        return mm["angle"] * 360.0 / 2048.0
    return needle_angle(frame) if frame is not None else None


def click_scale():
    """Minimap px per map px: the client also scales the minimap +-8% at login (1.0 without
    game data). Multiply a map-px move by this before clicking."""
    mm = _game_minimap()
    return float(mm["scale"]) if mm is not None else 1.0


def patch_north(frame):
    """Minimap patch rotated so north is up. (img, mask, angle)."""
    img, mask = patch(frame)
    a = heading(frame)
    if a is None:
        return img, mask, None
    M = cv2.getRotationMatrix2D((R_USE, R_USE), a, 1.0)
    img = cv2.warpAffine(img, M, (SIZE, SIZE))
    mask = cv2.warpAffine(mask, M, (SIZE, SIZE), flags=cv2.INTER_NEAREST)
    mask = cv2.bitwise_and(mask, _circle)
    return img, mask, a


def _compass(frame):
    x, y, w, h = COMPASS_BOX
    return frame[y:y + h, x:x + w]


def save_north_reference(frame):
    NORTH_REF.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(NORTH_REF), _compass(frame))


def facing_north(frame, tol=12.0):
    ref = cv2.imread(str(NORTH_REF))
    if ref is None:
        return False
    return float(np.abs(_compass(frame).astype(np.int16) - ref.astype(np.int16)).mean()) < tol
