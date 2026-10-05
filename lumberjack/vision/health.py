"""Hitpoints and "am I fighting?" from the screen (read-only vision, no input).

Hitpoints orb (top-right, regions.HP_ORB): a red orb with a heart in it, and a grey box to
its right with the current HP as a number ("10"). Measured on a full-HP capture:
    orb disc   centre (707, 27), radius ~12 (red pixels x 695..719, y 15..39)
    heart      inside the disc, roughly x 699..716, y 21..36
    number     green (hue 60) glyphs at x 728..736, y 28..35, in a box x ~721..744
Three readings, best first:
    read_hp()          the number, via digit templates this module learns itself
                       (learn_digits(frame, value) with a value known from the Stats tab)
    orb_fill()         how far up the red liquid reaches (the orb drains from the top)
    text_hue_fraction  green -> yellow -> red colour of the number (coarse)
hp_fraction() combines them.

Combat: the client draws a 30x5 health bar (pure green 0x00FF00 + pure red 0xFF0000) above
anything that was hit in the last few seconds, and hitsplats (red = damage, blue = 0) with
white digits on it. So "something near us has a health bar / splat" == we're fighting.

TO VERIFY ON THE LIVE CLIENT (constants marked CALIBRATE):
  * ORB_CENTER / ORB_R / HEART_BOX against a capture at full HP (already measured once)
  * that the orb really drains from the top when hurt (orb_fill); else rely on read_hp
  * PLAYER_BAR_POS: where our own bar appears with the standard top-down camera
  * BAR_* colour/size limits on a capture mid-fight (bars are exact colours in SD mode)
  * SPLAT_* limits on a capture with a hitsplat showing
"""
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from lumberjack.core import regions as R

# ---- hitpoints orb ---------------------------------------------------------------------
ORB_CENTER = (707, 27)            # CALIBRATE (measured once at full HP)
ORB_R = 12
HEART_BOX = (699, 21, 18, 16)     # x, y, w, h - the heart is red too, so skip it for the level
ORB_TEXT = R.Rect(721, 24, 23, 15)
DIGITS = Path(__file__).resolve().parents[1] / "assets" / "templates" / "orb_digits"

ORB_RED_MIN_R = 110               # orb liquid: clearly red
ORB_RED_MARGIN = 50               # r - max(g, b)
ROW_FILLED = 0.5                  # share of a row's orb pixels that must be red to count as filled
TEXT_MIN_S, TEXT_MIN_V = 100, 140  # number glyphs are saturated + bright; the box is grey


def _orb_rows(frame):
    """{y: (red_pixels, orb_pixels)} for each row of the orb disc, heart excluded."""
    cx, cy = ORB_CENTER
    hx, hy, hw, hh = HEART_BOX
    out = {}
    for y in range(cy - ORB_R, cy + ORB_R + 1):
        half = int(np.sqrt(max(ORB_R * ORB_R - (y - cy) ** 2, 0)))
        xs = [x for x in range(cx - half, cx + half + 1) if not (hx <= x < hx + hw and hy <= y < hy + hh)]
        if not xs:
            continue
        px = frame[y, xs].astype(np.int16)
        b, g, r = px[:, 0], px[:, 1], px[:, 2]
        red = (r >= ORB_RED_MIN_R) & (r - np.maximum(g, b) >= ORB_RED_MARGIN)
        out[y] = (int(red.sum()), len(xs))
    return out


def orb_fill(frame):
    """Fraction of the orb filled with red, measured from the bottom up (0..1), or None if
    the orb doesn't look like an orb at all (e.g. covered)."""
    rows = _orb_rows(frame)
    if not rows:
        return None
    total_red = sum(r for r, _ in rows.values())
    if total_red < 6:
        return 0.0 if _text_pixels(frame) > 4 else None   # number visible -> orb really is empty
    cx, cy = ORB_CENTER
    bottom = cy + ORB_R
    top = bottom + 1
    misses = 0
    for y in range(bottom, cy - ORB_R - 1, -1):
        red, n = rows.get(y, (0, 0))
        if n and red / n >= ROW_FILLED:
            top, misses = y, 0
        else:
            misses += 1
            if misses > 2:      # allow a stray dark row (orb rim / highlight)
                break
    return float(np.clip((bottom - top + 1) / (2 * ORB_R + 1), 0.0, 1.0))


def text_mask(frame):
    """Binary mask (bool) of the HP number glyphs inside ORB_TEXT."""
    hsv = cv2.cvtColor(ORB_TEXT.crop(frame), cv2.COLOR_BGR2HSV)
    return (hsv[..., 1] > TEXT_MIN_S) & (hsv[..., 2] > TEXT_MIN_V)


