"""Explore around here (square spiral) while recording a map, and catalogue every tree
name seen on the way. Unknown names are saved as strips for checking.

    python -m lumberjack.tools.explore_names draynor --rings 2
"""
import argparse
import json
import time
from pathlib import Path

import cv2

from lumberjack.core import regions as R
from lumberjack.core.input import AgentInput
from lumberjack.core.window import GameWindow
from lumberjack.nav.localizer import Localizer
from lumberjack.nav.worldmap import WorldMap
from lumberjack.tools.calibrate_name import KNOWN, unknown_name
from lumberjack.tools.record_map import spiral_legs
from lumberjack.ui import mouseover as M
from lumberjack.vision import minimap as MM
from lumberjack.vision import trees

OUT = Path(__file__).resolve().parents[1] / "captures" / "names"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--rings", type=int, default=2)
    ap.add_argument("--step", type=int, default=40)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    win, inp = GameWindow(), AgentInput()
    wm = WorldMap(args.map)
    pos = None
    while pos is None:
        pos = wm.add(win.grab())
    seen = []

    def record_until_still(timeout=12):
        nonlocal pos
        end, last_move = time.time() + timeout, time.time()
        while time.time() < end:
            p = wm.add(win.grab())
            if p and p != pos:
                pos, last_move = p, time.time()
            if time.time() - last_move > 1.6:
                return
            time.sleep(0.15)

    def catalogue():
        frame = win.grab()
        for c in trees.find_trees(frame)[:8]:
            inp.move(c.x, c.y)
            time.sleep(0.25)
            f = win.grab()
            strip = R.MOUSEOVER_TEXT.crop(f)
            name = M.which(f, "chop_down", KNOWN)
            if name is None and unknown_name(strip, "chop_down", "cyan"):
                name = f"unknown{len(seen)}"
                cv2.imwrite(str(OUT / f"{name}.png"), cv2.resize(strip, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
            if name:
                seen.append({"name": name, "map": [int(pos[0]), int(pos[1])], "screen": [c.x, c.y]})
                print(f"  {name} at screen ({c.x},{c.y}) while at map {pos}", flush=True)
        inp.move(260, 300)

    catalogue()
    for k, (dx, dy) in enumerate(spiral_legs(args.rings, args.step)):
        remaining = [dx, dy]
        while remaining[0] or remaining[1]:
            sx = max(-50, min(50, remaining[0]))
            sy = max(-50, min(50, remaining[1]))
            remaining = [remaining[0] - sx, remaining[1] - sy]
            a = MM.heading(win.grab()) or 0.0
            cx, cy = Localizer.map_to_minimap(sx, sy, a)
            inp.click(MM.CENTER[0] + cx, MM.CENTER[1] + cy)
            record_until_still()
        print(f"leg {k + 1} - at {pos}", flush=True)
        catalogue()
    wm.save()
    (OUT / f"{args.map}_seen.json").write_text(json.dumps(seen, indent=1))
    inp.close()
    print("done", len(seen), "trees seen")


if __name__ == "__main__":
    main()
