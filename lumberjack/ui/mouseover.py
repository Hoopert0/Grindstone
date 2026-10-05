"""Read the top-left hover text ("Chop down Tree / 2 more options").

The client draws it in a fixed bitmap font: actions in white, object names in cyan, NPCs
in yellow, items in orange - every glyph with a 1px black drop shadow below-right.

The scene shows through around (and slightly into) the letters, so plain colour
thresholds break whenever the top-left of the view is bright or busy. Instead a pixel
counts as "letter" when it is bright AND much brighter than the pixel diagonally
below-right of it (its shadow) AND has the right hue. That edge exists on any
background. Words are then template-matched against masks captured from the game.
"""
from pathlib import Path

import cv2
import numpy as np

from lumberjack.core import regions as R

TEMPLATES = Path(__file__).resolve().parents[1] / "assets" / "templates" / "mouseover"

MIN_V = 130          # letter pixels are bright...
MIN_SHADOW_GAP = 45  # ...and this much brighter than their shadow pixel


def _hue_ok(h, s, color):
    if color == "white":
        return s < 70
    if color == "cyan":
        return (h >= 78) & (h <= 102) & (s > 70)
    if color == "yellow":
        return (h >= 22) & (h <= 36) & (s > 70)
    if color == "orange":
        return (h >= 6) & (h <= 22) & (s > 70)
    if color == "green":  # spell names
        return (h >= 45) & (h <= 75) & (s > 70)
    raise ValueError(color)


def strip_mask(strip, color):
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV).astype(np.int16)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    shadow = np.zeros_like(v)
    shadow[:-1, :-1] = v[1:, 1:]
    lit = (v > MIN_V) & (v - shadow > MIN_SHADOW_GAP)
    return ((lit & _hue_ok(h, s, color)) * 255).astype(np.uint8)


def mask(frame, color):
    return strip_mask(R.MOUSEOVER_TEXT.crop(frame), color)


_cache = {}


def available(name):
    return name in _cache or (TEMPLATES / f"{name}.png").exists()


_variants_cache = {}


def templates(name):
    """All saved samples of a word: name.png plus name.v1.png, name.v2.png, ..."""
    if name not in _variants_cache:
        out = [template(name)]
        for p in sorted(TEMPLATES.glob(f"{name}.v*.png")):
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                out.append(img)
        _variants_cache[name] = out
    return _variants_cache[name]


VARIANT_WIDTH_TOL = 6   # px: another sample of the same word is about as wide as the first


def add_variant(strip, name, color, any_width=False):
    """Save another sample of a coloured word (e.g. the same item name over a different
    background) so matching tolerates both. A sample much wider or narrower than the saved
    ones is a different word ("Logs" isn't "Raw shrimps") and is refused - unless
    any_width (a catch-all name like 'raw_fish' collects different words on purpose)."""
    col = strip_mask(strip, color)
    run = _name_run(col)
    if run is None:
        raise ValueError("no coloured text in strip")
    word = _trim(col[:, run[0]:run[1] + 1])
    if word.shape[1] < 8 or word.shape[0] < 6:
        raise ValueError(f"coloured text too small to be a word {word.shape}")
    known = templates(name) if available(name) else []
    if known and not any_width and min(abs(t.shape[1] - word.shape[1]) for t in known) > VARIANT_WIDTH_TOL:
        raise ValueError(f"that word is {word.shape[1]} px wide, '{name}' is "
                         f"{', '.join(str(t.shape[1]) for t in known)} px - a different item?")
    n = len(list(TEMPLATES.glob(f"{name}.v*.png"))) + 1
    cv2.imwrite(str(TEMPLATES / f"{name}.v{n}.png"), word)   # (no "~": file copies skip "~<digit>" names)
    _variants_cache.pop(name, None)
    return word.shape


def template(name):
    if name not in _cache:
        path = TEMPLATES / f"{name}.png"
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) if path.exists() else None
        if img is None:
            raise FileNotFoundError(f"missing mouseover template '{name}' in {TEMPLATES}")
        _cache[name] = img
    return _cache[name]


def which(frame, action, targets, target_color="cyan"):
    """First of `targets` the hover text matches (e.g. 'Chop down Oak' -> 'oak'), else None."""
    strip = R.MOUSEOVER_TEXT.crop(frame)
    if not action_ok(strip, action):
        return None
    for t in targets:
        if available(t) and _target_ok(strip, t, target_color):
            return t
    return None


_K = np.ones((3, 3), np.uint8)


def _score(m, t):
    """(recall, precision) of the best placement of template t in mask m, 1px tolerant.

    recall    = share of the template's letter pixels that have a mask pixel within 1px
    precision = share of mask pixels in that window that lie within 1px of a letter pixel

    The tolerance matters: glyph positions round differently depending on where the text
    starts, so parts of a word shift by a pixel between frames.
    """
    m = (m > 0).astype(np.uint8)
    t = (t > 0).astype(np.uint8)
    if m.shape[0] < t.shape[0] + 2 or m.shape[1] < t.shape[1] + 2 or not m.any():
        return 0.0, 0.0
    m_fat = cv2.dilate(m, _K).astype(np.float32)
    recall_map = cv2.matchTemplate(m_fat, t.astype(np.float32), cv2.TM_CCORR)
    _, best, _, (x, y) = cv2.minMaxLoc(recall_map)
    recall = best / t.sum()

    t_fat = cv2.dilate(np.pad(t, 1), _K)               # (h+2, w+2), centred on the placement
    x0, y0 = max(x - 1, 0), max(y - 1, 0)
    win = m[y0:y0 + t_fat.shape[0], x0:x0 + t_fat.shape[1]]
    tf = t_fat[: win.shape[0], : win.shape[1]]
    precision = float((win & tf).sum()) / max(float(win.sum()), 1.0)
    return recall, precision