def _text_pixels(frame):
    return int(text_mask(frame).sum())


def text_hue_fraction(frame):
    """Rough HP fraction from the number's colour: green 1.0, yellow 0.5, red 0.0."""
    m = text_mask(frame)
    if m.sum() < 5:
        return None
    hsv = cv2.cvtColor(ORB_TEXT.crop(frame), cv2.COLOR_BGR2HSV)
    h = float(np.median(hsv[..., 0][m]))
    if h > 150:          # red wraps around (h ~ 170-179)
        h = 0.0
    return float(np.clip(h / 60.0, 0.0, 1.0))


def glyphs(mask):
    """Split a bool text mask into per-glyph bitmaps (trimmed), left to right."""
    cols = mask.any(axis=0)
    out, x = [], 0
    while x < len(cols):
        if cols[x]:
            x0 = x
            while x < len(cols) and cols[x]:
                x += 1
            g = mask[:, x0:x]
            ys = np.where(g.any(axis=1))[0]
            out.append(g[ys.min():ys.max() + 1])
        x += 1
    return [g for g in out if g.shape[0] >= 4]   # drop specks (digits are ~8 px tall)


_digits = None


def _digit_templates():
    global _digits
    if _digits is None:
        _digits = {}
        for p in DIGITS.glob("*.png"):
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                _digits[p.stem] = img > 0
    return _digits


def _iou(a, b):
    h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    A = np.zeros((h, w), bool)
    B = np.zeros((h, w), bool)
    A[:a.shape[0], :a.shape[1]] = a
    B[:b.shape[0], :b.shape[1]] = b
    return (A & B).sum() / max((A | B).sum(), 1)


def classify_digit(g, templates, min_iou=0.8):
    best = (0.0, None)
    for name, t in templates.items():
        if abs(t.shape[0] - g.shape[0]) > 1 or abs(t.shape[1] - g.shape[1]) > 1:
            continue
        iou = _iou(t, g)
        if iou > best[0]:
            best = (iou, name)
    return best[1] if best[0] >= min_iou else None


def read_hp(frame):
    """Current HP from the orb number, or None (unknown digit / unreadable)."""
    gl = glyphs(text_mask(frame))
    if not gl or len(gl) > 3:
        return None
    tm = _digit_templates()
    out = ""
    for g in gl:
        d = classify_digit(g, tm)
        if d is None:
            return None
        out += d
    return int(out)


def learn_digits(frame, value):
    """Save templates for the digits of `value` (the HP number currently shown, e.g. read
    from the Stats-tab tooltip). Only digits we don't know yet are written. Returns how
    many new digits were saved."""
    gl = glyphs(text_mask(frame))
    s = str(int(value))
    if len(gl) != len(s):
        return 0
    DIGITS.mkdir(parents=True, exist_ok=True)
    known = _digit_templates()
    saved = 0
    for ch, g in zip(s, gl):
        if ch in known:
            continue
        cv2.imwrite(str(DIGITS / f"{ch}.png"), g.astype(np.uint8) * 255)
        known[ch] = g
        saved += 1
    return saved


def hp_fraction(frame, max_hp=None):
    """Best available HP fraction (0..1) or None. With max_hp (the Hitpoints base level)
    and learned digits this is exact; otherwise the orb fill, then the number colour."""
    if max_hp:
        hp = read_hp(frame)
        if hp is not None and 0 <= hp <= max_hp * 1.3:   # boosted HP can exceed the base
            return min(hp / max_hp, 1.0)
    fill = orb_fill(frame)
    if fill is not None:
        return fill
    return text_hue_fraction(frame)


# ---- health bars & hitsplats -----------------------------------------------------------
BAR_GREEN_MIN, BAR_OTHER_MAX = 190, 70   # pure green: g >= 190, r,b <= 70 (and vice versa red)
BAR_MIN_W, BAR_MAX_W = 20, 40            # the bar is 30 px wide
BAR_MIN_H, BAR_MAX_H = 3, 7              # ... and 5 px tall
BAR_MIN_FILL = 0.8
PLAYER_BAR_POS = (256, 140)              # CALIBRATE: centre of our own bar (top-down camera)
PLAYER_BAR_TOL = (12, 18)
NEAR_ZONE = R.Rect(186, 70, 140, 160)    # "next to us": bars/splats here mean we're fighting
SPLAT_MIN_AREA, SPLAT_MAX_AREA = 60, 700
SPLAT_MIN_WH, SPLAT_MAX_WH = 9, 32
SPLAT_MIN_WHITE = 4                      # white digit pixels inside the splat


@dataclass
class Bar:
    x: int          # canvas coords of the bar's top-left
    y: int
    w: int
    h: int
    frac: float     # green share = the entity's remaining HP

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2


