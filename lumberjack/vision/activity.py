"""Is our character doing something (walking / swinging an axe) or standing idle?

Frame-differences the box around the player. Idle frames still flicker a little (grass
animation, ~1.5-4), while chopping/walking regularly spikes above ~6. We look at the
*max* over a short window because the swing animation has pauses between strokes.
"""
import collections
import time

import numpy as np

from lumberjack.core import regions as R

ACTIVE_THRESHOLD = 5.5


class ActivityMonitor:
    def __init__(self, window_s=2.0):
        self.window_s = window_s
        self.samples = collections.deque()
        self.prev = None

    def update(self, frame):
        cur = R.PLAYER.crop(frame).astype(np.int16)
        now = time.monotonic()
        if self.prev is not None:
            self.samples.append((now, float(np.abs(cur - self.prev).mean())))
        self.prev = cur
        while self.samples and now - self.samples[0][0] > self.window_s:
            self.samples.popleft()

    def reset(self):
        self.samples.clear()
        self.prev = None

    @property
    def filled(self):
        return bool(self.samples) and self.samples[-1][0] - self.samples[0][0] >= self.window_s * 0.8

    @property
    def active(self):
        return any(m > ACTIVE_THRESHOLD for _, m in self.samples)
