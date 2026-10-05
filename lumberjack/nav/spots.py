"""Named spots on a recorded map (e.g. a tree cluster), plus a small CLI.

    python -m lumberjack.nav.spots where                      # current map position
    python -m lumberjack.nav.spots save oaks_west --trees oak,tree
    python -m lumberjack.nav.spots list
    python -m lumberjack.nav.spots goto oaks_west
    (all take --map NAME, default lumbridge)
"""
import argparse
import logging

from lumberjack.nav.worldmap import WorldMap


def save_spot(wm, name, x, y, trees, kind="trees"):
    wm.spots[name] = {"x": int(x), "y": int(y), "trees": list(trees), "kind": kind}
    wm.save()


def bank_spots(wm):
    return [(n, s) for n, s in wm.spots.items() if s.get("kind") == "bank"]


def delete_spot(wm, name):
    wm.spots.pop(name, None)
    wm.save()


def spots_for(wm, trees):
    """Spots that list at least one of `trees` (or list no trees at all)."""
    out = []
    for name, s in wm.spots.items():
        if s.get("kind") in ("bank", "place", "fish"):   # banks, waypoints and fishing spots aren't tree spots
            continue
        if not s.get("trees") or set(s["trees"]) & set(trees):
            out.append((name, s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["where", "save", "list", "goto", "delete"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--map", default="lumbridge")
    ap.add_argument("--trees", default="tree")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    wm = WorldMap.load(args.map)

    if args.cmd == "list":
        for n, s in wm.spots.items():
            print(f"{n:20s} ({s['x']}, {s['y']})  trees: {', '.join(s.get('trees', []))}")
        return
    if args.cmd == "delete":
        delete_spot(wm, args.name)
        return

    from lumberjack.core.input import AgentInput
    from lumberjack.core.window import GameWindow
    from lumberjack.nav.walker import Walker
    wk = Walker(GameWindow(), AgentInput(), wm)
    if args.cmd == "where":
        print(wk.where())
    elif args.cmd == "save":
        fix = wk.where()
        if not fix:
            raise SystemExit("Can't tell where we are on this map")
        save_spot(wm, args.name, fix.x, fix.y, args.trees.split(","))
        print(f"saved {args.name} at ({fix.x}, {fix.y})")
    elif args.cmd == "goto":
        s = wm.spots[args.name]
        print("arrived" if wk.walk_to(s["x"], s["y"]) else "didn't make it")


if __name__ == "__main__":
    main()
