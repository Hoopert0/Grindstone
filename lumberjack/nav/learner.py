"""Grow the map while the bot works.

A background thread runs the recorder's stitching on live frames: once we have a
confident position fix, each new minimap capture is matched against the map around the
last placed position and blended in. Areas the bot wanders into (off the recorded route)
become part of the map, so localization keeps working there. If tracking is lost, it waits
for another confident fix before adding anything again.
"""
import json
import logging
import threading
import time

from lumberjack.nav.localizer import Localizer
from lumberjack.nav.worldmap import MAPS

log = logging.getLogger("map-learner")

STRONG_SEED = 0.60       # only (re)start stitching from a confident fix
REFRESH_EVERY = 40       # placed captures between localizer refreshes
SAVE_EVERY_S = 120


class MapLearner:
    def __init__(self, walker, win):
        self.walker, self.win = walker, win
        self.map = walker.map
        self.stop_event = threading.Event()
        self.thread = None
        self.placed = 0
        self.dirty = False

    def start(self):
        self.thread = threading.Thread(target=self._run, name="map-learner", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        if self.dirty:
            self.save()

    def _seed(self, frame):
        fix = self.walker.loc.locate(frame)
        if fix.ok and fix.score >= STRONG_SEED:
            self.map.resume_at(frame)
            return True
        return False

    def _run(self):
        last_save = time.monotonic()
        since_refresh = 0
        while not self.stop_event.is_set():
            try:
                frame = self.win.grab()
                if self.map._prev is None or self.map.lost_frames > 20:
                    self.map._prev = None
                    if not self._seed(frame):
                        self.stop_event.wait(2.0)
                        continue
                if self.map.add(frame):
                    self.placed += 1
                    since_refresh += 1
                    self.dirty = True
                if since_refresh >= REFRESH_EVERY:
                    # hand the walker a localizer that knows the newly learned areas
                    fresh = Localizer(self.map)
                    fresh.last = self.walker.loc.last
                    self.walker.loc = fresh
                    since_refresh = 0
                if self.dirty and time.monotonic() - last_save > SAVE_EVERY_S:
                    self.save()
                    last_save = time.monotonic()
            except Exception as e:  # never take the bot down with us
                if type(e).__name__ == "GameMinimized":
                    self.stop_event.wait(2.0)   # nothing to see; the bot says so once
                    continue
                log.exception("map learner hiccup")
            self.stop_event.wait(0.4)

    def save(self):
        # keep spots saved from the control panel meanwhile
        try:
            self.map.spots = json.loads((MAPS / f"{self.map.name}.json").read_text()).get("spots", self.map.spots)
        except (OSError, ValueError):
            pass
        self.map.save()
        self.dirty = False
        log.info("Map '%s' updated (%d new captures learned)", self.map.name, self.placed)
