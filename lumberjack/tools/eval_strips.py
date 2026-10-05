"""Regression check for hover-text reading across collected strips (captures/strips/*.png).

    python -m lumberjack.tools.eval_strips
"""
import glob
from pathlib import Path

import cv2

from lumberjack.ui import mouseover as M

STRIPS = sorted(glob.glob(str(Path(__file__).resolve().parents[1] / "captures" / "strips" / "*.png")))


def main():
    for f in STRIPS:
        s = cv2.imread(f)
        a = M.strip_score(s, "chop_down", "white")
        b = M.strip_score(s, "tree", "cyan")
        verdict = M.strip_is_text(s, "chop_down", "tree")
        print(f"{Path(f).name}  chop {a[0]:.2f}/{a[1]:.2f}  tree {b[0]:.2f}/{b[1]:.2f}  -> {verdict}")


if __name__ == "__main__":
    main()
