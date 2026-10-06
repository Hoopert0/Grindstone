"""The plan runner (web.server.BotController) with fake bots, travel and game - no game needed."""
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("USERPROFILE", "/tmp")
if "lumberjack.core.input" not in sys.modules:
    _i = types.ModuleType("lumberjack.core.input")
    _i.VK_LEFT, _i.VK_RIGHT, _i.VK_UP, _i.VK_DOWN, _i.VK_SHIFT = 37, 39, 38, 40, 16
    _i.AgentInput = object
    sys.modules["lumberjack.core.input"] = _i

from lumberjack.web import server  # noqa: E402


class FakeBot:
    def __init__(self, script, task):
        self.script, self.task = script, task

    def run(self):
        self.stop_reason = self.script.pop(0) if self.script else "time limit reached"
        if self.stop_reason == "boom":
            raise RuntimeError("boom")


def runner(monkeypatch, reasons, travelled=None):
    ctl = server.BotController()
    ctl.plan_info = {"step": 1, "of": 1}
    built = []

    def build(s):
        built.append(s.task)
        return FakeBot(reasons, s.task)

    class Inp:
        def close(self):
            pass
    monkeypatch.setattr(ctl, "build_bot", build)
    monkeypatch.setattr(ctl, "_ctx", lambda: types.SimpleNamespace(inp=Inp()))
    monkeypatch.setattr(ctl, "_recover", lambda ctx: None)
    monkeypatch.setattr(ctl, "_tidy_backpack", lambda ctx, log, task=None: None)
    monkeypatch.setattr(server, "task_problem", lambda s: None)
    monkeypatch.setattr(server, "load_settings", lambda: server.Settings())
    from lumberjack.nav import places
    monkeypatch.setattr(places, "load", lambda: {"pond": {"tile": [3086, 3228], "plane": 0}})
    monkeypatch.setattr(places, "travel", lambda ctx, gs, place, use_tele=True: (travelled.append(place["tile"])
                                                                               if travelled is not None else None) or True)
    from lumberjack.core import gamestate
    monkeypatch.setattr(gamestate, "shared", lambda: object())
    monkeypatch.setattr(server, "IDLE_PASS_WAIT_S", 0)
    import tempfile
    monkeypatch.setattr(server, "PLAN_RUNNING", Path(tempfile.mkdtemp()) / "plan_running")
    from lumberjack import history
    monkeypatch.setattr(history, "record", lambda *a: {"xp": {}})    # don't write logs/runs.jsonl
    return ctl, built


def test_step_retries_then_succeeds(monkeypatch):
    ctl, built = runner(monkeypatch, ["no fishing spot found after a long search", "boom", "reached level 20 in fishing"])
    travelled = []
    from lumberjack.nav import places
    monkeypatch.setattr(places, "travel", lambda ctx, gs, place, use_tele=True: travelled.append(1) or True)
    import logging
    ctl._run_step(server.PlanStep(task="fishing", minutes=30, level=20, place="pond"), server.Plan(), logging.getLogger("t"))
    assert built == ["fishing"] * 3 and len(travelled) == 3    # recovered + travelled before each attempt


def test_step_gives_up_after_retries(monkeypatch):
    ctl, built = runner(monkeypatch, ["stuck"] * 5)
    import logging
    ctl._run_step(server.PlanStep(task="mining"), server.Plan(), logging.getLogger("t"))
    assert built == ["mining"] * server.STEP_RETRIES


def test_f12_stops_the_plan(monkeypatch):
    ctl, built = runner(monkeypatch, ["F12 pressed"])
    import logging
    ctl._run_step(server.PlanStep(task="combat"), server.Plan(), logging.getLogger("t"))
    assert built == ["combat"] and ctl.stop_event.is_set()


def test_plan_runs_steps_in_order_and_loops(monkeypatch):
    ctl, built = runner(monkeypatch, [])
    plan = server.Plan(steps=[server.PlanStep(task="woodcutting"), server.PlanStep(task="fishing", place="pond")],
                       loop=True)
    real = ctl._run_step

    def counting(step, p, log):
        ok = real(step, p, log)
        if len(built) >= 5:
            ctl.stop_event.set()
        return ok
    monkeypatch.setattr(ctl, "_run_step", counting)
    ctl.plan_info = {"step": 0, "of": 2}
    t = threading.Thread(target=ctl._run_plan, args=(plan,))
    t.start()
    t.join(5)
    assert built[:5] == ["woodcutting", "fishing", "woodcutting", "fishing", "woodcutting"]
    assert ctl.plan_info is None and ctl.error is None


def test_until_text():
    assert server._until_text(server.PlanStep(minutes=30, level=20)) == "until 30 min or level 20"
    assert server._until_text(server.PlanStep(minutes=None)) == "no limit"


