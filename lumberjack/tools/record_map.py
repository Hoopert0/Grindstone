"""Record a north-up stitched minimap map of an area.

    python -m lumberjack.tools.record_map lumbridge              # you walk; F12 to finish
    python -m lumberjack.tools.record_map lumbridge --explore 2  # bot walks a square spiral

Turning the camera while recording is fine - every capture is rotated to north-up using
the compass needle. Stop the woodcutting bot first.
"""
import argparse
import time

import win32api

from lumberjack.core.input import AgentInput
from lumberjack.core.window import GameWindow
from lumberjack.nav.localizer import Localizer
from lumberjack.nav.worldmap import MAPS, WorldMap
from lumberjack.vision import minimap as MM

F12 = 0x7B


def spiral_legs(rings, step):
    """Map-frame (north-up) legs of a square spiral: N, E, S S, W W, N N N, ..."""
    dirs = [(0, -1), (1, 0), (0, 1), (-1, 0)]
    legs, n = [], 1
    for _ in range(rings * 2):
        for _ in range(2):
            d = dirs[len(legs) % 4]
            legs.append((d[0] * step * n, d[1] * step * n))
        n += 1
    return legs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--explore", type=int, default=0, help="walk a square spiral with this many rings")
    ap.add_argument("--step", type=int, default=44, help="map px per spiral unit (~4 px/tile)")
    ap.add_argument("--extend", action="store_true", help="add to an existing map instead of starting fresh")
    args = ap.parse_args()

    win = GameWindow()
    inp = AgentInput() if args.explore else None  # manual mode: you drive, we only watch
    if inp:
        inp.move(260, 300)
    pos = None
    if args.extend:
        wm = WorldMap.load(args.name)
        pos = wm.resume_at(win.grab())
        if pos is None:
            raise SystemExit("Can't find you on the existing map - start inside the recorded area")
        print(f"extending '{args.name}' from {pos} ({len(wm.spots)} spots kept)")
    else:
        wm = WorldMap(args.name)
        while pos is None:
            pos = wm.add(win.grab())
        print("recording - start at", pos)

    def record_until_still(timeout):
        nonlocal pos
        end, last_move = time.time() + timeout, time.time()
        while time.time() < end:
            if win32api.GetAsyncKeyState(F12) & 0x8000:
                raise KeyboardInterrupt
            p = wm.add(win.grab())
            if p and p != pos:
                pos, last_move = p, time.time()
            if args.explore and time.time() - last_move > 1.6:
                return
            time.sleep(0.15)

    try:
        if args.explore:
            legs = spiral_legs(args.explore, args.step)
            for k, (dx, dy) in enumerate(legs):
                remaining = [dx, dy]
                while remaining[0] or remaining[1]:
                    sx = max(-50, min(50, remaining[0]))
                    sy = max(-50, min(50, remaining[1]))
                    remaining = [remaining[0] - sx, remaining[1] - sy]
                    a = MM.heading(win.grab()) or 0.0
                    cx, cy = Localizer.map_to_minimap(sx, sy, a)
                    inp.click(MM.CENTER[0] + cx, MM.CENTER[1] + cy)
                    record_until_still(12)
                print(f"leg {k + 1}/{len(legs)} done - at {pos}, path points {len(wm.path)}")
        else:
            print("walk around the area; press F12 when done", flush=True)
            while True:
                record_until_still(5)
                print(f"at {pos}, {len(wm.path)} path points", flush=True)
    except KeyboardInterrupt:
        pass
    wm.save()
    print(f"saved {MAPS / (args.name + '.png')}  ({len(wm.path)} path points)")


if __name__ == "__main__":
    main()
