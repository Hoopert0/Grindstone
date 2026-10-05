"""Probe: does the game accept background mouse moves? Hover a few points, save the mouseover strip."""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from lumberjack.core import regions as R
from lumberjack.core.input import AgentInput
from lumberjack.core.window import GameWindow

OUT = Path(__file__).parent / "captures"
win = GameWindow()
inp = AgentInput()
win.grab()

points = [tuple(map(int, p.split(","))) for p in sys.argv[1:]] or [(225, 205), (420, 260)]
strips = []
for x, y in points:
    inp.move(x, y)
    time.sleep(0.4)
    strip = R.MOUSEOVER_TEXT.crop(win.grab())
    strips.append(cv2.resize(strip, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
    print(f"hover ({x},{y})")
cv2.imwrite(str(OUT / "hover_strips.png"), np.vstack(strips))
cv2.imwrite(str(OUT / "hover_last_frame.png"), win.grab())
