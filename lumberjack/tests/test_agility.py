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


def test_leave_teleports_back(course, monkeypatch):
    a = runner(course, monkeypatch)
    course.tile = (2474, 3437)
    a.leave()
    assert course.tile == (3222, 3218)


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
