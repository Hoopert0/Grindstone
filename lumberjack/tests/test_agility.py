"""Agility: the course runner walks a fake Gnome Stronghold course."""
import sys
import types

import pytest

# the input agent is never loaded in tests (same stand-in as test_combat's)
_stub = types.ModuleType("lumberjack.core.input")
_stub.VK_LEFT, _stub.VK_RIGHT, _stub.VK_UP, _stub.VK_DOWN, _stub.VK_SHIFT = 0x25, 0x27, 0x26, 0x28, 0x10


class _NoInput:
    def __init__(self, *a, **k):
        raise RuntimeError("tests must never create AgentInput")


_stub.AgentInput = _NoInput
sys.modules.setdefault("lumberjack.core.input", _stub)

from lumberjack.core import interact  # noqa: E402
from lumberjack.nav import places  # noqa: E402
from lumberjack.skills import agility_task as A  # noqa: E402
from lumberjack.skills.base import StopBot  # noqa: E402

# where each obstacle lands us (the server's GnomeStrongholdCourse)
LANDS = {2295: ((2474, 3429), 0), 2285: ((2473, 3424), 1), 35970: ((2473, 3420), 2),
         2312: ((2483, 3420), 2), 2314: ((2487, 3420), 0), 2286: ((2487, 3427), 0),
         154: ((2484, 3437), 0)}
LOCS = [(2295, (2474, 3435), 0), (2285, (2473, 3425), 0), (35970, (2473, 3422), 1),
        (2312, (2478, 3420), 2), (2314, (2486, 3420), 2), (2286, (2487, 3426), 0),
        (154, (2484, 3431), 0), (154, (2484, 3435), 0)]       # a pipe's far end: never used


class Course:
    def __init__(self, tile=(3222, 3218), plane=0):
        self.tile, self.plane, self.clicks = tile, plane, []

    def player(self):
        return {"tile": list(self.tile), "plane": self.plane}

    def locs(self, radius=15, name=None):
        return [{"id": i, "name": "Obstacle", "tile": list(t), "screen": [300, 200], "body": [300, 190]}
                for i, t, p in LOCS if p == self.plane]


@pytest.fixture
def course(monkeypatch):
    c = Course()

    def use(ctx, gs, points, verb, subject):
        loc = next(l for l in c.locs() if l["screen"] == [300, 200] and l["id"] in c.want)
        c.clicks.append((loc["id"], verb))
        c.tile, c.plane = LANDS[loc["id"]]
        return 300, 200

    def tele(ctx, gs, tile, plane=0):
        c.tile, c.plane = tuple(tile), plane
        return True
    monkeypatch.setattr(interact, "use_option", use)
    monkeypatch.setattr(places, "teleport", tele)
    return c


def runner(c, monkeypatch, laps=2):
    a = A.Agility.__new__(A.Agility)
    a.gs, a.ctx, a.state, a.log = c, None, "", __import__("logging").getLogger("t")
    a.inp = types.SimpleNamespace(move=lambda *x, **k: None, close=lambda: None)
    a.laps = a.obstacles = 0
    a.course, a.came_from = A.GNOME, (3222, 3218, 0)
    clock = {"t": 0.0}
    monkeypatch.setattr(A.time, "monotonic", lambda: clock["t"])

    def sleep(s):
        clock["t"] += s
    a.sleep = sleep

    def check():
        if a.laps >= laps:
            raise StopBot("enough")
    a.check_stop = check
    orig = a.do

    def do(o, me):
        c.want = o["ids"]
        return orig(o, me)
    a.do = do
    return a


def test_runs_laps_in_order(course, monkeypatch):
    a = runner(course, monkeypatch)
    with pytest.raises(StopBot):
        a.loop()
    assert a.laps == 2
    order = [i for i, _ in course.clicks]
    assert order[:7] == [2295, 2285, 35970, 2312, 2314, 2286, 154]   # from off-course: teleport, then a lap
    assert course.clicks[0][1] == "Walk-across" and course.clicks[6][1] == "Squeeze-through"


def test_pipe_far_end_is_never_used():
    o = A.GNOME["obstacles"][-1]
    assert A.inside(o["at"], (2484, 3431), 0) and not A.inside(o["at"], (2484, 3435), 0)


def test_lost_too_often_stops(course, monkeypatch):
    a = runner(course, monkeypatch)
    monkeypatch.setattr(places, "teleport", lambda ctx, gs, tile, plane=0: True)   # never arrives
    with pytest.raises(StopBot, match="lost"):
        a.loop()


def test_leave_stays_put(course, monkeypatch):
    """The end of a run (also before every retry) no longer teleports away from the course."""
    a = runner(course, monkeypatch)
    course.tile = (2474, 3437)
    a.leave()
    assert course.tile == (2474, 3437)


def test_course_areas_never_overlap_and_landings_lead_on():
    def overlap(a, b):
        return a[4] == b[4] and a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]
    for c in A.COURSES:
        obs = c["obstacles"]
        for i, a in enumerate(obs):
            for b in obs[i + 1:]:
                assert not overlap(a["area"], b["area"]), (c["name"], a["op"], b["op"])
    bar = A.BARBARIAN
    # the server's landing tiles: each lands in the next obstacle's area
    lands = [((2551, 3549), 0), ((2541, 3546), 0), ((2540, 3546), 1), ((2532, 3547), 1),
             ((2532, 3546), 0), ((2537, 3553), 0), ((2540, 3553), 0), ((2543, 3553), 0)]
    for i, (tile, plane) in enumerate(lands):
        assert A.next_obstacle(bar, tile, plane)[0] == (i + 1) % len(bar["obstacles"]), (i, tile)
    assert A.next_obstacle(bar, bar["start"][:2], 0)[0] == 0
    assert A.next_obstacle(bar, (2545, 3542), 0) == (None, None)       # fell in the water: lost
    assert A.next_obstacle(bar, (2549, 9951), 0) == (None, None)       # the pit: lost
    assert A.best_course(34) is A.GNOME and A.best_course(35) is A.BARBARIAN


