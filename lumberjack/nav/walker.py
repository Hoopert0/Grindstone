"""Walk to a point on a recorded map by clicking the minimap.

Closed loop: locate -> click up to ~12 tiles toward the target -> wait until we stop ->
repeat. If a direct step makes no progress (river, wall, fence), hop along the route
walked while recording the map, which is known to be walkable.
"""
import logging
import math
import time

import numpy as np

from lumberjack.nav.localizer import Localizer
from lumberjack.vision import minimap as MM

log = logging.getLogger("walker")

STEP = 48          # max map px per click (~12 tiles), stays inside the minimap disc
ARRIVED = 8        # map px (~2 tiles)
CLOSE_ENOUGH = 20  # if we can't get nearer than this (target tile blocked), call it arrived


class Walker:
    def __init__(self, win, inp, worldmap, sleep=time.sleep):
        self.win, self.inp = win, inp
        self.map = worldmap
        self.loc = Localizer(worldmap)
        self.sleep = sleep

    def where(self, tries=3):
        for _ in range(tries):
            fix = self.loc.locate(self.win.grab())
            if fix.ok:
                return fix
            self.sleep(0.4)
        return None

    def _wait_until_still(self, timeout=10.0):
        """Wait for the walk to finish: the minimap stops scrolling."""
        prev, still_since, start = None, None, time.monotonic()
        self.sleep(0.6)  # give the walk time to start
        while time.monotonic() - start < timeout:
            img, mask = MM.patch(self.win.grab())
            if prev is not None:
                m = (mask > 0) & (prev[1] > 0)
                diff = float(np.abs(img.astype(np.int16) - prev[0].astype(np.int16))[m].mean()) if m.any() else 99
                if diff < 1.5:
                    still_since = still_since or time.monotonic()
                    if time.monotonic() - still_since > 0.9:
                        return
                else:
                    still_since = None
            prev = (img, mask)
            self.sleep(0.15)

    def _click_map_vector(self, dx, dy, angle):
        s = MM.click_scale()            # the client's login-time minimap zoom (1.0 without game data)
        cx, cy = Localizer.map_to_minimap(dx * s, dy * s, angle)
        r = math.hypot(cx, cy)
        if r > STEP + 4:  # safety: never click outside the usable disc
            cx, cy = cx * STEP / r, cy * STEP / r
        self.inp.click(MM.CENTER[0] + cx, MM.CENTER[1] + cy)

    def _route_hop(self, x, y, tx, ty):
        """A recorded-route point within reach that gets us closer to the target."""
        best, best_d = None, math.hypot(tx - x, ty - y) - 6
        for px, py in self.map.path:
            if 12 <= math.hypot(px - x, py - y) <= STEP:
                d = math.hypot(tx - px, ty - py)
                if d < best_d:
                    best, best_d = (px, py), d
        return best

    def walk_to(self, tx, ty, max_clicks=30):
        """Returns True on arrival, False if lost or stuck."""
        stuck = 0
        last = None
        best, since_best = float("inf"), 0
        for k in range(max_clicks):
            fix = self.where()
            if fix is None:
                log.warning("Lost - can't match the minimap to the map")
                return False
            dx, dy = tx - fix.x, ty - fix.y
            dist = math.hypot(dx, dy)
            log.info("  step %d: at (%d, %d) score %.2f, %.0f px to go", k + 1, fix.x, fix.y, fix.score, dist)
            if dist <= ARRIVED:
                log.info("Arrived (%d, %d)", fix.x, fix.y)
                return True
            # going back and forth (bad fixes / blocked path): stop early instead of wandering
            if dist < best - 3:
                best, since_best = dist, 0
            else:
                since_best += 1
                if since_best >= 6:
                    log.warning("Not getting closer to (%d, %d) - giving up", tx, ty)
                    return False
            if last is not None and math.hypot(fix.x - last[0], fix.y - last[1]) < 4:
                stuck += 1
            else:
                stuck = 0
            last = (fix.x, fix.y)
            if stuck >= 1 and dist <= CLOSE_ENOUGH:
                log.info("Close enough (%.0f px) - can't get nearer", dist)
                return True
            if stuck >= 1:
                hop = self._route_hop(fix.x, fix.y, tx, ty)
                if hop is None or stuck >= 4:
                    log.warning("Stuck at (%d, %d), %.0f px from target", fix.x, fix.y, dist)
                    return False
                dx, dy = hop[0] - fix.x, hop[1] - fix.y
                log.info("Direct way blocked - following the recorded route via %s", hop)
            elif dist > STEP:
                dx, dy = dx * STEP / dist, dy * STEP / dist
            self._click_map_vector(dx, dy, fix.angle)
            self._wait_until_still()
        log.warning("Gave up after %d clicks", max_clicks)
        return False
