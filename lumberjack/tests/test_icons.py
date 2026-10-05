"""Minimap bank-icon walking: icon matching and the north-up turn (no game needed)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from lumberjack.nav import icons  # noqa: E402
from lumberjack.nav.localizer import Localizer  # noqa: E402
from lumberjack.vision import minimap as MM  # noqa: E402


def frame_with_icon(dx, dy):
    f = np.zeros((503, 765, 3), np.uint8)
    f[:] = (40, 120, 60)
    cx, cy = MM.CENTER
    cv2.circle(f, (cx, cy), MM.R_USE, (60, 140, 90), -1)
    t = cv2.imread(str(icons.BANK_ICON))
    h, w = t.shape[:2]
    x, y = cx + dx - w // 2, cy + dy - h // 2
    f[y:y + h, x:x + w] = t
    return f


def test_find_bank_icon():
    f = frame_with_icon(-10, 40)
    found = icons.find_icons(f)
    assert len(found) == 1
    dx, dy = found[0]
    assert abs(dx + 10) <= 1 and abs(dy - 40) <= 1, found


def test_no_icon():
    f = np.zeros((503, 765, 3), np.uint8)
    f[:] = (40, 120, 60)
    assert icons.find_icons(f) == []


def test_north_turn_round_trip():
    for angle in (0, 37, 90, 200):
        live = Localizer.map_to_minimap(12, -30, angle)
        n = icons.to_north(*live, angle)
        assert math.isclose(n[0], 12, abs_tol=1e-6) and math.isclose(n[1], -30, abs_tol=1e-6), (angle, n)


def test_step_clamp():
    assert icons.step_toward(3, 4) == (3, 4)
    x, y = icons.step_toward(300, 400)
    assert math.isclose(math.hypot(x, y), icons.STEP)


def test_heading_and_scale_from_game_data():
    from lumberjack.core import gamestate as G
    from lumberjack.core import interact

    class GS:
        def minimap(self):
            return {"angle": 512, "scale": 1.0625}        # north points right; minimap 6% bigger

    class Ctx:
        clicks = []
        grab = staticmethod(lambda: None)

        class inp:
            @staticmethod
            def click(x, y):
                Ctx.clicks.append((x, y))

    G._shared = GS()
    try:
        assert MM.heading() == 90.0 and MM.click_scale() == 1.0625
        interact.walk_toward(Ctx, (3200, 3200), (3200, 3205))   # 5 tiles north
        x, y = Ctx.clicks[-1]
        assert abs(x - (MM.CENTER[0] + 5 * 4 * 1.0625)) < 0.5 and abs(y - MM.CENTER[1]) < 0.5
    finally:
        G._shared = None
        G._shared_retry_at = 0.0
    G._shared_retry_at = 1e18          # no game data: the needle (none on a blank frame) and scale 1
    try:
        assert MM.heading(np.zeros((503, 765, 3), np.uint8)) is None and MM.click_scale() == 1.0
    finally:
        G._shared_retry_at = 0.0