def _pure(frame, chan):
    """Pure-colour mask: channel `chan` (0=b,1=g,2=r) high, the other two low."""
    f = frame.astype(np.int16)
    others = [c for c in range(3) if c != chan]
    return (f[..., chan] >= BAR_GREEN_MIN) & (f[..., others[0]] <= BAR_OTHER_MAX) & (f[..., others[1]] <= BAR_OTHER_MAX)


def find_bars(frame, region=R.VIEWPORT):
    """All health bars in `region` (default: the game view, minus the hover-text strip)."""
    crop = region.crop(frame)
    green, red = _pure(crop, 1), _pure(crop, 2)
    # the hover text ("level-3" can be green) sits in the top strip - ignore it
    top_cut = max(0, R.MOUSEOVER_TEXT.y + R.MOUSEOVER_TEXT.h - region.y)
    green[:top_cut] = False
    red[:top_cut] = False
    both = (green | red).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(both, connectivity=4)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (BAR_MIN_W <= w <= BAR_MAX_W and BAR_MIN_H <= h <= BAR_MAX_H):
            continue
        if area < BAR_MIN_FILL * w * h:
            continue
        g_cols = green[y:y + h, x:x + w].any(axis=0).sum()
        out.append(Bar(int(x + region.x), int(y + region.y), int(w), int(h), float(g_cols) / w))
    return out


def player_bar(frame, bars=None):
    bars = find_bars(frame) if bars is None else bars
    px, py = PLAYER_BAR_POS
    tx, ty = PLAYER_BAR_TOL
    near = [b for b in bars if abs(b.center[0] - px) <= tx and abs(b.center[1] - py) <= ty]
    return min(near, key=lambda b: abs(b.center[0] - px) + abs(b.center[1] - py)) if near else None


def target_bar(frame, bars=None, near=None):
    """The bar most likely to be our opponent's: the nearest one that isn't ours.
    `near` = (x, y) screen point we clicked, if known."""
    bars = find_bars(frame) if bars is None else bars
    mine = player_bar(frame, bars)
    others = [b for b in bars if b is not mine]
    if not others:
        return None
    ref = near or PLAYER_BAR_POS
    return min(others, key=lambda b: np.hypot(b.center[0] - ref[0], b.center[1] - ref[1]))


def hitsplats(frame, zone=NEAR_ZONE):
    """[(x, y, colour)] centres of red/blue hitsplats in `zone` (canvas coords)."""
    crop = zone.crop(frame).astype(np.int16)
    b, g, r = crop[..., 0], crop[..., 1], crop[..., 2]
    white = (r > 200) & (g > 200) & (b > 200)
    masks = {"red": (r > 160) & (g < 60) & (b < 60),
             "blue": (b > 160) & (r < 70) & (g < 100)}
    out = []
    for colour, m in masks.items():
        n, _, stats, cents = cv2.connectedComponentsWithStats(m.astype(np.uint8))
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if not (SPLAT_MIN_AREA <= area <= SPLAT_MAX_AREA):
                continue
            if not (SPLAT_MIN_WH <= w <= SPLAT_MAX_WH and SPLAT_MIN_WH <= h <= SPLAT_MAX_WH):
                continue
            if h < BAR_MAX_H + 2 or not (0.5 <= w / h <= 2.0):
                continue         # flat = a health bar, not a splat
            if white[y:y + h, x:x + w].sum() < SPLAT_MIN_WHITE:
                continue
            out.append((int(cents[i][0] + zone.x), int(cents[i][1] + zone.y), colour))
    return out


def in_combat(frame, bars=None):
    """True while a health bar or hitsplat is showing on/next to our character."""
    bars = find_bars(frame) if bars is None else bars
    if any(NEAR_ZONE.contains(*b.center) for b in bars):
        return True
    return bool(hitsplats(frame))


def draw(frame, bars=None):
    """Debug overlay: bars (yellow box + green share), splats, our bar zone."""
    img = frame.copy()
    bars = find_bars(frame) if bars is None else bars
    for b in bars:
        cv2.rectangle(img, (b.x - 1, b.y - 1), (b.x + b.w, b.y + b.h), (0, 255, 255), 1)
        cv2.putText(img, f"{b.frac:.2f}", (b.x, b.y - 3), cv2.FONT_HERSHEY_PLAIN, 0.8, (0, 255, 255), 1)
    for x, y, c in hitsplats(frame):
        cv2.circle(img, (x, y), 12, (255, 0, 255), 1)
    z = NEAR_ZONE
    cv2.rectangle(img, (z.x, z.y), (z.x + z.w, z.y + z.h), (200, 200, 200), 1)
    return img