def test_watch_level_stall_and_logout(monkeypatch):
    from lumberjack.core import gamestate
    from lumberjack.skills import watch

    class Stop(Exception):
        pass

    state = {"fishing": 19, "xp": 100, "logged_in": True, "calls": 0}

    class GS:
        def player(self, raw=False):
            state["calls"] += 1
            if not state["logged_in"] and state["calls"] > 2:
                state["logged_in"] = True                     # logs back in after a moment
            return {"logged_in": state["logged_in"]}

        def skills(self):
            return {"fishing": {"level": state["fishing"], "xp": state["xp"]}}

    monkeypatch.setattr(gamestate, "shared", lambda: GS())
    monkeypatch.setattr(watch.time, "sleep", lambda s: None)
    clock = [1000.0]
    monkeypatch.setattr(watch.time, "monotonic", lambda: clock[0])
    bot = types.SimpleNamespace(stop_at_level=(["fishing"], 20), stall_limit_s=300, logs_cut=5, state="fishing",
                                stop_event=None)
    watch.plan_checks(bot, Stop)                                # 19 < 20, first marker
    clock[0] += 200
    watch.plan_checks(bot, Stop)                                # no progress yet, but < 300 s
    clock[0] += 200
    try:
        watch.plan_checks(bot, Stop)
        assert False, "should have stalled"
    except Stop as e:
        assert "no progress" in str(e)
    bot.logs_cut, state["fishing"] = 6, 20
    clock[0] += 20
    try:
        watch.plan_checks(bot, Stop)
        assert False, "should have reached the level"
    except Stop as e:
        assert "reached level 20" in str(e)
    state["fishing"], state["logged_in"] = 19, False             # logged out: waits, no stop
    clock[0] += 20
    watch.plan_checks(bot, Stop)
    assert state["logged_in"] and bot.state == "fishing"


def test_random_event_dismissed(monkeypatch):
    from lumberjack.core import interact
    from lumberjack.skills import events
    used = []
    monkeypatch.setattr(interact, "use_option", lambda ctx, gs, pts, verb, name: used.append((verb, name)) or (1, 1))

    class GS:
        def player(self):
            return {"index": 7, "tile": [3200, 3200], "plane": 0}

        def npcs(self, name=None):
            return [{"name": "Chicken", "ops": ["Attack"], "dist": 1, "interacting": 32775, "screen": [1, 1], "body": [1, 1]},
                    {"name": "Sandwich lady", "ops": ["Talk-to", "Dismiss"], "dist": 2, "interacting": 32775,
                     "screen": [1, 1], "body": [1, 1]},
                    {"name": "Genie", "ops": ["Talk-to", "Dismiss"], "dist": 3, "interacting": 32768 + 2,   # someone else's
                     "screen": [1, 1], "body": [1, 1]}]
    bot = types.SimpleNamespace(ctx=types.SimpleNamespace(sleep=lambda s: None))
    assert events.handle(bot, GS())
    assert used == [("Dismiss", "Sandwich lady")] and bot.events_handled == 1


def test_random_event_without_dismiss_is_shaken_off(monkeypatch):
    """This server's events have no Dismiss, but end when we're > 10 tiles away: hop and come back.
    A borrowed id (the Halloween spider is an ordinary spider) counts only when it came for us,
    and never when it's what the bot is fighting."""
    from lumberjack.nav import places
    from lumberjack.skills import events
    hops = []
    monkeypatch.setattr(places, "teleport", lambda ctx, gs, tile, plane=0, camera=True:
                        hops.append((list(tile), plane, camera)) or True)
    me = {"index": 7, "tile": [3200, 3200], "plane": 0}
    npcs = [{"name": "Spider", "id": 61, "ops": ["Attack"], "dist": 6, "interacting": -1},       # just a spider
            {"name": "Genie", "id": 409, "ops": ["Talk-to"], "dist": 2, "interacting": 32775}]

    class GS:
        def player(self):
            return me

        def npcs(self, name=None):
            return npcs
    bot = types.SimpleNamespace(ctx=types.SimpleNamespace(sleep=lambda s: None), state="mining")
    assert events.handle(bot, GS())
    assert hops == [([3215, 3200], 0, False), ([3200, 3200], 0, False)] and bot.state == "mining"
    assert not events.handle(bot, GS())                        # not again straight away
    bot._event_shaken_at = float("-inf")
    npcs[:] = [{"name": "Spider", "id": 61, "ops": ["Attack"], "dist": 1, "interacting": 32775}]
    assert events.after_us(npcs[0], 7) and not events.after_us(npcs[0], 7, skip={"spider"})
    assert not events.after_us({"name": "Spider", "id": 61, "dist": 1, "interacting": -1}, 7)
    assert not events.after_us({"name": "Man", "id": 1, "dist": 1, "interacting": 32775}, 7)


def test_next_step_order_and_skips_reached_steps():
    P, S = server.Plan, server.PlanStep
    plan = P(steps=[S(task="woodcutting", level=30), S(task="fishing"), S(task="mining", level=10)])
    lv = {"woodcutting": 30, "fishing": 5, "mining": 12, "attack": 1, "strength": 1, "defence": 1}
    assert server.next_step(plan, -1, lv) == 1          # woodcutting is done (30), fishing next
    assert server.next_step(plan, 1, lv) == 1           # mining reached 10 too: fishing again
    assert server.next_step(P(steps=[S(task="mining", level=10)]), -1, lv) is None
    low = P(order="lowest", steps=[S(task="woodcutting"), S(task="fishing"), S(task="combat")])
    assert server.next_step(low, -1, lv) == 2           # combat (lowest of att/str/def = 1)
    lv2 = dict(lv, attack=40, strength=40, defence=40)
    assert server.next_step(low, 2, lv2) == 1           # then fishing (5)
    assert server.next_step(low, -1, {}) == 0           # no game data: plain order


