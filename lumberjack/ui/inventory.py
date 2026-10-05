"""Inventory state: which of the 28 slots hold an item.

Compares each slot against a reference image of the *empty* backpack, captured once
with `save_empty_reference()` while the inventory tab is open and empty.
"""
from pathlib import Path

import cv2
import numpy as np

from lumberjack.core import regions as R

REF = Path(__file__).resolve().parents[1] / "assets" / "templates" / "inventory_empty.png"
DIFF_THRESHOLD = 3.0    # empty slots match the reference pixel-perfectly (0.0); logs score ~17

_ref = None


def save_empty_reference(frame):
    REF.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(REF), R.SIDE_PANEL.crop(frame))


def _reference():
    global _ref
    if _ref is None:
        _ref = cv2.imread(str(REF))
        if _ref is None:
            raise FileNotFoundError("No empty-inventory reference; run save_empty_reference() with an empty backpack")
    return _ref


def slot_diffs(frame):
    panel, ref = R.SIDE_PANEL.crop(frame).astype(np.int16), _reference().astype(np.int16)
    out = []
    for s in R.INV_SLOTS:
        x, y = s.x - R.SIDE_PANEL.x, s.y - R.SIDE_PANEL.y
        a = panel[y:y + s.h, x:x + s.w]
        b = ref[y:y + s.h, x:x + s.w]
        out.append(float(np.abs(a - b).mean()))
    return out


def occupied(frame):
    """Which slots hold an item - from the game's own data when readable (exact, and no
    reference image needed), else by comparing the slots with the empty-backpack image."""
    from lumberjack.core import backpack
    occ = backpack.occupied()
    if occ is not None:
        return occ
    return [d > DIFF_THRESHOLD for d in slot_diffs(frame)]


def is_noted(frame, i):
    """A bank note: the slot is mostly a pale, low-saturation paper square (a real item
    is smaller and more saturated). Measured: note ~800 px at S~93; items S~170-230."""
    s = R.INV_SLOTS[i]
    x, y = s.x - R.SIDE_PANEL.x, s.y - R.SIDE_PANEL.y
    cur = R.SIDE_PANEL.crop(frame)[y:y + s.h, x:x + s.w]
    ref = _reference()[y:y + s.h, x:x + s.w]
    changed = np.abs(cur.astype(np.int16) - ref.astype(np.int16)).sum(axis=2) > 40
    if changed.sum() < 650:
        return False
    hsv = cv2.cvtColor(cur, cv2.COLOR_BGR2HSV)
    return float(np.median(hsv[..., 1][changed])) < 130


def count(frame):
    return sum(occupied(frame))


def is_full(frame):
    return count(frame) >= 28
