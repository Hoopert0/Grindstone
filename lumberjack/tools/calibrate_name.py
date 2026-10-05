"""Teach the bot a new object name from the hover text (e.g. "Chop down Willow").

    python -m lumberjack.tools.calibrate_name willow
    python -m lumberjack.tools.calibrate_name willow --at 300,120   # hover one exact spot

Hovers tree candidates in view; the first hover whose text is "<action> <unknown cyan
name>" is saved as the template for NAME. The strip is kept in captures/ for checking.
"""
import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from lumberjack.core import regions as R
from lumberjack.core.input import AgentInput
from lumberjack.core.window import GameWindow
from lumberjack.tools.survey import loose_blobs
from lumberjack.ui import mouseover as M

KNOWN = ["tree", "oak", "willow", "maple", "yew", "magic"]
OUT = Path(__file__).resolve().parents[1] / "captures"


def unknown_name(strip, action, color):
    if not M._ok(M.strip_score(strip, action, "white")):
        return False
    if not M.strip_mask(strip, color).any():
        return False
    width = M._name_width(strip, color)
    for k in KNOWN:
        if M.available(k) and abs(width - M.template(k).shape[1]) <= 2 and M._ok(M.strip_score(strip, k, color)):
            return False  # it's a name we already know
    return True


def save_name(strip, name, color):
    col = M.strip_mask(strip, color)
    cols = np.where(col.any(axis=0))[0]
    word = M._trim(col[:, cols.min():cols.max() + 1])
    cv2.imwrite(str(M.TEMPLATES / f"{name}.png"), word)
    M._cache.pop(name, None)
    return word.shape


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--action", default="chop_down")
    ap.add_argument("--color", default="cyan")
    ap.add_argument("--at", default=None, help="x,y to hover instead of searching")
    args = ap.parse_args()

    win, inp = GameWindow(), AgentInput()
    frame = win.grab()
    if args.at:
        points = [tuple(int(v) for v in args.at.split(","))]
    else:
        points = []
        for b in loose_blobs(frame):
            x, y, w, h = b["bbox"]
            points += [(b["cx"], b["cy"]), (x + w // 2, y + h // 3)]
    for px, py in points:
        inp.move(px, py)
        time.sleep(0.25)
        strip = R.MOUSEOVER_TEXT.crop(win.grab())
        if unknown_name(strip, args.action, args.color):
            shape = save_name(strip, args.name, args.color)
            path = OUT / f"calibrated_{args.name}.png"
            cv2.imwrite(str(path), cv2.resize(strip, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST))
            print(f"saved '{args.name}' template {shape} from hover at ({px},{py}) - check {path.name}")
            return
    print("no unknown name found in view - stand closer to the object and try again")


if __name__ == "__main__":
    main()