def strip_score(strip, name, color):
    return _score(strip_mask(strip, color), template(name))


def word_score(frame, name, color):
    return strip_score(R.MOUSEOVER_TEXT.crop(frame), name, color)


# True matches score 1.00/1.00 on all 77 collected strips; the nearest impostors are
# "Inspect" vs "Chop down" (0.81/0.87) and "Oak" vs "Tree" (0.83/0.77).
def _ok(score, min_recall=0.93, min_precision=0.90):
    return score[0] >= min_recall and score[1] >= min_precision


def _name_run(mask):
    """(first, last) column of the coloured name. Coloured columns are grouped into runs
    (split at gaps > 10 px, so word gaps like "Bank booth" stay joined); runs with only a
    few pixels are stray background specks and are dropped; the name spans the rest."""
    colsum = (mask > 0).sum(axis=0)
    cols = np.where(colsum > 0)[0]
    if not len(cols):
        return None
    runs, start, end = [], cols[0], cols[0]
    for c in cols[1:]:
        if c - end > 10:
            runs.append((start, end))
            start = c
        end = c
    runs.append((start, end))
    real = [r for r in runs if colsum[r[0]:r[1] + 1].sum() >= 8]
    if not real:
        return None
    return int(real[0][0]), int(real[-1][1])


def _name_width(strip, color):
    """Pixel width of the whole coloured name (e.g. "Tree", "Willow", "Tree patch")."""
    run = _name_run(strip_mask(strip, color))
    return run[1] - run[0] + 1 if run else 0


def _target_ok(strip, target, color):
    """Any saved sample of `target` matches - and has the same overall width. (The 1px
    tolerance would otherwise let a short word fit inside a longer one: "Yew" in "Tree".)"""
    m = strip_mask(strip, color)
    run = _name_run(m)
    if not run:
        return False
    start, width = run[0], run[1] - run[0] + 1
    for t in templates(target):
        # roughly the same width (a background speck can add a few px)...
        if not (width - 8 <= t.shape[1] <= width + 2):
            continue
        if not _ok(_score(m, t)):
            continue
        # ...and the word must sit at the START of the name, so "Yew" can't hide in "Tree"
        if abs(_best_x(m, t) - start) <= 2:
            return True
    return False


def _best_x(m, t):
    """Left x of the best (1px-tolerant) placement of template t in mask m."""
    m_fat = cv2.dilate((m > 0).astype(np.uint8), _K).astype(np.float32)
    res = cv2.matchTemplate(m_fat, (t > 0).astype(np.float32), cv2.TM_CCORR)
    return cv2.minMaxLoc(res)[3][0]


def action_ok(strip, action):
    """The hover's FIRST word is `action` (e.g. "Use"). Matching anywhere in the line let
    "Use" be found inside "...3 more options" on "Wield Rune axe" - so it must sit at the
    start of the white text."""
    m = strip_mask(strip, "white")
    t = template(action)
    if not _ok(_score(m, t)):
        return False
    run = _name_run(m)            # first real run of white text (stray background specks ignored)
    # a few px of slack: a white sliver at the strip's edge can join onto the text run; the
    # bogus "Use" inside "...more options" sat 40+ px in, far beyond this
    return run is not None and -2 <= _best_x(m, t) - run[0] <= 8


def strip_is_text(strip, action, target, target_color="cyan"):
    if not action_ok(strip, action):
        return False
    return _target_ok(strip, target, target_color)


def is_text(frame, action, target, target_color="cyan"):
    """e.g. is_text(frame, 'chop_down', 'tree') -> hovering 'Chop down Tree'?"""
    return strip_is_text(R.MOUSEOVER_TEXT.crop(frame), action, target, target_color)


def _trim(m):
    ys, xs = np.where(m > 0)
    if not len(xs):
        return None
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def save_word_templates(frame, action_name, target_name, target_color="cyan", strip=None):
    """Capture templates from a frame (or strip) where the hover text is showing.

    The action is the white text *before* the target word; the target is the coloured word.
    """
    strip = R.MOUSEOVER_TEXT.crop(frame) if strip is None else strip
    TEMPLATES.mkdir(parents=True, exist_ok=True)
    white, col = strip_mask(strip, "white"), strip_mask(strip, target_color)
    cols = np.where(col.any(axis=0))[0]
    if not len(cols):
        raise ValueError(f"no {target_color} text in hover strip")
    start, end = cols.min(), cols.max()
    action = _trim(white[:, :start])
    target = _trim(col[:, start:end + 1])
    cv2.imwrite(str(TEMPLATES / f"{action_name}.png"), action)
    cv2.imwrite(str(TEMPLATES / f"{target_name}.png"), target)
    for n in (action_name, target_name):
        _cache.pop(n, None)
        _variants_cache.pop(n, None)
    return action.shape, target.shape
