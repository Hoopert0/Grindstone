"""Agility: run an obstacle course, nonstop (game data + admin teleport).

A course is data: its obstacles in order, each with the area you stand in before it. Each pass:

    where am I? -> the obstacle whose area holds me -> do its option on it -> wait to land
    lost (fell, wandered, an event moved us) or stuck on one obstacle -> ::tele to the start

Courses and their obstacles come from the 2009scape server's own course code.
"""
import time

from lumberjack.skills.base import BotBase, StopBot

OBSTACLE_RADIUS = 15
LAND_S = 20.0              # an obstacle takes up to ~10 s (the pipe); wait for the landing
TRIES = 4                  # clicks on one obstacle that never moved us -> back to the start
STILL_BEFORE_S = 8.0       # still before the obstacle this long after a click -> click again
LOST_LIMIT = 6             # teleports to the start in a row without progress -> stop
MISS_LIMIT = 8             # looks (~1 s apart) that never saw the obstacle -> back to the start
SETTLE_S = 2.0             # after a teleport: let the scene load before looking for obstacles


def box(x0, y0, x1, y1, plane):
    return (x0, y0, x1, y1, plane)


def inside(b, tile, plane):
    x0, y0, x1, y1, p = b
    return p == plane and x0 <= tile[0] <= x1 and y0 <= tile[1] <= y1


# Gnome Stronghold (level 1): 7 obstacles, no failing. The server's GnomeStrongholdCourse.
GNOME = {
    "name": "Gnome Stronghold course",
    "level": 1,
    "start": (2474, 3437, 0),
    "obstacles": [
        {"ids": {2295}, "op": "Walk-across", "area": box(2466, 3435, 2492, 3442, 0)},     # log balance
        {"ids": {2285}, "op": "Climb-over", "area": box(2466, 3426, 2480, 3434, 0)},      # net up
        {"ids": {35970}, "op": "Climb", "area": box(2466, 3415, 2480, 3430, 1)},          # tree branch
        {"ids": {2312}, "op": "Walk-on", "area": box(2466, 3415, 2479, 3425, 2)},         # balancing rope
        {"ids": {2314, 2315}, "op": "Climb-down", "area": box(2480, 3415, 2492, 3425, 2)},
        {"ids": {2286}, "op": "Climb-over", "area": box(2481, 3413, 2495, 3425, 0)},      # net
        # the pipes' far ends (y 3435) only say "You can't do that from here"
        {"ids": {154, 4058}, "op": "Squeeze-through", "area": box(2481, 3426, 2495, 3434, 0),
         "at": box(2480, 3428, 2492, 3433, 0)},
    ],
}
# Barbarian Outpost (level 35): the server's BarbarianOutpostCourse. Its door wants the Barcrawl
# card - the teleport starts us inside. A slip drops us in a pit or the water: off the course,
# so the runner teleports back to the start.
BARBARIAN = {
    "name": "Barbarian Outpost course",
    "level": 35,
    "start": (2552, 3556, 0),
    "obstacles": [
        {"ids": {2282}, "op": "Swing-on", "area": box(2542, 3552, 2560, 3562, 0)},       # rope swing
        {"ids": {2294}, "op": "Walk-across", "area": box(2546, 3544, 2556, 3551, 0)},    # log balance
        {"ids": {20211}, "op": "Climb-over", "area": box(2538, 3543, 2545, 3550, 0)},    # net up
        {"ids": {2302}, "op": "Walk-across", "area": box(2535, 3540, 2545, 3555, 1)},    # ledge
        {"names": {"Ladder"}, "op": "Climb-down", "area": box(2525, 3540, 2534, 3555, 1)},
        {"ids": {1948}, "op": "Climb-over", "area": box(2525, 3540, 2535, 3558, 0),      # the 3 low walls,
         "at": box(2536, 3553, 2536, 3553, 0)},                                          # west to east
        {"ids": {1948}, "op": "Climb-over", "area": box(2537, 3551, 2538, 3556, 0),
         "at": box(2539, 3553, 2539, 3553, 0)},
        {"ids": {1948}, "op": "Climb-over", "area": box(2540, 3551, 2541, 3556, 0),
         "at": box(2542, 3553, 2542, 3553, 0)},
    ],
}
COURSES = [GNOME, BARBARIAN]


def best_course(level):
    return max((c for c in COURSES if c["level"] <= level), key=lambda c: c["level"])


def next_obstacle(course, tile, plane):
    """(index, obstacle) whose area holds us, or (None, None)."""
    for i, o in enumerate(course["obstacles"]):
        if inside(o["area"], tile, plane):
            return i, o
    return None, None


