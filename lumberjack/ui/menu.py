"""The right-click "Choose Option" menu.

Layout (530 client): a 1px frame in the menu's body colour, a 19px black header, then one
15px row per option. Each row reads "<white action> <coloured target>", so we identify
rows by template-matching the white action word (e.g. "Drop").
"""
from pathlib import Path

import cv2
import numpy as np

from lumberjack.core.regions import Rect

TEMPLATES = Path(__file__).resolve().parents[1] / "assets" / "templates" / "menu"
BODY_BGR = np.array([71, 84, 93])
HEADER_H, ROW_H = 19, 15
WHITE_LO, WHITE_HI = np.array([220, 220, 220]), np.array([255, 255, 255])


def find(frame, near):
    """Locate an open menu around the click point `near`. Returns its Rect or None."""
    x, y = near
    x0, y0 = max(0, x - 260), max(0, y - 260)
    roi = frame[y0:y + 260, x0:x + 260]
    body = np.all(roi == BODY_BGR, axis=2).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(body)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        if bw < 40 or bh < HEADER_H + ROW_H:
            continue
        r = Rect(int(bx + x0), int(by + y0), int(bw), int(bh))
        if r.contains(x, y + 2) and (best is None or bw * bh > best.w * best.h):
            best = r
    return best


def rows(menu):
    """Rects of each option row."""
    n = (menu.h - HEADER_H - 2) // ROW_H
    return [Rect(menu.x + 2, menu.y + HEADER_H + i * ROW_H, menu.w - 4, ROW_H) for i in range(n)]


def _white(frame, row):
    return cv2.inRange(row.crop(frame), WHITE_LO, WHITE_HI)


def row_has_action(frame, row, action, threshold=0.92):
    t = cv2.imread(str(TEMPLATES / f"{action}.png"), cv2.IMREAD_GRAYSCALE)
    if t is None:
        raise FileNotFoundError(f"missing menu template '{action}'")
    m = _white(frame, row)
    if not m.any() or m.shape[0] < t.shape[0] or m.shape[1] < t.shape[1]:
        return False
    # the action is the first word, so only search the left part of the row
    m = m[:, : t.shape[1] + 12]
    if m.shape[1] < t.shape[1]:
        return False
    if cv2.matchTemplate(m, t, cv2.TM_CCORR_NORMED).max() < threshold:
        return False
    # same width too, so a shorter action can't pass for a longer one ("Make 1" vs "Make 10")
    return abs(_first_word_width(_white(frame, row)) - t.shape[1]) <= 2


def _first_word_width(m):
    cols = np.where(m.any(axis=0))[0]
    if not len(cols):
        return 0
    end = cols[0]
    for c in cols[1:]:
        if c - end > 3:
            break
        end = c
    return int(end - cols[0] + 1)


def find_option(frame, menu, action):
    for i, r in enumerate(rows(menu)):
        if row_has_action(frame, r, action):
            return i, r
    return None


def save_action_template(frame, row, action):
    """Save the first white word of a row (e.g. 'Drop') as a template."""
    m = _white(frame, row)
    cols = np.where(m.any(axis=0))[0]
    # first word = columns up to the first gap of >= 3 empty columns
    end = cols[0]
    for c in cols[1:]:
        if c - end > 3:
            break
        end = c
    ys = np.where(m[:, cols[0]:end + 1].any(axis=1))[0]
    word = m[ys.min():ys.max() + 1, cols[0]:end + 1]
    TEMPLATES.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(TEMPLATES / f"{action}.png"), word)
    return word.shape