def test_recovery_goes_back_to_where_we_died(monkeypatch):
    travelled = []
    ctl, built = runner(monkeypatch, [], travelled)
    bots = iter([{"reason": "moved away (40 tiles) - died or teleported?", "from": {"tile": [3200, 3200], "plane": 0}},
                 {"reason": "time limit reached"}])

    def build(s):
        built.append(s.start_mode)
        b = next(bots)
        bot = FakeBot([b["reason"]], s.task)
        if "from" in b:
            bot.displaced_from = b["from"]
        return bot
    monkeypatch.setattr(ctl, "build_bot", build)
    import logging
    s = server.Settings(task="combat", start_mode="teleport", max_minutes=30)
    assert ctl._run_with_recovery(s, logging.getLogger("t"), "run", first_as_is=True)
    assert built == ["teleport", "here"]                 # first as set up, then from where we are
    assert travelled == [[3200, 3200]]                   # back to where we died


def test_dying_again_and_again_moves_on(monkeypatch):
    """Deaths end a step after DEATH_LIMIT, however long each run lasted (a long run resets the
    failure count, so a too-hard place would otherwise be fought forever)."""
    ctl, built = runner(monkeypatch, ["died - back at the respawn point, 40 tiles from where we were"] * 5)
    import logging
    clock = [0.0]

    def mono():
        clock[0] += 400                                   # every run "went well" for a while
        return clock[0]
    monkeypatch.setattr(server.time, "monotonic", mono)
    assert not ctl._run_with_recovery(server.Settings(task="combat"), logging.getLogger("t"), "run",
                                      first_as_is=True)
    assert len(built) == server.DEATH_LIMIT


def test_death_message_names_the_jump(monkeypatch):
    from lumberjack.skills import watch

    class Stop(Exception):
        pass

    class GS:
        def __init__(self):
            self.n = 0

        def chat(self, n):
            self.n += 1
            return {"count": self.n, "lines": [{"type": 0, "text": "Oh dear, you are dead!"}]}
    gs = GS()
    bot = types.SimpleNamespace(state="fighting")
    watch.check_chat(bot, gs, 0.0, Stop)                      # first look: where we are
    watch.check_moved(bot, {"tile": [3000, 3000]}, 100.0, Stop)
    watch.check_chat(bot, gs, 101.0, Stop)
    assert bot.deaths == 1 and bot.died
    try:                                                      # (respawned close by: still a death)
        watch.check_moved(bot, {"tile": [3005, 3002]}, 110.0, Stop)
        raise AssertionError("no stop")
    except Stop as e:
        assert str(e).startswith("died")
    assert bot.displaced_from == {"tile": [3000, 3000], "plane": 0} and not bot.died


def test_session_counts_recoveries_and_deaths(monkeypatch):
    ctl, built = runner(monkeypatch, ["died - back at the respawn point, 40 tiles from where we were",
                                      "no progress for 6 min", "reached level 10 in mining"])
    import logging
    ctl._new_session()
    assert ctl._run_with_recovery(server.Settings(task="mining"), logging.getLogger("t"), "run", first_as_is=True)
    ss = ctl.session_status()
    assert ss["runs"] == 3 and ss["recoveries"] == 2 and ss["last_problem"] == "no progress for 6 min"
    assert ctl.session_status()["elapsed"] >= 0


def test_long_waits_keep_the_game_awake():
    """The client logs out after 5 minutes without mouse or keys: a long wait (traps out, rocks
    growing back, paused) gets a tiny wiggle well before that, and nothing sooner."""
    from lumberjack.skills import watch
    nudges = []
    inp = types.SimpleNamespace(last_input=1000.0, nudge=lambda: nudges.append(1))
    bot = types.SimpleNamespace(inp=inp)
    assert not watch.keep_awake(bot, 1000.0 + 60)
    assert watch.keep_awake(bot, 1000.0 + watch.IDLE_NUDGE_S + 1) and nudges == [1]
    assert watch.IDLE_NUDGE_S < 300


def test_plan_waits_for_the_login_instead_of_failing(monkeypatch):
    """Started before the game was up / at the login screen: wait (nothing counted), then go."""
    from lumberjack.core import gamestate
    ctl = server.BotController()
    states = iter([None, {"logged_in": False}, {"logged_in": True, "tile": [3222, 3218]}])

    class GS:
        def player(self, raw=False):
            return self.cur
    gs = GS()

    def shared():
        st = next(states)
        if st is None:
            return None
        gs.cur = st
        return gs
    monkeypatch.setattr(gamestate, "shared", shared)
    monkeypatch.setattr(ctl, "_sleep", lambda s: None)
    import logging
    assert ctl._wait_ready(logging.getLogger("t"))


