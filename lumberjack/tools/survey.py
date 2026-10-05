"""Survey nearby trees: find tree-ish blobs with a loose colour mask, hover each one,
read its name from the hover text, and record colour/shape stats per tree type.

    python -m lumberjack.tools.survey [--turns 4]

Output: captures/survey/samples.json (+ a crop per blob) - used to tune per-type
detection profiles in vision/trees.py. Stop the bot first (it holds the input agent).
"""
import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from lumberjack.core import regions as R
from lumberjack.core.input import VK_LEFT, VK_UP, AgentInput
from lumberjack.core.window import GameWindow
from lumberjack.ui import mouseover

OUT = Path(__file__).resolve().parents[1] / "captures" / "survey"
NAMES = ["tree", "oak", "willow", "maple", "yew", "magic"]

LOOSE_LO = np.array([22, 50, 20])
LOOSE_HI = np.array([55, 215, 190])


def loose_blobs(frame):
    vp = R.VIEWPORT.crop(frame)
    hsv = cv2.cvtColor(vp, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, LOOSE_LO, LOOSE_HI)
    # grass is flat-shaded at S~203; drop pixels that look like plain grass
    grass = (hsv[..., 1] >= 196) & (hsv[..., 1] <= 210)
    m[grass] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 500 or w < 20 or h < 20 or area / (w * h) < 0.3:
            continue
        px = hsv[labels == i]
        out.append({
            "cx": int(cents[i][0]) + R.VIEWPORT.x, "cy": int(cents[i][1]) + R.VIEWPORT.y,
            "bbox": [int(x) + R.VIEWPORT.x, int(y) + R.VIEWPORT.y, int(w), int(h)],
            "area": int(area), "fill": round(area / (w * h), 3),
            "h": [float(np.percentile(px[:, 0], p)) for p in (5, 50, 95)],
            "s": [float(np.percentile(px[:, 1], p)) for p in (5, 50, 95)],
            "v": [float(np.percentile(px[:, 2], p)) for p in (5, 50, 95)],
        })
    return out


def name_at(win, inp, x, y):
    inp.move(x, y)
    time.sleep(0.2)
    f = win.grab()
    for n in NAMES:
        try:
            if mouseover.is_text(f, "chop_down", n):
                return n
        except FileNotFoundError:
            continue  # no template for this tree type yet
    # still record what the cyan word looked like, to calibrate new types later
    return "other" if mouseover.mask(f, "cyan").any() else "none"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=4)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    win, inp = GameWindow(), AgentInput()
    inp.click(*R.COMPASS_CENTER)
    time.sleep(0.4)
    inp.hold_key(VK_UP, 2.0)
    samples = []
    for turn in range(args.turns):
        frame = win.grab().copy()
        blobs = loose_blobs(frame)
        print(f"view {turn + 1}: {len(blobs)} candidate blobs")
        for b in blobs:
            x, y, w, h = b["bbox"]
            # try the centroid and the upper-middle of the blob
            for px, py in [(b["cx"], b["cy"]), (x + w // 2, y + h // 3)]:
                b["label"] = name_at(win, inp, px, py)
                if b["label"] not in ("none", "other"):
                    break
            b["view"] = turn
            idx = len(samples)
            cv2.imwrite(str(OUT / f"{idx:03d}_{b['label']}.png"), frame[y:y + h, x:x + w])
            samples.append(b)
            print(f"  ({b['cx']},{b['cy']}) {b['label']:6s} area={b['area']} fill={b['fill']} "
                  f"H={b['h'][1]:.0f} S={b['s'][0]:.0f}-{b['s'][2]:.0f} V={b['v'][0]:.0f}-{b['v'][2]:.0f}")
        inp.move(260, 300)
        inp.hold_key(VK_LEFT, 0.8)
        time.sleep(0.4)
    (OUT / "samples.json").write_text(json.dumps(samples, indent=1))
    by = {}
    for s in samples:
        by.setdefault(s["label"], []).append(s)
    print({k: len(v) for k, v in by.items()})


if __name__ == "__main__":
    main()
