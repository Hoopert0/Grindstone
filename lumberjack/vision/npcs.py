"""Where are the NPCs? Candidate screen points to hover, plus NPC hover-text matching.

NPC figures are small, varied and animated, so instead of recognising them on screen we
read the minimap: every NPC is a yellow dot there (~4x4 px, ~4 minimap px per tile). The
minimap rotates together with the camera, so a dot's offset from our marker points the
same way on screen - it only needs scaling (and a little perspective) to land on the
NPC's tile in the game view. Points are guesses; the bot hovers a few around each and
only clicks when the hover text reads "Attack <name>".

Optionally, frame-difference blobs (things that moved) add candidates the minimap missed.

NPC hover text: "Attack Chicken (level-1) / 2 more options" - white action, YELLOW name,
then a "(level-N)" suffix whose colour depends on the level gap (often yellow-ish too).
So unlike mouseover._target_ok we can't demand the whole coloured run has the template's
width; instead the name must start where the coloured text starts and be followed by a
gap (a space) - "Cow" won't pass inside "Cow calf"... unless calf is also a target.

CALIBRATE (live client, standard top-down camera from actions.reset_camera):
  SCREEN_PER_MM_X/Y, PLAYER_TILE, AIM_UP: hover a few NPCs and compare their screen
  position with project(dot). Other players are WHITE dots and also visible on screen, so
  they make good reference points. First estimate (one capture): ~9.5 px per minimap px.
"""
import logging
import random
import time
from dataclasses import dataclass

import cv2
import numpy as np

from lumberjack.core import regions as R
from lumberjack.ui import mouseover
from lumberjack.vision import minimap as MM

log = logging.getLogger("npcs")

# ---- minimap dots ----------------------------------------------------------------------
DOT_MIN_AREA, DOT_MAX_AREA = 4, 40       # one dot = 12 px (4x4 rounded); two touching ~24
OWN_MARKER_R = 3                         # ignore our own marker in the centre
# one capture, 4 players matched to their white dots: x ~9-10, y ~9-13 (perspective)
SCREEN_PER_MM_X = 9.5                    # CALIBRATE: screen px per minimap px, horizontally
SCREEN_PER_MM_Y = 10.0                   # CALIBRATE: ... vertically
PLAYER_TILE = (258, 186)                 # CALIBRATE: screen point of the tile we stand on
AIM_UP = 14                              # NPC bodies stand up from their tile: aim a bit higher
VIEW_MARGIN = 12                         # don't hover right at the edge of the game view
MAX_WALK_MM = 46                         # minimap clicks stay well inside the disc (walker: 48)


@dataclass
class Candidate:
    x: int                  # screen point to hover first (canvas coords)
    y: int
    dist: float             # screen distance from our character
    mm: tuple = None        # (dx, dy) minimap offset of the dot it came from, if any
    source: str = "minimap"

    def probe_points(self):
        """Points to hover, best guess first (the projection is only approximate)."""
        pts = [(0, 0), (0, -12), (-10, -6), (10, -6), (0, 10), (0, -24)]
        return [(self.x + dx + random.randint(-2, 2), self.y + dy + random.randint(-2, 2))
                for dx, dy in pts]


def minimap_dots(frame):
    """(dx, dy) offsets of the yellow NPC dots from our marker, in minimap px, nearest first."""
    cx, cy = MM.CENTER
    r = MM.R_USE
    img = frame[cy - r:cy + r + 1, cx - r:cx + r + 1].astype(np.int16)
    b, g, rr = img[..., 0], img[..., 1], img[..., 2]
    yellow = ((rr > 190) & (g > 190) & (b < 110)).astype(np.uint8)
    yellow[MM._circle == 0] = 0
    n, _, stats, cents = cv2.connectedComponentsWithStats(yellow)
    out = []
    for i in range(1, n):
        if not (DOT_MIN_AREA <= stats[i][4] <= DOT_MAX_AREA):
            continue
        dx, dy = float(cents[i][0] - r), float(cents[i][1] - r)
        if abs(dx) <= OWN_MARKER_R and abs(dy) <= OWN_MARKER_R:
            continue
        out.append((dx, dy))
    out.sort(key=lambda d: d[0] * d[0] + d[1] * d[1])
    return out


def project(dx, dy):
    """Minimap offset -> screen point (the NPC's body, not its feet)."""
    px, py = PLAYER_TILE
    return int(round(px + dx * SCREEN_PER_MM_X)), int(round(py + dy * SCREEN_PER_MM_Y - AIM_UP))


def in_view(x, y, margin=VIEW_MARGIN):
    v = R.VIEWPORT
    if not (v.x + margin <= x < v.x + v.w - margin and v.y + margin <= y < v.y + v.h - margin):
        return False
    return not R.MOUSEOVER_TEXT.contains(x, y) and y > R.MOUSEOVER_TEXT.y + R.MOUSEOVER_TEXT.h + 4


def _player_dist(x, y):
    px, py = R.PLAYER.center
    return float(np.hypot(x - px, y - py))