def test_retries_share_the_time_limit(monkeypatch):
    ctl, built = runner(monkeypatch, [])
    seen = []
    script = ["stuck", "time limit reached"]
    monkeypatch.setattr(ctl, "build_bot", lambda s: seen.append(s.max_minutes) or FakeBot(script, s.task))
    import logging
    ctl._run_with_recovery(server.Settings(max_minutes=30), logging.getLogger("t"), "run", first_as_is=True)
    assert 29.9 < seen[0] <= 30 and seen[1] < seen[0]


def test_watch_moved_away():
    from lumberjack.skills import watch

    class Stop(Exception):
        pass
    bot = types.SimpleNamespace(state="fishing")
    watch.check_moved(bot, {"tile": [3086, 3228]}, 100.0, Stop)
    watch.check_moved(bot, {"tile": [3090, 3230]}, 110.0, Stop)       # walked a bit: fine
    try:
        watch.check_moved(bot, {"tile": [3222, 3218]}, 120.0, Stop)   # Lumbridge: died
        raise AssertionError("no stop")
    except Stop:
        pass
    assert bot.displaced_from == {"tile": [3090, 3230], "plane": 0}
    bot2 = types.SimpleNamespace(state="teleporting")
    watch.check_moved(bot2, {"tile": [3086, 3228]}, 100.0, Stop)
    bot2.state = "fishing"
    watch.check_moved(bot2, {"tile": [3222, 3218]}, 110.0, Stop)      # our own teleport: fine
    watch.check_moved(bot2, {"tile": [3240, 3240]}, 140.0, Stop)      # 22 tiles in 30 s: walked
    # a ::tele between two checks (the state never said "teleporting" at a check): ours, fine
    from lumberjack.nav import places
    bot3 = types.SimpleNamespace(state="raking weeds")
    watch.check_moved(bot3, {"tile": [3254, 3189]}, 100.0, Stop)
    places.teleported_at = 104.0
    try:
        watch.check_moved(bot3, {"tile": [3054, 3307]}, 108.0, Stop)
        watch.check_moved(bot3, {"tile": [3055, 3307]}, 113.0, Stop)
    finally:
        places.teleported_at = float("-inf")


def test_tidy_banks_what_the_next_task_does_not_use(monkeypatch):
    from lumberjack import actions, bank
    from lumberjack.core import backpack
    names = ["bronze_axe", "small_fishing_net", "bronze_pickaxe", "raw_shrimps", "bones", "cowhide",
             "tinderbox", "mystery_item", "bronze_sword", "pot"] + [None] * 18
    monkeypatch.setattr(backpack, "slots", lambda: [{"id": 1 if n else -1, "key": n} for n in names])
    trips, drops = [], []
    monkeypatch.setattr(bank, "gs_bank_trip", lambda ctx, keep_slots=(): trips.append(set(keep_slots)) or True)
    monkeypatch.setattr(actions, "drop_all", lambda ctx, keep=(): drops.append(set(keep)))
    import logging
    ctl = server.BotController()
    ctl._tidy_backpack(None, logging.getLogger("t"), "mining")
    assert trips == [{2}]                                # only the pickaxe stays
    monkeypatch.setattr(bank, "gs_bank_trip", lambda ctx, keep_slots=(): False)
    ctl._tidy_backpack(None, logging.getLogger("t"), "mining")
    assert drops == [{0, 1, 2, 6, 7}]                    # no bank: drop only products, loot, starter kit


def test_tidy_drops_a_few_leftovers_instead_of_a_bank_trip(monkeypatch):
    from lumberjack import actions, bank
    from lumberjack.core import backpack, gamestate
    names = ["bronze_pickaxe", "logs", "oak_logs"] + [None] * 25
    monkeypatch.setattr(backpack, "slots", lambda: [{"id": 1 if n else -1, "key": n} for n in names])
    monkeypatch.setattr(gamestate, "shared", lambda: object())
    monkeypatch.setattr(bank, "gs_bank_trip", lambda ctx, keep_slots=(): pytest.fail("a bank trip for 2 logs"))
    dropped = []
    monkeypatch.setattr(actions, "drop_known", lambda ctx, gs, slots: dropped.extend(slots))
    import logging
    server.BotController()._tidy_backpack(None, logging.getLogger("t"), "mining")
    assert dropped == [1, 2]


def test_resume_marker(monkeypatch, tmp_path):
    marker = tmp_path / "plan_running"
    monkeypatch.setattr(server, "PLAN_RUNNING", marker)
    started = []
    monkeypatch.setattr(server, "load_plan", lambda: server.Plan(steps=[server.PlanStep()]))
    monkeypatch.setattr(server.ctl, "start_plan", lambda p, after=-1, ends_at=None: started.append(after) or None)
    from lumberjack.core import gamestate
    monkeypatch.setattr(gamestate, "shared", lambda: object())
    server.resume_plan()                                 # no marker: nothing
    marker.write_text('{"version": "v1", "last": 0}')
    server.resume_plan()
    for _ in range(50):
        if started:
            break
        threading.Event().wait(0.05)
    assert started == [0]                                # carries on after step 1


