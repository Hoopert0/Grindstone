"""Merge one recorded map into another (both north-up, same scale).

    python -m lumberjack.tools.merge_maps world draynor     # draynor -> world

Finds where the source map sits inside the target by matching the source's central area,
then blends the source in and copies its spots and route across (shifted).
"""
import argparse

import cv2
import numpy as np

from lumberjack.nav.worldmap import CANVAS, WorldMap


def find_offset(target, source, crop=130):
    """(dx, dy, score): add to source coords to get target coords."""
    timg, simg = target.image(), source.image()
    ys, xs = np.where(source.weight > 0)
    centres = [(int(xs.mean()), int(ys.mean()))]
    # spots are where both maps were walked, so crops around them are likeliest to overlap
    centres += [(s["x"], s["y"]) for s in source.spots.values()]
    votes = []
    for cx, cy in centres:
        x0, y0 = cx - crop // 2, cy - crop // 2
        patch = simg[y0:y0 + crop, x0:x0 + crop]
        mask = (source.weight[y0:y0 + crop, x0:x0 + crop] > 0).astype(np.uint8) * 255
        if mask.mean() < 200:  # mostly unrecorded - skip
            continue
        res = cv2.matchTemplate(timg, patch, cv2.TM_CCOEFF_NORMED, mask=mask)
        res[~np.isfinite(res)] = -1
        _, score, _, (mx, my) = cv2.minMaxLoc(res)
        votes.append((mx - x0, my - y0, float(score)))
    # Single crops can mis-match (repetitive trees, blurry one-pass map), so prefer an
    # offset that several crops agree on; fall back to the single best score.
    best = None
    for dx, dy, sc in votes:
        agree = [v for v in votes if abs(v[0] - dx) <= 8 and abs(v[1] - dy) <= 8]
        support = (len(agree), sum(v[2] for v in agree))
        if best is None or support > best[0]:
            best = (support, agree)
    if not best:
        return None
    (n, total), agree = best
    dx = int(round(np.mean([v[0] for v in agree])))
    dy = int(round(np.mean([v[1] for v in agree])))
    # agreement between independent crops is strong evidence even at modest scores
    return dx, dy, (1.0 if n >= 2 else total)


def merge(target_name, source_name, min_score=0.5):
    target, source = WorldMap.load(target_name), WorldMap.load(source_name)
    found = find_offset(target, source)
    if not found or found[2] < min_score:
        raise SystemExit(f"Couldn't line the maps up (best score {found and round(found[2], 2)}) - "
                         "do they overlap?")
    dx, dy, score = found
    print(f"'{source_name}' sits at offset ({dx}, {dy}) in '{target_name}' (score {score:.2f})")
    # blend the source's known pixels in (shifted), without overwriting what's there
    ys, xs = np.where(source.weight > 0)
    tx, ty = xs + dx, ys + dy
    ok = (tx >= 0) & (ty >= 0) & (tx < CANVAS) & (ty < CANVAS)
    xs, ys, tx, ty = xs[ok], ys[ok], tx[ok], ty[ok]
    empty = target.weight[ty, tx] == 0
    target.img[ty[empty], tx[empty]] = source.img[ys[empty], xs[empty]] / np.maximum(
        source.weight[ys[empty], xs[empty]], 1e-6)[:, None]
    target.weight[ty[empty], tx[empty]] = 1.0
    for name, s in source.spots.items():
        target.spots[name] = {**s, "x": s["x"] + dx, "y": s["y"] + dy}
    target.path += [(x + dx, y + dy) for x, y in source.path]
    target.save()
    print(f"merged: {len(source.spots)} spots copied ({', '.join(source.spots)}), "
          f"{int(empty.sum())} new map pixels")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("source")
    args = ap.parse_args()
    merge(args.target, args.source)


if __name__ == "__main__":
    main()