# ---- motion blobs (optional extra candidates) -------------------------------------------
MOTION_DIFF = 28                # per-pixel grey difference that counts as "moved"
MOTION_MIN_AREA, MOTION_MAX_AREA = 40, 2500


def motion_blobs(prev, cur):
    """Centres of things that changed between two frames (walking NPCs), excluding our own
    character and the hover text. Water/fire flicker can show up too - hover verifies."""
    if prev is None or prev.shape != cur.shape:
        return []
    a = cv2.cvtColor(R.VIEWPORT.crop(prev), cv2.COLOR_BGR2GRAY)
    b = cv2.cvtColor(R.VIEWPORT.crop(cur), cv2.COLOR_BGR2GRAY)
    m = (cv2.absdiff(a, b) > MOTION_DIFF).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        if not (MOTION_MIN_AREA <= stats[i][4] <= MOTION_MAX_AREA):
            continue
        x, y = int(cents[i][0]) + R.VIEWPORT.x, int(cents[i][1]) + R.VIEWPORT.y
        if R.PLAYER.contains(x, y) or not in_view(x, y):
            continue
        out.append((x, y))
    return out


def find_npcs(frame, prev=None):
    """NPC candidates inside the game view, nearest to our character first.
    `prev` (an earlier frame) adds motion blobs that aren't near a minimap candidate."""
    out = []
    for dx, dy in minimap_dots(frame):
        x, y = project(dx, dy)
        if in_view(x, y):
            out.append(Candidate(x, y, _player_dist(x, y), (dx, dy), "minimap"))
    for x, y in motion_blobs(prev, frame):
        if all(abs(x - c.x) > 25 or abs(y - c.y) > 25 for c in out):
            out.append(Candidate(x, y, _player_dist(x, y), None, "motion"))
    out.sort(key=lambda c: c.dist)
    return out


def far_dots(frame):
    """Minimap offsets of NPCs outside the game view, nearest first (to walk toward)."""
    return [d for d in minimap_dots(frame) if not in_view(*project(*d))]


def minimap_click_point(dx, dy, max_r=MAX_WALK_MM):
    """Canvas point on the minimap for walking toward offset (dx, dy) (clamped inside)."""
    r = float(np.hypot(dx, dy))
    if r > max_r:
        dx, dy = dx * max_r / r, dy * max_r / r
    return int(round(MM.CENTER[0] + dx)), int(round(MM.CENTER[1] + dy))


# ---- hover text -------------------------------------------------------------------------
_K = np.ones((3, 3), np.uint8)
NAME_MIN_RECALL, NAME_MIN_PRECISION = 0.93, 0.90
LEFT_TOL = 2          # the name must start within this many px of the first yellow column
GAP_AFTER = 2         # ... and be followed by this many empty columns (a space)


def name_match(mask, tmpl, min_recall=NAME_MIN_RECALL, min_precision=NAME_MIN_PRECISION):
    """Does the coloured-text mask start with the word `tmpl` (a trimmed binary template),
    allowing anything after a space (e.g. "(level-3)")? 1px tolerant, like mouseover."""
    m = (mask > 0).astype(np.uint8)
    t = (tmpl > 0).astype(np.uint8)
    th, tw = t.shape
    if m.shape[0] < th + 2 or m.shape[1] < tw + 2 or not m.any() or not t.any():
        return False
    run = mouseover._name_run(m * 255)
    if run is None:
        return False
    m_fat = cv2.dilate(m, _K).astype(np.float32)
    res = cv2.matchTemplate(m_fat, t.astype(np.float32), cv2.TM_CCORR)
    # only placements where the name starts where the coloured text starts: a dense
    # suffix would otherwise "contain" any word under the 1px tolerance
    lo, hi = max(run[0] - LEFT_TOL, 0), min(run[0] + LEFT_TOL + 1, res.shape[1])
    if lo >= hi:
        return False
    _, best, _, (dx, y) = cv2.minMaxLoc(res[:, lo:hi])
    x = lo + dx
    if best / t.sum() < min_recall:
        return False
    t_fat = cv2.dilate(np.pad(t, 1), _K)
    x0, y0 = max(x - 1, 0), max(y - 1, 0)
    win = m[y0:y0 + t_fat.shape[0], x0:x0 + t_fat.shape[1]]
    tf = t_fat[:win.shape[0], :win.shape[1]]
    if float((win & tf).sum()) / max(float(win.sum()), 1.0) < min_precision:
        return False
    after = m[:, x + tw + 1:x + tw + 1 + GAP_AFTER]
    return not after.any()      # the word ends here (space or end of text)


def hover_target(frame, targets, action="attack"):
    """Which of `targets` (yellow NPC-name templates) the hover text names, with `action`
    as the white verb ("Attack Chicken (level-1)" -> "chicken"). None if no match."""
    if not mouseover.available(action):
        return None
    strip = R.MOUSEOVER_TEXT.crop(frame)
    if not mouseover._ok(mouseover.strip_score(strip, action, "white")):
        return None
    ym = mouseover.strip_mask(strip, "yellow")
    if not ym.any():
        return None
    for t in targets:
        if mouseover.available(t) and any(name_match(ym, tpl) for tpl in mouseover.templates(t)):
            return t
    return None