def test_plan_resumes_after_a_step(monkeypatch):
    ctl, built = runner(monkeypatch, [])
    plan = server.Plan(steps=[server.PlanStep(task="woodcutting"), server.PlanStep(task="fishing"),
                              server.PlanStep(task="mining")], loop=False)
    monkeypatch.setattr(server, "PLAN_RUNNING", Path("/tmp/claude-0/plan_running_test"))
    ctl.plan_info = {"step": 0, "of": 3}
    ctl._run_plan(plan, after=0)
    assert built == ["fishing", "mining"]


def test_history_gains_and_summary(monkeypatch, tmp_path):
    from lumberjack import history
    monkeypatch.setattr(history, "FILE", tmp_path / "runs.jsonl")
    before = {"fishing": [20, 4500], "cooking": [10, 1200], "mining": [5, 400]}
    after = {"fishing": [21, 5100], "cooking": [10, 1300], "mining": [5, 400]}
    assert history.gains(before, after) == ({"fishing": 600, "cooking": 100}, {"fishing": [20, 21]})
    history.record("fishing", "pond", 1000.0, before, after, "time limit reached")
    history.record("mining", None, 2000.0, after, after, "crashed: boom")
    h = history.summary(hours=1, now=2500.0)
    assert h["runs"] == 2 and h["xp"] == {"fishing": 600, "cooking": 100} and h["problems"] == 1
    assert h["recent"][0]["task"] == "mining" and h["levels"] == {"fishing": [20, 21]}


def test_training_route_pick():
    from lumberjack.nav import training
    assert training.pick("woodcutting", 1)[1]["trees"] == ["tree"]
    assert training.pick("woodcutting", 29)[1]["trees"] == ["oak", "tree"]
    assert training.pick("woodcutting", 44) == ("★ Draynor willows", {"trees": ["willow"], "auto_trees": False})
    assert training.pick("woodcutting", 70)[0] == "★ Edgeville yews"
    assert training.pick("fishing", None)[0] == "★ Draynor fishing"
    assert training.pick("firemaking", 50) == (None, {})
    for task, tiers in training.ROUTES.items():          # every route's place exists, tiers ascend
        assert all(p is None or p in training.STARTER_PLACES for _, p, _ in tiers)
        assert [t[0] for t in tiers] == sorted(t[0] for t in tiers)


def test_places_merge_builtin_and_saved(monkeypatch, tmp_path):
    from lumberjack.nav import places
    monkeypatch.setattr(places, "PLACES", tmp_path / "places.json")
    places.save_all({"★ Draynor willows": {"tile": [3088, 3233], "plane": 0}, "home": {"tile": [1, 2], "plane": 0}})
    p = places.load()
    assert p["★ Draynor willows"]["tile"] == [3088, 3233] and "builtin" not in p["★ Draynor willows"]
    assert p["★ Lumbridge cows"]["builtin"] and "home" in p
    places.delete("home")
    assert "home" not in places.load() and "★ Lumbridge cows" not in places.load_saved()


def test_auto_step_moves_up_a_tier(monkeypatch):
    travelled = []
    ctl, built = runner(monkeypatch, [], travelled)
    from lumberjack.nav import places
    monkeypatch.setattr(places, "load", lambda: {k: dict(v) for k, v in __import__(
        "lumberjack.nav.training", fromlist=["x"]).STARTER_PLACES.items()})
    lv = {"woodcutting": 14}
    monkeypatch.setattr(server, "_levels", lambda: dict(lv))
    script, seen = ["reached level 15 in woodcutting", "time limit reached"], []

    def build(s):
        seen.append((list(s.trees), s.max_minutes is not None))
        lv["woodcutting"] = 15
        return FakeBot(script, s.task)
    monkeypatch.setattr(ctl, "build_bot", build)
    import logging
    ok = ctl._run_step(server.PlanStep(task="woodcutting", minutes=30, place="auto",
                                       options={"when_full": "bank", "bogus": 1}), server.Plan(), logging.getLogger("t"))
    assert ok and seen == [(["tree"], True), (["oak", "tree"], True)]
    assert travelled == [[3192, 3236], [3192, 3236]]


