"""The plan runner (web.server.BotController) with fake bots, travel and game - no game needed."""
import os
import sys
import threading
import types
from pathlib import Path

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
        def player(self):
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
    from lumberjack.skills import watch
    used = []
    monkeypatch.setattr(interact, "use_option", lambda ctx, gs, pts, verb, name: used.append((verb, name)) or (1, 1))

    class GS:
        def player(self):
            return {"index": 7}

        def npcs(self, name=None):
            return [{"name": "Chicken", "ops": ["Attack"], "dist": 1, "interacting": 32775, "screen": [1, 1], "body": [1, 1]},
                    {"name": "Sandwich lady", "ops": ["Talk-to", "Dismiss"], "dist": 2, "interacting": 32775,
                     "screen": [1, 1], "body": [1, 1]},
                    {"name": "Genie", "ops": ["Talk-to", "Dismiss"], "dist": 3, "interacting": 32768 + 2,   # someone else's
                     "screen": [1, 1], "body": [1, 1]}]
    bot = types.SimpleNamespace(ctx=types.SimpleNamespace(sleep=lambda s: None))
    assert watch.dismiss_random_event(bot, GS())
    assert used == [("Dismiss", "Sandwich lady")]


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


def test_tidy_banks_what_the_next_task_does_not_use(monkeypatch):
    from lumberjack import actions, bank
    from lumberjack.core import backpack
    names = ["bronze_axe", "small_fishing_net", "bronze_pickaxe", "raw_shrimps", "bones", "cowhide",
             "tinderbox", "mystery_item"] + [None] * 20
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
    assert drops == [{0, 1, 2, 6, 7}]                    # no bank: drop only products + loot


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
    assert training.pick("woodcutting", 45) == ("★ Draynor willows", {"trees": ["willow"], "auto_trees": False})
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
    assert steps[2][0] == "magic" and ctl.plan_info is None


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