def test_obstacle_not_in_sight_yet_is_no_try(course, monkeypatch):
    """Right after the teleport the scene can still be loading: looks that see nothing don't count
    as clicks that failed (that sent us back to the start every ~4 s)."""
    a = runner(course, monkeypatch, laps=1)
    course.tile = (2474, 3437)
    real = course.locs
    looks = {"n": 0}

    def slow(radius=15, name=None):
        looks["n"] += 1
        return [] if looks["n"] <= 3 else real(radius, name)
    course.locs = slow
    teleports = []
    monkeypatch.setattr(places, "teleport", lambda ctx, gs, tile, plane=0: teleports.append(tile) or True)
    with pytest.raises(StopBot, match="enough"):
        a.loop()
    assert teleports == [] and a.laps == 1


def test_other_id_with_the_option_is_used(course, monkeypatch):
    """This cache numbers the log balance differently: anything here offering Walk-across will do."""
    a = runner(course, monkeypatch)
    course.locs = lambda radius=15, name=None: [
        {"id": 9999, "name": "Log balance", "ops": ["Walk-across"], "tile": [2474, 3435], "dist": 2},
        {"id": 1, "name": "Tree", "ops": ["Chop down"], "tile": [2470, 3437], "dist": 4}]
    found = a.find(A.GNOME["obstacles"][0], 0)
    assert [l["id"] for l in found] == [9999]


def test_never_seeing_the_obstacle_logs_and_goes_back(course, monkeypatch, caplog):
    a = runner(course, monkeypatch)
    course.tile = (2474, 3437)
    course.locs = lambda radius=15, name=None: [{"id": 1, "name": "Tree", "ops": ["Chop down"], "tile": [1, 1]}]
    monkeypatch.setattr(places, "teleport", lambda ctx, gs, tile, plane=0: True)
    with caplog.at_level("INFO"), pytest.raises(StopBot, match="lost"):
        a.loop()
    assert "Tree #1" in caplog.text and "can't see the next obstacle" in caplog.text.lower()


def test_an_obstacle_the_add_on_couldnt_place_is_found_from_its_neighbours():
    """The log balance came back at screen [-1, -1]; the spread around it hovered the top-left
    corner ("Walk here") forever. Now: no points from a bad screen, and the obstacle's tiles are
    placed by the objects around it."""
    bad = {"id": 2295, "name": "Log balance", "tile": [2474, 3430], "size": [1, 6],
           "screen": [-1, -1], "body": [-1, -1]}
    assert interact.points_for(bad) == []
    # one-tile objects with screen points: 30 px per tile, north up, our tile (2474, 3437) at (258, 186)
    near = [{"tile": [x, y], "size": [1, 1], "screen": [258 + (x - 2474) * 30, 186 - (y - 3437) * 30]}
            for x, y in [(2470, 3437), (2478, 3438), (2472, 3433), (2477, 3432), (2475, 3436)]]
    fit = interact.screen_fit(near)
    pts = interact.footprint_points(bad, fit)
    assert pts and all(interact.on_screen(x, y) for x, y in pts)
    assert (258, 186 + 7 * 30 - 2 * 30) in pts or any(abs(x - 258) <= 1 for x, _ in pts)


def test_log_balance_missing_from_the_scene_list_is_clicked_at_its_tile(course, monkeypatch):
    """An add-on that only lists scenery never shows the log balance (a ground decoration) - the
    log: "Can't see the obstacle to Walk-across ... Nearby: ... Obstacle net ..." over and over.
    Its known tiles are placed on screen from the objects around it."""
    a = runner(course, monkeypatch)
    near = [{"id": 1, "name": "Tree", "ops": ["Chop down"], "size": [1, 1], "tile": [x, y], "dist": 3,
             "screen": [258 + (x - 2474) * 30, 186 - (y - 3437) * 30]}
            for x, y in [(2470, 3437), (2478, 3438), (2472, 3433), (2477, 3432), (2479, 3436)]]
    course.locs = lambda radius=15, name=None: near
    found = a.find(A.GNOME["obstacles"][0], 0)
    assert len(found) == 1 and found[0]["name"] == "Log balance"
    assert found[0]["points"][0] == (258, 186 + 2 * 30)              # (2474, 3435): two tiles north... of us


def test_sweep_covers_the_log_when_the_add_on_cant_place_it():
    near = [{"tile": [x, y], "size": [1, 1], "screen": [258 + (x - 2474) * 30, 186 - (y - 3437) * 30]}
            for x, y in [(2470, 3437), (2478, 3438), (2472, 3433), (2477, 3432), (2475, 3436)]]
    fit = interact.screen_fit(near)
    log = {"id": 2295, "name": "Log balance", "tile": [2474, 3435], "size": [1, 1], "screen": [-1, -1]}
    pts = A.sweep_points(A.GNOME["obstacles"][0], log, fit, {"tile": [2474, 3437]})
    assert pts[0] == (258 - A.SWEEP_STEP, 246 - A.SWEEP_STEP) and len(pts) <= 120
    assert (258, 276) in pts                                 # (2474, 3434), the middle of the log
    assert A.sweep_points(A.GNOME["obstacles"][0], log, None, {"tile": [2474, 3437]}) == []