def test_check_place_ok_moved_missing(monkeypatch, tmp_path):
    from lumberjack.core import interact
    from lumberjack.nav import places, training
    monkeypatch.setattr(places, "PLACES", tmp_path / "places.json")
    monkeypatch.setattr(places, "travel", lambda ctx, gs, place, use_tele=True: True)
    monkeypatch.setattr(training, "SETTLE_S", 0)
    ctx = types.SimpleNamespace(sleep=lambda s: None)

    class GS:
        def __init__(self, cows):
            self.me, self.cows = [3258, 3276], cows

        def npcs(self, name=None):
            return [{"name": "Cow", "ops": ["Attack"], "tile": t,
                     "dist": max(abs(t[0] - self.me[0]), abs(t[1] - self.me[1]))} for t in self.cows]

        def player(self):
            return {"tile": list(self.me), "plane": 0}
    tiers = [("combat", 5, {"targets": ["cow"]})]
    assert training.check_place(ctx, GS([[3260, 3280]]), "★ Lumbridge cows", tiers)["status"] == "ok"
    gs = GS([[3280, 3300]])                                # 24 tiles off: walk there, save
    monkeypatch.setattr(interact, "walk_to_tile", lambda ctx, g, t, arrive=2, max_clicks=30: g.me.__setitem__(
        slice(None), [t[0] - 2, t[1]]) or True)
    r = training.check_place(ctx, gs, "★ Lumbridge cows", tiers)
    assert r["status"] == "moved" and places.load()["★ Lumbridge cows"]["tile"] == [3278, 3300]
    assert training.check_place(ctx, GS([]), "★ Lumbridge chickens",
                                [("combat", 1, {"targets": ["chicken"]})])["status"] == "missing"


def test_places_check_endpoint_takes_no_body(monkeypatch):
    from fastapi.testclient import TestClient
    from lumberjack.core import gamestate
    monkeypatch.setattr(gamestate, "shared", lambda: None)
    c = TestClient(server.app)
    r = c.post("/api/places/check")
    assert r.status_code == 400 and "game's own data" in r.json()["error"]
    assert c.get("/api/places/check").json() == {"running": False, "results": []}


def test_firemaking_spawns_best_logs(monkeypatch):
    from lumberjack import items
    from lumberjack.core import backpack, gamestate
    from lumberjack.skills.firemaking_task import Firemaker
    fm = Firemaker.__new__(Firemaker)
    fm.blocked, fm.current, fm.state = set(), None, ""
    fm.ctx = types.SimpleNamespace(sleep=lambda s: None)
    monkeypatch.setattr(gamestate, "skill", lambda name: {"base": 33})
    assert fm.best_log() == "willow_logs"
    fm.blocked.add("willow_logs")
    assert fm.best_log() == "oak_logs"
    inv = [{"id": 590, "key": "tinderbox"}] + [{"id": -1, "key": None}] * 27
    monkeypatch.setattr(backpack, "slots", lambda: inv)
    asked = []

    def spawn(ctx, key, amount=1):                       # the game gives one per command
        asked.append(amount)
        inv[inv.index(next(s for s in inv if s["id"] < 0))] = {"id": 1521, "key": key}
    monkeypatch.setattr(items, "spawn", spawn)
    assert fm.spawn_load() and fm.current == "oak_logs"
    assert sum(1 for s in inv if s["key"] == "oak_logs") == 27 and asked[0] == 27 and asked[-1] == 1
    assert fm.non_log_slots() == {0}


def test_plan_hours_limit(monkeypatch):
    import time as _t
    ctl, built = runner(monkeypatch, [])
    plan = server.Plan(steps=[server.PlanStep(task="woodcutting", minutes=None)], loop=True, max_hours=1)
    seen = []
    real = ctl._run_step

    def step(st, p, log):
        seen.append(st.minutes)
        ctl.plan_ends_at = _t.time() - 1               # time's up after this step
        return real(st, p, log)
    monkeypatch.setattr(ctl, "_run_step", step)
    ctl.plan_info = {"step": 0, "of": 1}
    ctl.plan_ends_at = _t.time() + 3600
    ctl._run_plan(plan)
    assert len(seen) == 1 and 59 < seen[0] <= 60        # the step got the plan's remaining hour


def test_combat_defaults_to_spawned_food():
    from lumberjack.skills import combat
    assert combat.DEFAULT_FOOD in __import__("lumberjack.items", fromlist=["x"]).BY_KEY


def test_cooking_task_checks(monkeypatch):
    monkeypatch.setattr(server, "_names_from_game", lambda: False)
    s = server.Settings(task="cooking")
    assert "game's own data" in server.task_problem(s)
    cooking = lambda: [e for e in server.preflight(s)["errors"] if "Cooking" in e]
    assert cooking()
    monkeypatch.setattr(server, "_names_from_game", lambda: True)
    assert server.task_problem(s) is None and not cooking()
    assert server.task_problem(s.model_copy(update={"spawn_tools": False}))


def test_autopilot_runs_its_picks(monkeypatch):
    from lumberjack.core.gamestate import SKILLS
    ctl, built = runner(monkeypatch, [])
    lv = {s: 1 for s in SKILLS}
    monkeypatch.setattr(server, "_levels", lambda: dict(lv))
    from lumberjack.nav import places, training
    monkeypatch.setattr(places, "load", lambda: dict(training.STARTER_PLACES))
    steps = []

    seen_opts = []

    def step(st, p, log):
        steps.append((st.task, st.level, st.place))
        seen_opts.append(st.options)
        if st.task == "combat":
            lv.update(attack=10, strength=10, defence=10)
        elif st.task == "prayer":
            lv["prayer"] = 43
        if len(steps) >= 3:
            ctl.stop_event.set()
        return True
    monkeypatch.setattr(ctl, "_run_step", step)
    ctl.plan_info = {}
    ctl._run_plan(server.Plan(autopilot=True, target=99))
    assert steps[0] == ("combat", 10, "auto") and steps[1] == ("prayer", 43, None)
    assert seen_opts[0]["when_full"] == "drop" and seen_opts[0]["loot"] == []
    assert steps[2][0] == "herblore" and ctl.plan_info is None


