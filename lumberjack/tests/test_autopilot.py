from lumberjack.core.gamestate import SKILLS
from lumberjack.nav import autopilot as A


def fresh(**over):
    lv = {s: 1 for s in SKILLS}
    lv["hitpoints"] = 10
    lv.update(over)
    return lv


def test_fresh_account_flow():
    ap = A.Autopilot()
    assert ap.next_step(fresh())[:2] == ("combat", 10)
    assert ap.next_step(fresh(attack=10, strength=10, defence=10))[:2] == ("prayer", 43)
    lv = fresh(attack=10, strength=10, defence=10, prayer=43)
    task, until, minutes, why = ap.next_step(lv)
    assert task == "herblore" and until == 10 and "lowest" in why      # ties: the fastest first
    assert A.milestone("woodcutting", 12, 99) == 15 and A.milestone("woodcutting", 31, 99) == 40
    assert A.milestone("fishing", 38, 39) == 39


def test_rest_and_done():
    ap = A.Autopilot(target=20, skip=["combat", "prayer"])
    lv = fresh()
    ap.done("herblore", False, now=0)
    ap.done("herblore", False, now=0)                                  # twice: rested
    assert ap.next_step(lv, now=10)[0] != "herblore"
    assert ap.next_step(lv, now=A.COOLDOWN_S + 1)[0] == "herblore"
    all20 = {s: 20 for s in SKILLS}
    assert ap.next_step(all20) is None


def test_a_step_runs_until_its_milestone():
    ap = A.Autopilot()
    lv = {s: 15 for s in ("attack", "strength", "defence", "prayer")}
    lv.update(prayer=50)
    task, until, minutes, why = ap.next_step(lv)
    assert minutes is None and until > 1                      # no time cap: to the milestone
