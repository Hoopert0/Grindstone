"""Shared plumbing for bot tasks: stop/pause handling, timing, start options, keep-list."""
import logging
import time

import win32api

from lumberjack import actions, singleplayer_guard
from lumberjack.core.input import AgentInput
from lumberjack.core.window import GameWindow
from lumberjack.ui import inventory

STOP_KEY = 0x7B  # F12, checked globally
MAX_KEPT = 4


class StopBot(Exception):
    pass


class BotBase:
    name = "bot"

    def __init__(self, max_minutes=None, max_logs=None, stop_event=None, pause_event=None,
                 map_name=None, start_mode="here", start_spot=None, keep_carried=True, clear_at_start=False):
        self.log = logging.getLogger(self.name)
        singleplayer_guard.announce(self.log)
        singleplayer_guard.require()          # offline singleplayer only - see singleplayer_guard.py
        self.win = GameWindow()
        self.inp = AgentInput()
        self.ctx = actions.Ctx(self.win, self.inp, self.sleep, grabber=self.grab)
        self.deadline = time.monotonic() + max_minutes * 60 if max_minutes else None
        self.max_logs = max_logs
        self.stop_event, self.pause_event = stop_event, pause_event
        self.start_mode, self.start_spot = start_mode, start_spot
        self.keep_carried = keep_carried
        self.clear_at_start = clear_at_start   # drop logs/ore/fish/loot first (tools stay)
        self.keep_slots = set()
        self.started = time.monotonic()
        self.state = "starting"
        self.stop_reason = None   # shown in the panel when the run ends
        self.logs_cut = self.banked = self.burned = 0
        self.walker = None
        if map_name:
            from lumberjack.nav.walker import Walker
            from lumberjack.nav.worldmap import WorldMap
            self.walker = Walker(self.win, self.inp, WorldMap.load(map_name), sleep=self.sleep)

    # ---- control ---------------------------------------------------------------------
    def progress(self):
        """The number compared against max_logs (override per task)."""
        return self.logs_cut

    def check_stop(self):
        if win32api.GetAsyncKeyState(STOP_KEY) & 0x8000:
            raise StopBot("F12 pressed")
        try:
            singleplayer_guard.require()      # re-checked once a minute: never online
        except singleplayer_guard.NotSingleplayer as e:
            raise StopBot(str(e))
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopBot("stopped from control panel")
        if self.deadline and time.monotonic() > self.deadline:
            raise StopBot("time limit reached")
        if self.max_logs and self.progress() >= self.max_logs:
            raise StopBot(f"reached {self.max_logs}")
        from lumberjack.skills.watch import plan_checks
        plan_checks(self, StopBot)      # plan conditions: target level, stall guard, logged out
        if self.pause_event is not None and self.pause_event.is_set():
            before, self.state = self.state, "paused"
            from lumberjack.skills.watch import keep_awake
            while self.pause_event.is_set():
                if self.stop_event is not None and self.stop_event.is_set():
                    raise StopBot("stopped from control panel")
                keep_awake(self)             # paused for long: still logged in when resumed
                time.sleep(0.1)
            self.state = before

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check_stop()
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))

    def grab(self):
        from lumberjack.core.window import GameMinimized
        self.check_stop()
        warned = False
        while True:
            try:
                return self.win.grab()
            except GameMinimized:
                if not warned:   # wait it out instead of crashing (and keep F12 / Stop working)
                    self.log.warning("The game window is minimized - waiting until it's back")
                    warned = True
                before, self.state = self.state, "game minimized"
                self._watch_marker = None    # minimized isn't stalled: the "no progress" clock waits
                time.sleep(1.0)
                self.check_stop()
                self.state = before

    # ---- start-up --------------------------------------------------------------------
    def prepare(self):
        """Close stray menus/bank, set the camera, remember what we're carrying."""
        from lumberjack import bank
        self.inp.move(260, 300)
        self.sleep(0.3)
        if bank.is_open(self.grab()):
            bank.close(self.ctx)
        self.state = "setting camera"
        actions.reset_camera(self.ctx)
        if self.start_mode == "teleport":
            self.state = "teleporting"
            if not actions.home_teleport(self.ctx):
                raise StopBot("Home Teleport failed")
            actions.reset_camera(self.ctx)
        if self.walker and self.start_mode == "spot" and self.start_spot in self.walker.map.spots:
            from lumberjack.nav.localizer import Fix
            s = self.walker.map.spots[self.start_spot]
            self.walker.loc.last = Fix(s["x"], s["y"], 0.0, 1.0, local=True)
        if self.clear_at_start:
            self.state = "dropping materials"
            actions.clear_materials(self.ctx, food=self.name not in ("combat", "ranged", "thieving"), log=self.log)
        if self.keep_carried:
            actions.open_tab(self.ctx, "inventory")
            from lumberjack.core import gamestate
            if gamestate.shared() is not None:
                # items known by name: keep the tools (and anything unknown), whatever their number -
                # products (logs, ore, fish...) aren't "carried tools" and get dropped/banked as usual
                self.keep_slots = actions.carried_keepers(self.ctx)
                if self.keep_slots:
                    self.log.info("Keeping %d tool(s)/item(s) (slots %s)", len(self.keep_slots),
                                  ", ".join(str(s + 1) for s in sorted(self.keep_slots)))
                return
            carried = {i for i, o in enumerate(inventory.occupied(self.grab())) if o}
            if len(carried) > MAX_KEPT:
                raise StopBot(f"you're carrying {len(carried)} items - bank everything except what the "
                              f"bot should keep (axe, tinderbox), or untick 'Keep what I'm carrying'")
            self.keep_slots = carried
            if carried:
                self.log.info("Keeping the %d item(s) you're carrying (slots %s)",
                              len(carried), ", ".join(str(s + 1) for s in sorted(carried)))

    def spot_xy(self, name):
        s = self.walker.map.spots.get(name) if (self.walker and name) else None
        return (s["x"], s["y"]) if s else None

    def walk_to_spot(self, name):
        xy = self.spot_xy(name)
        if not xy:
            return False
        self.state = f"walking to {name}"
        self.log.info("Walking to '%s'", name)
        return self.walker.walk_to(*xy)

    def stats(self):
        hrs = (time.monotonic() - self.started) / 3600
        return f"{self.burned} fires, {self.banked} bank trips" if hrs > 0 else ""