def test_login_settings_keep_the_password(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "LOGIN_FILE", tmp_path / "login.json")
    server.login_set(server.LoginIn(user="hero", password="secret", enabled=True))
    assert server.login_get() == {"user": "hero", "enabled": True, "has_password": True}
    server.login_set(server.LoginIn(user="hero", password=None, enabled=False))   # untouched password
    assert server.load_login()["password"] == "secret" and not server.load_login()["enabled"]


def test_auto_login_logs_in_at_the_login_screen(monkeypatch, tmp_path):
    from lumberjack.core import gamestate
    monkeypatch.setattr(server, "LOGIN_FILE", tmp_path / "login.json")
    server.login_set(server.LoginIn(user="hero", password="secret", enabled=True))
    state = {"s": 10, "calls": []}

    def q(self, cmd):
        if cmd == "tick":
            return {"loop": 1, "state": state["s"]}
        if cmd.startswith("login "):
            state["calls"].append(cmd)
            state["s"] = 30
            return {"ok": True}
        if cmd == "loginstatus":
            return {"state": state["s"]}
        if cmd.startswith("widgets "):               # the welcome screen, gone once clicked
            if cmd == "widgets click here to play" and not state.get("played"):
                return [{"x": 340, "y": 330, "w": 90, "h": 40, "text": "CLICK HERE TO PLAY"}]
            return []
        raise AssertionError(cmd)
    monkeypatch.setattr(gamestate.GameState, "_q", q)
    from lumberjack.core import input as inp

    class FakeInput:
        def click(self, x, y):
            state["played"] = (x, y)
    monkeypatch.setattr(inp, "AgentInput", FakeInput)
    ticks = {"n": 0}

    def sleep(s):
        ticks["n"] += 1
        if ticks["n"] > 12:
            raise Stop
    monkeypatch.setattr(server.time, "sleep", sleep)
    with __import__("pytest").raises(Stop):
        server.auto_login()
    import base64
    assert len(state["calls"]) == 1
    _, u, p = state["calls"][0].split(" ")
    assert base64.b64decode(u).decode() == "hero" and base64.b64decode(p).decode() == "secret"
    x, y = state["played"]                          # then past the welcome screen
    assert 330 <= x <= 400 and 345 <= y <= 355


class Stop(Exception):
    pass


def test_failed_run_at_a_route_place_rechecks_it_once(monkeypatch):
    ctl, built = runner(monkeypatch, ["no fishing spot found", "no fishing spot found", "reached level 20 in fishing"])
    from lumberjack.nav import places, training
    saved = {"★ Draynor fishing": {"tile": [3086, 3228], "plane": 0}}
    monkeypatch.setattr(places, "load", lambda: saved)
    travelled, checks = [], []
    monkeypatch.setattr(places, "travel", lambda ctx, gs, place, use_tele=True: travelled.append(place["tile"]) or True)

    def check(ctx, gs, name, tiers, fix=True, log=None):
        checks.append(name)
        saved[name] = {"tile": [3090, 3232], "plane": 0}          # the check moved the place
        return {"status": "moved"}
    monkeypatch.setattr(training, "check_place", check)
    import logging
    ctl._run_step(server.PlanStep(task="fishing", minutes=30, level=20, place="★ Draynor fishing"),
                  server.Plan(), logging.getLogger("t"))
    assert checks == ["★ Draynor fishing"]                         # once per step, not every failure
    assert travelled == [[3086, 3228], [3090, 3232], [3090, 3232]]


def test_every_task_builds_its_bot(monkeypatch):
    """Each task the panel offers turns into a bot (constructor arguments line up)."""
    import sys
    from lumberjack.skills import base, woodcutting  # noqa: F401 - loaded so they get the fakes
    for name, mod in list(sys.modules.items()):
        if name.startswith("lumberjack.skills.") and hasattr(mod, "GameWindow"):
            monkeypatch.setattr(mod, "GameWindow", lambda *a, **k: None)
            monkeypatch.setattr(mod, "AgentInput", lambda *a, **k: types.SimpleNamespace(close=lambda: None))
    ctl = server.BotController()
    names = {}
    for task in list(server.TASK_LEVEL_SKILLS) + ["woodcutting"]:
        bot = ctl.build_bot(server.Settings(task=task, map=None))
        names[task] = getattr(bot, "name", "woodcutting")
    for task in ("herblore", "runecrafting", "agility", "hunter", "fishing", "thieving"):
        assert names[task] == task


