"""Grindstone - run a bot against the local 2009scape Singleplayer client.

    %USERPROFILE%\\.venvs\\lumberjack\\Scripts\\python.exe -m lumberjack.main woodcut --minutes 30

Press F12 at any time to stop.
"""
import argparse
import logging

from lumberjack.skills.woodcutting import Woodcutter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=["woodcut"])
    ap.add_argument("--minutes", type=float, default=None, help="stop after this long")
    ap.add_argument("--trees", default="tree", help="comma-separated, e.g. oak,tree (best available is chopped)")
    ap.add_argument("--map", default=None, help="recorded map to walk between saved tree spots")
    ap.add_argument("--drop-at", type=int, default=28, help="drop logs once this many are held")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    if args.task == "woodcut":
        Woodcutter(max_minutes=args.minutes, trees_allowed=args.trees.split(","), drop_at=args.drop_at,
                   map_name=args.map).run()


if __name__ == "__main__":
    main()