# ---- calibration helpers ----------------------------------------------------------------
def split_words(mask, gap=3):
    """[(x0, x1)] column runs of a text mask separated by >= `gap` empty columns."""
    cols = np.where(mask.any(axis=0))[0]
    if not len(cols):
        return []
    runs, s, e = [], cols[0], cols[0]
    for c in cols[1:]:
        if c - e > gap:
            runs.append((int(s), int(e)))
            s = c
        e = c
    runs.append((int(s), int(e)))
    return runs


def name_columns(mask, words=None):
    """(x0, x1) of the NPC name in a yellow mask, without the "(level-N)" suffix.
    words=N keeps the first N words; otherwise we stop at the first word that reaches
    lower than the first word's bottom row AND starts at/above its top - the "(" paren."""
    runs = [r for r in split_words(mask) if mask[:, r[0]:r[1] + 1].sum() >= 6]
    if not runs:
        return None
    if words:
        keep = runs[:words]
    else:
        def rows(r):
            ys = np.where(mask[:, r[0]:r[1] + 1].any(axis=1))[0]
            return ys.min(), ys.max()
        top0, bot0 = rows(runs[0])
        keep = [runs[0]]
        for r in runs[1:]:
            # the paren's first glyph is the tallest thing on the line
            first = mask[:, r[0]:r[0] + 2]
            ys = np.where(first.any(axis=1))[0]
            if len(ys) and ys.max() > bot0 and ys.min() <= top0:
                break
            keep.append(r)
    return keep[0][0], keep[-1][1]


def save_npc_name(strip, name, words=None, variant=False):
    """Save the yellow NPC name in a hover strip as template `name` (e.g. 'chicken').
    variant=True adds it as name.vN.png instead (another sample of a known name)."""
    col = mouseover.strip_mask(strip, "yellow")
    span = name_columns(col, words)
    if span is None:
        raise ValueError("no yellow text in strip")
    word = mouseover._trim(col[:, span[0]:span[1] + 1])
    mouseover.TEMPLATES.mkdir(parents=True, exist_ok=True)
    if variant and mouseover.available(name):
        n = len(list(mouseover.TEMPLATES.glob(f"{name}.v*.png"))) + 1
        path = mouseover.TEMPLATES / f"{name}.v{n}.png"
    else:
        path = mouseover.TEMPLATES / f"{name}.png"
    cv2.imwrite(str(path), word)
    mouseover._cache.pop(name, None)
    mouseover._variants_cache.pop(name, None)
    return word.shape


def save_action(strip, action="attack"):
    """Save the white verb before the yellow name (e.g. 'Attack') as template `action`."""
    white, col = mouseover.strip_mask(strip, "white"), mouseover.strip_mask(strip, "yellow")
    cols = np.where(col.any(axis=0))[0]
    if not len(cols):
        raise ValueError("no yellow text in strip")
    word = mouseover._trim(white[:, :cols.min()])
    if word is None:
        raise ValueError("no white action text before the name")
    cv2.imwrite(str(mouseover.TEMPLATES / f"{action}.png"), word)
    mouseover._cache.pop(action, None)
    mouseover._variants_cache.pop(action, None)
    return word.shape


def calibrate_npc(ctx, name, action="attack", words=None):
    """USER-INVOKED ONLY (moves the mouse): hover NPC candidates until the hover text shows
    a yellow name after `action`-like white text, then save the name (and the action
    template if we don't have it yet). Stand next to the NPC with the camera reset."""
    frame = ctx.grab()
    for c in find_npcs(frame):
        for px, py in c.probe_points():
            ctx.inp.move(px, py)
            time.sleep(0.25)
            strip = R.MOUSEOVER_TEXT.crop(ctx.grab())
            if not mouseover.strip_mask(strip, "yellow").any():
                continue
            if mouseover.available(action):
                if not mouseover._ok(mouseover.strip_score(strip, action, "white")):
                    continue
            else:
                save_action(strip, action)
            shape = save_npc_name(strip, name, words)
            log.info("Learned NPC name '%s' %s at (%d, %d)", name, shape, px, py)
            return True
    log.warning("No NPC with a yellow name in view - stand next to a %s and try again", name)
    return False


def draw(frame, cands=None):
    img = frame.copy()
    cands = find_npcs(frame) if cands is None else cands
    for k, c in enumerate(cands):
        color = (0, 255, 255) if c.source == "minimap" else (255, 128, 0)
        cv2.drawMarker(img, (c.x, c.y), color, cv2.MARKER_TILTED_CROSS, 10, 2)
        cv2.putText(img, str(k), (c.x + 6, c.y - 6), cv2.FONT_HERSHEY_PLAIN, 1, color, 1)
    return img