class Agility(BotBase):
    name = "agility"

    def __init__(self, **kw):
        kw.pop("keep_carried", None)
        super().__init__(keep_carried=False, **kw)
        self.laps = self.obstacles = 0
        self.course = None
        self.came_from = None

    def progress(self):
        return self.laps

    def stats(self):
        return f"{self.laps} laps, {self.obstacles} obstacles"

    def run(self):
        self.log.info("Agility started - press F12 to stop")
        try:
            self.prepare()
            from lumberjack.core import gamestate
            self.gs = gamestate.shared()
            if self.gs is None:
                raise StopBot("Agility needs the game's own data (it finds the obstacles by id)")
            me = self.gs.player()
            self.came_from = (me["tile"][0], me["tile"][1], me.get("plane", 0))
            lv = (gamestate.skill("agility") or {}).get("base", 1)
            self.course = best_course(lv)
            self.loop()
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            try:
                self.leave()
            except Exception as e:                  # the game went away: nothing to tidy
                self.log.debug("leave: %s", e)
            self.state = "stopped"
            self.inp.close()

    def loop(self):
        course, obstacles = self.course, self.course["obstacles"]
        lost = tries = misses = 0
        last = None
        while True:
            self.check_stop()
            me = self.gs.player()
            i, o = next_obstacle(course, me["tile"], me.get("plane", 0))
            if o is None or tries >= TRIES or misses >= MISS_LIMIT:
                lost += 1
                if lost > LOST_LIMIT:
                    raise StopBot(f"keeps getting lost on the {course['name']}")
                why = (f"off the course (at {me['tile']} plane {me.get('plane', 0)})" if o is None
                       else "stuck on an obstacle" if tries >= TRIES else "can't see the next obstacle")
                self.to_start(why)
                tries = misses = 0
                last = None
                continue
            if i != last:
                if last is not None:                # moved on: the last obstacle is done
                    self.obstacles += 1
                    lost = 0
                    if last == len(obstacles) - 1:
                        self.laps += 1
                        self.log.info("Lap %d done", self.laps)
                last, tries = i, 0
            self.state = f"{o['op'].lower()} ({i + 1}/{len(obstacles)})"
            done = self.do(o, me)
            if done == "miss":                      # not in sight (yet): that's no try on it
                misses += 1
                if misses == MISS_LIMIT // 2:
                    self.log_nearby(o)
            else:
                misses = 0
                tries += 1

    def find(self, o, plane):
        """The obstacle's locs: by id/name first, else anything here offering its option."""
        locs = self.gs.locs(OBSTACLE_RADIUS)

        def has_op(l):
            return o["op"].lower() in [op.lower() for op in (l.get("ops") or [])]

        def placed(l):
            return o.get("at") is None or inside(o["at"], l["tile"], plane)
        found = [l for l in locs
                 if (l.get("id") in o.get("ids", ()) or l["name"] in o.get("names", ()))
                 and (has_op(l) or not l.get("ops")) and placed(l)]
        if found:
            return found
        # a different id in this cache: the option is what counts (nearest first)
        return sorted((l for l in locs if has_op(l) and placed(l)), key=lambda l: l.get("dist", 0))

    def log_nearby(self, o):
        try:
            locs = self.gs.locs(OBSTACLE_RADIUS)
        except Exception:
            return
        seen = sorted({(l["name"], l.get("id"), "/".join(l.get("ops") or [])) for l in locs if l.get("ops")})
        self.log.info("Can't see the obstacle to %s (ids %s). Nearby: %s", o["op"],
                      sorted(o.get("ids", ())) or sorted(o.get("names", ())),
                      "; ".join(f"{n} #{i} [{ops}]" for n, i, ops in seen[:15]) or "nothing")

    def do(self, o, me):
        """Click obstacle `o` and wait until we've landed somewhere else (or time's up).
        "miss" when it isn't in sight, "noclick" when the click didn't land, else "done"."""
        from lumberjack.core import interact
        plane = me.get("plane", 0)
        found = self.find(o, plane)
        if not found:
            self.sleep(1.0)
            return "miss"
        loc = found[0]
        if not interact.use_option(self.ctx, self.gs, interact.points_for(loc), o["op"], loc["name"]):
            from lumberjack.core.gamestate import top_entry
            try:
                top = top_entry(self.gs.menu())
            except Exception:
                top = None
            self.log.info("Couldn't click %s %s at %s (screen %s, the mouse shows %s) - walking closer",
                          o["op"], loc["name"], loc["tile"], loc.get("screen"),
                          f'"{top["verb"]} {top["subject"]}"' if top else "nothing")
            interact.walk_to_tile(self.ctx, self.gs, loc["tile"], arrive=2)   # off screen: get closer
            return "noclick"
        self.log.info("%s %s", o["op"], loc["name"])
        t0, still = time.monotonic(), None
        while time.monotonic() - t0 < LAND_S:
            self.sleep(0.6)
            now = self.gs.player()
            here = (tuple(now["tile"]), now.get("plane", 0))
            if inside(o["area"], now["tile"], here[1]):
                if time.monotonic() - t0 > STILL_BEFORE_S:      # never got going: click again
                    return "done"
                continue
            if still is None or here != still[0]:
                still = (here, time.monotonic())
            elif time.monotonic() - still[1] > 1.2:             # landed and stopped moving
                return "done"
        return "done"

    def to_start(self, why):
        from lumberjack.nav import places
        x, y, plane = self.course["start"]
        self.state = "back to the start"
        self.log.info("%s - teleporting to the start of the %s", why.capitalize(), self.course["name"])
        if not places.teleport(self.ctx, self.gs, (x, y), plane):
            raise StopBot(f"couldn't teleport to the {self.course['name']}")
        self.sleep(SETTLE_S)                         # the scene's objects load after we land

    def leave(self):
        """Stay put: a run also ends before every retry, and teleporting back to where it started
        sent us away from the spot each time. The next step travels by itself (Autopilot surfaces
        at Lumbridge first when it needs open ground)."""
        return
