"""Read skill levels from the Stats tab.

Hovering a skill shows a yellow tooltip:  "Woodcutting: 58/58" / "Current Xp: 233,906" / ...
We find the tooltip, split its first line into glyphs and read the "current/base" numbers
with digit templates learned from a tooltip whose numbers were known.
"""
from pathlib import Path

import cv2
import numpy as np

DIGITS = Path(__file__).resolve().parents[1] / "assets" / "templates" / "digits"

# Stats tab, fixed mode: 3 columns x 7 visible rows (canvas coords of each cell's centre)
COLS = (578, 632, 686)
ROWS = (241, 273, 304, 336, 368, 399, 431)
LAYOUT = [
    ("attack", "hitpoints", "mining"),
    ("strength", "agility", "smithing"),
    ("defence", "herblore", "fishing"),
    ("ranged", "thieving", "cooking"),
    ("prayer", "crafting", "firemaking"),
    ("magic", "fletching", "woodcutting"),
    ("runecrafting", "slayer", "farming"),
]
CELL = {name: (COLS[c], ROWS[r]) for r, row in enumerate(LAYOUT) for c, name in enumerate(row)}


def tooltip_box(frame, near):
    """Bounding box (x, y, w, h) of the pale-yellow tooltip near the mouse, or None."""
    b, g, r = (frame[..., k].astype(np.int16) for k in range(3))
    yel = ((r > 220) & (g > 220) & (b > 120) & (b < 200)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(yel)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w > 60 and h > 30 and area > 0.6 * w * h:
            d = abs(x + w / 2 - near[0]) + abs(y + h / 2 - near[1])
            if best is None or d < best[0]:
                best = (d, (int(x), int(y), int(w), int(h)))
    return best[1] if best else None


def glyphs(line):
    """Split a binary text line (dark pixels = True) into (x0, x1, bitmap) glyphs."""
    cols = line.any(axis=0)
    out, x = [], 0
    while x < len(cols):
        if cols[x]:
            x0 = x
            while x < len(cols) and cols[x]:
                x += 1
            g = line[:, x0:x]
            ys = np.where(g.any(axis=1))[0]
            out.append((x0, x, g[ys.min():ys.max() + 1]))
        x += 1
    return out


def first_line(frame, box):
    x, y, w, h = box
    t = frame[y:y + h, x:x + w]
    dark = t.max(axis=2) < 90
    rows = np.where(dark.any(axis=1))[0]
    if not len(rows):
        return None
    # first text line = first run of dark rows
    end = rows[0]
    for r in rows[1:]:
        if r - end > 1:
            break
        end = r
    return dark[rows[0]:end + 1]


_tmpl = None


def _templates():
    global _tmpl
    if _tmpl is None:
        _tmpl = {}
        for p in DIGITS.glob("*.png"):
            _tmpl[p.stem] = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) > 0
    return _tmpl


def _classify(g):
    """Best-matching template name for one glyph bitmap, or None."""
    best = (0.0, None)
    for name, t in _templates().items():
        if abs(t.shape[0] - g.shape[0]) > 1 or abs(t.shape[1] - g.shape[1]) > 1:
            continue
        h, w = max(t.shape[0], g.shape[0]), max(t.shape[1], g.shape[1])
        a = np.zeros((h, w), bool)
        b = np.zeros((h, w), bool)
        a[:t.shape[0], :t.shape[1]] = t
        b[:g.shape[0], :g.shape[1]] = g
        iou = (a & b).sum() / max((a | b).sum(), 1)
        if iou > best[0]:
            best = (iou, name)
    return best[1] if best[0] >= 0.8 else None


def read_line_numbers(line):
    """Text of the trailing number part of a tooltip line, e.g. '58/58'."""
    out = []
    for _, _, g in reversed(glyphs(line)):
        c = _classify(g)
        if c is None:
            break
        out.append({"slash": "/", "comma": ","}.get(c, c))
    return "".join(reversed(out))


def text_lines(frame, box):
    """Binary bitmaps of each text line in the tooltip, top to bottom."""
    x, y, w, h = box
    dark = frame[y:y + h, x:x + w].max(axis=2) < 90
    rows = np.where(dark.any(axis=1))[0]
    lines, start, end = [], None, None
    for r in rows:
        if start is None:
            start = end = r
        elif r - end > 1:
            lines.append(dark[start:end + 1])
            start = end = r
        else:
            end = r
    if start is not None:
        lines.append(dark[start:end + 1])
    return lines


def _num(text):
    t = text.replace(",", "")
    return int(t) if t.isdigit() else None


def read_skill(ctx, skill):
    """{'level', 'xp', 'next', 'remainder'} from a skill's tooltip, or None on failure.
    Tooltip lines: "Woodcutting: 58/58", "Current Xp: 233,906", "Next level: 247,886",
    "Remainder: 13,980" (at 99 the last two may be missing).
    From the game's own data when readable - no tab opening or hovering."""
    from lumberjack import actions
    from lumberjack.core import gamestate
    g = gamestate.skill(skill)
    if g is not None:
        return {k: g[k] for k in ("level", "xp", "next", "remainder")}
    actions.open_tab(ctx, "stats")
    x, y = CELL[skill]
    ctx.inp.move(x, y)
    ctx.sleep(0.7)
    frame = ctx.grab()
    box = tooltip_box(frame, (x, y))
    out = None
    if box:
        texts = [read_line_numbers(line) for line in text_lines(frame, box)]
        if texts and "/" in texts[0]:
            cur = _num(texts[0].split("/", 1)[0])
            if cur is not None:
                out = {"level": cur,
                       "xp": _num(texts[1]) if len(texts) > 1 else None,
                       "next": _num(texts[2]) if len(texts) > 2 else None,
                       "remainder": _num(texts[3]) if len(texts) > 3 else None}
    ctx.inp.move(300, 250)
    actions.open_tab(ctx, "inventory")
    return out


def read_level(ctx, skill):
    """(current, base) level of a skill - from the game's own data, else read from its
    Stats-tab tooltip. None on failure."""
    from lumberjack import actions
    from lumberjack.core import gamestate
    g = gamestate.skill(skill)
    if g is not None:
        return g["level"], g["base"]
    actions.open_tab(ctx, "stats")
    x, y = CELL[skill]
    ctx.inp.move(x, y)
    ctx.sleep(0.7)
    frame = ctx.grab()
    box = tooltip_box(frame, (x, y))
    result = None
    if box:
        line = first_line(frame, box)
        text = read_line_numbers(line) if line is not None else ""
        if "/" in text:
            cur, base = text.split("/", 1)
            if cur.isdigit() and base.isdigit():
                result = (int(cur), int(base))
    ctx.inp.move(300, 250)
    actions.open_tab(ctx, "inventory")
    return result


def read_levels(ctx, skills):
    return {s: read_level(ctx, s) for s in skills}