def test_autopilot_surfaces_before_a_placeless_step(monkeypatch):
    from lumberjack.core.gamestate import SKILLS
    ctl, built = runner(monkeypatch, [])
    lv = {s: 1 for s in SKILLS}
    lv.update(attack=10, strength=10, defence=10)
    monkeypatch.setattr(server, "_levels", lambda: dict(lv))
    monkeypatch.setattr(server, "_my_tile", lambda: (3109, 9835))      # still in the Edgeville dungeon
    steps = []

    def step(st, p, log):
        steps.append((st.task, st.place))
        ctl.stop_event.set()
        return True
    monkeypatch.setattr(ctl, "_run_step", step)
    ctl.plan_info = {}
    ctl._run_plan(server.Plan(autopilot=True, target=99))
    assert steps == [("prayer", "★ Lumbridge")]


def test_skill_check_runs_each_task_once_and_reports(monkeypatch, tmp_path):
    ctl, built = runner(monkeypatch, ["time limit reached", "stuck: no anvil nearby"])
    monkeypatch.setattr(server, "LOGS_DIR", tmp_path)
    from lumberjack import history
    xp = iter([{"woodcutting": 350}, {}])
    monkeypatch.setattr(history, "record", lambda *a: {"xp": next(xp), "minutes": 2})
    assert ctl.start_check(minutes=0.01, tasks=["woodcutting", "smithing"]) is None
    ctl.thread.join(10)
    assert built == ["woodcutting", "smithing"]                 # one try each, no retries
    report = (tmp_path / "skill_check.txt").read_text()
    assert "1 of 2 earned XP" in report
    assert "OK   woodcutting" in report and "+350 woodcutting" in report
    assert "FAIL smithing" in report and "no anvil nearby" in report
    assert "skill check done" in ctl.end_reason and ctl.check_results is None


def test_autopilot_resumes_as_autopilot(monkeypatch, tmp_path):
    """Autopilot isn't the saved plan: the marker carries it, so a panel restart resumes it."""
    marker = tmp_path / "plan_running"
    monkeypatch.setattr(server, "PLAN_RUNNING", marker)
    monkeypatch.setattr(server, "load_plan", lambda: server.Plan(steps=[server.PlanStep(task="mining")]))
    monkeypatch.setattr(server, "_running_plan", server.Plan(autopilot=True, target=70, skip=["agility"]))
    server._mark_running(-1)
    started = []
    monkeypatch.setattr(server.ctl, "start_plan", lambda p, after=-1, ends_at=None: started.append(p) or None)
    from lumberjack.core import gamestate
    monkeypatch.setattr(gamestate, "shared", lambda: object())
    server.resume_plan()
    for _ in range(50):
        if started:
            break
        threading.Event().wait(0.05)
    assert started and started[0].autopilot and started[0].target == 70 and started[0].skip == ["agility"]


def test_single_task_resumes_with_its_time_left(monkeypatch, tmp_path):
    marker = tmp_path / "plan_running"
    monkeypatch.setattr(server, "PLAN_RUNNING", marker)
    s = server.Settings(task="fishing", max_minutes=60)
    server._mark_single(s, time.time() + 30 * 60)              # 30 of its 60 minutes left
    started = []
    monkeypatch.setattr(server.ctl, "start", lambda st: started.append(st) or None)
    from lumberjack.core import gamestate
    monkeypatch.setattr(gamestate, "shared", lambda: object())
    server.resume_plan()
    for _ in range(50):
        if started:
            break
        threading.Event().wait(0.05)
    assert started and started[0].task == "fishing" and 29 < started[0].max_minutes <= 30


def test_off_the_surface_map():
    from lumberjack.nav import training
    assert training.underground((2841, 4829))           # air altar room
    assert training.underground((3109, 9835))           # Edgeville dungeon
    assert not training.underground((2474, 3437))       # Gnome agility course
    assert not training.underground((3054, 3307))       # Falador farm


def test_chat_logs_problems_and_stops_on_repeats():
    from lumberjack.skills import watch

    class Stop(Exception):
        pass

    class GS:
        def __init__(self):
            self.lines, self.count = [], 0

        def say(self, text, type_=0):
            self.lines.insert(0, {"type": type_, "text": text})
            self.count += 1

        def chat(self, n=20):
            return {"count": self.count, "lines": self.lines[:n]}
    gs, bot = GS(), types.SimpleNamespace()
    gs.say("Welcome to 2009Scape.")
    watch.check_chat(bot, gs, 0.0, Stop)                      # first look: just where we are
    gs.say("You manage to mine some copper.")
    gs.say("hello", type_=2)                                  # someone talking: ignored
    for i in range(watch.REPEAT_LIMIT - 1):
        gs.say("<col=ff0000>You need a Mining level of 15 to mine this rock.")
        watch.check_chat(bot, gs, 10.0 + i, Stop)            # repeating, not yet too often
    gs.say("You need a Mining level of 15 to mine this rock.")
    with __import__("pytest").raises(Stop, match="keeps saying"):
        watch.check_chat(bot, gs, 20.0, Stop)
    bot2 = types.SimpleNamespace()
    watch.check_chat(bot2, gs, 0.0, Stop)
    for i in range(watch.REPEAT_LIMIT + 2):                   # spread out: never too often
        gs.say("You can't light a fire here.")
        watch.check_chat(bot2, gs, 100.0 * i, Stop)
