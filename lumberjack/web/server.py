"""Grindstone control panel - a local web UI for running and watching the bot.

    %USERPROFILE%\\.venvs\\lumberjack\\Scripts\\python.exe -m lumberjack.web.server
    then open http://127.0.0.1:8765

Only listens on 127.0.0.1, so it's reachable from this PC only.
"""
import asyncio
import collections
import json
import random
import logging
import threading
import time
from pathlib import Path

import cv2
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from lumberjack import version
from lumberjack.core.window import GameWindow
from lumberjack.ui import inventory
from lumberjack.vision import trees

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parents[0] / "configs" / "woodcutting.json"
PORT = 8765

# Tree types the bot knows. "ready" = hover-text templates exist, so the bot can verify it.
TREE_TYPES = [
    {"id": "tree", "name": "Tree", "level": 1},
    {"id": "oak", "name": "Oak", "level": 15},
    {"id": "willow", "name": "Willow", "level": 30},
    {"id": "maple", "name": "Maple", "level": 45},
    {"id": "yew", "name": "Yew", "level": 60},
    {"id": "magic", "name": "Magic", "level": 75},
]
FULL_ACTIONS = [
    {"id": "drop", "name": "Drop logs", "ready": True},
    {"id": "burn", "name": "Burn (Firemaking)", "ready": True},
    {"id": "fletch", "name": "Fletch (knife)", "ready": True},
    {"id": "bank", "name": "Bank", "ready": True},
]


LOG_TYPES = [
    {"id": "logs", "name": "Logs", "level": 1},
    {"id": "oak_logs", "name": "Oak logs", "level": 15},
    {"id": "willow_logs", "name": "Willow logs", "level": 30},
    {"id": "maple_logs", "name": "Maple logs", "level": 45},
    {"id": "yew_logs", "name": "Yew logs", "level": 60},
    {"id": "magic_logs", "name": "Magic logs", "level": 75},
]


class Settings(BaseModel):
    task: str = "woodcutting"         # "autopilot" | "woodcutting" | "fishing" | ... (see TASKS in index.html)
    autopilot_target: int = 99        # autopilot: every skill to this level
    autopilot_skip: list[str] = []    # autopilot: tasks to leave out
    fm_logs: list[str] = ["willow_logs", "oak_logs", "logs"]
    burn_spot: str | None = None
    trees: list[str] = ["tree"]   # allowed types; the bot chops the best one available
    when_full: str = "drop"
    drop_at: int = 28
    max_minutes: float | None = None
    max_logs: int | None = None
    map: str | None = None            # recorded map for walking between spots
    spots: list[str] | None = None    # spots the bot may use (None = all that fit the trees)
    start_mode: str = "here"          # "here" | "teleport" | "spot"
    start_spot: str | None = None     # with start_mode "spot": where we're standing
    chop_spot: str | None = None      # walk here before chopping
    bank_spot: str | None = None      # with when_full "bank" (None = the map's first bank)
    keep_carried: bool = True         # never drop/bank what's in the backpack at Start (axe...)
    clear_at_start: bool = True       # drop logs/ore/fish/loot at Start (tools stay) - not firemaking
    recover: bool = True              # with game data: a crash/stall/death restarts the run, not ends it
    auto_trees: bool = False          # choose trees + spot from the Stats-tab levels
    spawn_tools: bool = True          # spawn a missing axe/knife/tinderbox with ::item (singleplayer admin)
    # fishing task
    fish_method: str = "net"
    auto_fish: bool = False           # best method for the Fishing level (of the learned ones)
    fish_spot: str | None = None      # walk here to fish (and back after a bank trip)
    cook: bool = True                 # cook the catch on a fire (chop a log + light it)
    cooked_action: str = "drop"       # "drop" | "bank" the cooked fish
    fire_trees: list[str] = ["willow", "oak", "tree"]
    # mining task
    ores: list[str] = ["copper", "tin"]
    auto_ores: bool = False           # best ore for the Mining level (of the ones with a spot)
    mine_spot: str | None = None
    mine_full: str = "drop"           # "drop" | "bank"
    # combat task
    targets: list[str] = ["chicken"]
    foods: list[str] = []
    eat_below: int = 50               # % of max HP
    train: str = "auto"               # "auto" | "attack" | "strength" | "defence"
    fight_spot: str | None = None
    loot: list[str] = []              # ground items to pick up after a kill
    bury_bones: bool = False
    # thieving task
    thieve: list[str] = ["man", "woman"]      # NPCs to pickpocket


class SpotIn(BaseModel):
    map: str
    name: str
    trees: list[str] = ["tree"]
    kind: str = "trees"               # "trees" | "bank" | "fish" | "rocks" | "combat"


class FishLearnIn(BaseModel):
    what: str                         # "spot" | "fire" | "item" | "cook"
    slot: int | None = None           # with "item": backpack slot, 1-28
    name: str | None = None           # with "item": e.g. raw_shrimps


class PlanStep(BaseModel):
    task: str = "woodcutting"
    minutes: float | None = 30        # stop the step after this long (None = no limit)
    level: int | None = None          # ... or once the task's skill reaches this level
    place: str | None = None          # travel here first (a saved place, or "auto": by level)
    options: dict = {}                # Task-tab settings for this step only, e.g. {"trees": ["oak"]}


class Plan(BaseModel):
    steps: list[PlanStep] = []
    loop: bool = True                 # start over after the last step
    teleport: bool = True             # travel with ::tele (singleplayer admin), else walk
    order: str = "in order"           # "in order" | "lowest": next = the step whose skill is lowest
    resume: bool = True               # running when the panel closed (update, crash) -> start again
    auto_update: bool = False         # between steps: get a newer version from GitHub, restart, resume
    max_hours: float | None = None    # stop the whole plan after this long (None = never)
    autopilot: bool = False           # ignore the steps: nav.autopilot picks what to train next
    target: int = 99                  # autopilot: train every skill to this level
    skip: list[str] = []              # autopilot: tasks to leave out
    check: bool = False               # skill check: one short try per step, then a report


class PlaceIn(BaseModel):
    name: str


class LearnIn(BaseModel):
    what: str                         # mining: "rock" | "test";  combat: "npc" | "loot" | "bury" | "test"
    name: str | None = None           # with "npc"/"loot": e.g. chicken, bones


class SpawnIn(BaseModel):
    key: str
    amount: int = 1


class RecordIn(BaseModel):
    map: str
    extend: bool = True


class CalibrateIn(BaseModel):
    name: str


PLAN = HERE.parents[0] / "configs" / "plan.json"
PLAN_RUNNING = HERE.parents[0] / "configs" / "plan_running"   # exists while a plan runs
UPDATE_RETRY_S = 3600                 # a failed self-update is tried again after this long
RESUME_WAIT_S = 1800                  # after a panel restart, wait this long for the game to resume
# the skill(s) a plan step's "until level" refers to
# the skills Grindstone can train (autopilot's progress counts these)
TRAINED_SKILLS = ["attack", "strength", "defence", "hitpoints", "ranged", "prayer", "magic", "cooking",
                  "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining", "herblore",
                  "runecrafting", "agility", "hunter", "slayer", "farming",
                  "thieving"]
TASK_LEVEL_SKILLS = {"woodcutting": ["woodcutting"], "firemaking": ["firemaking"], "fishing": ["fishing"],
                     "mining": ["mining"], "combat": ["attack", "strength", "defence"], "cooking": ["cooking"],
                     "prayer": ["prayer"], "fletching": ["fletching"], "crafting": ["crafting"],
                     "herblore": ["herblore"], "runecrafting": ["runecrafting"], "agility": ["agility"],
                     "hunter": ["hunter"], "slayer": ["slayer"],
                     "farming": ["farming"], "thieving": ["thieving"], "smithing": ["smithing"], "ranged": ["ranged"],
                     "magic": ["magic"]}
# tasks that spawn their own supplies: need game data + 'Spawn missing tools'
SPAWN_TASKS = {"cooking": "raw fish", "prayer": "bones", "fletching": "logs and a knife",
               "crafting": "uncut gems and a chisel", "smithing": "bars and a hammer", "magic": "runes",
               "herblore": "unfinished potions and their ingredients", "runecrafting": "pure essence"}
STEP_RETRIES = 3                      # attempts per step before moving on
CHECK_MINUTES = 2                     # skill check: each task this long
CHECK_TASKS = ["woodcutting", "fishing", "mining", "combat", "ranged", "magic", "thieving", "slayer",
               "agility", "hunter", "firemaking", "cooking", "prayer", "fletching", "crafting", "smithing",
               "herblore", "runecrafting", "farming"]
STALL_LIMIT_S = 360                   # a step with no progress/XP this long is restarted
RESET_AFTER_S = 600                   # a run that lasted this long counts as having gone well
TIDY_SPARE = 4                        # this many items the next task doesn't use -> worth a bank trip
IDLE_PASS_WAIT_S = 60                 # a whole plan pass where nothing worked: wait before the next
GOOD_ENDS = ("time limit reached", "reached")   # stop reasons that mean the step is done


def _err_text(e):
    """An exception as a readable line: a bare KeyError reads "'tile'" otherwise."""
    if isinstance(e, (KeyError, IndexError, TypeError, AttributeError)):
        return f"{type(e).__name__}: {e} (a game reply was missing something - see the log)"
    return str(e)


def _hours_text(h):
    return f"{h * 60:g} min" if h and h < 1 else f"{h:g} h"


def _my_tile():
    """Our world tile from the game, or None."""
    from lumberjack.core import gamestate
    gs = gamestate.shared()
    try:
        return tuple(gs.player()["tile"]) if gs else None
    except Exception:
        return None


def _levels():
    """{skill: base level} from the game, or {} without game data."""
    from lumberjack.core import gamestate
    gs = gamestate.shared()
    try:
        return {k: v["level"] for k, v in gs.skills().items()} if gs else {}
    except Exception:                      # levels only refine the order - never stop a plan
        return {}


STEP_OPTIONS = {"trees", "auto_trees", "when_full", "fish_method", "auto_fish", "cook", "cooked_action",
                "fire_trees", "ores", "auto_ores", "mine_full", "targets", "foods", "eat_below", "train",
                "loot", "bury_bones", "fm_logs", "drop_at", "thieve", "spawn_tools", "clear_at_start",
                "recover", "keep_carried", "map", "spots", "chop_spot", "fish_spot", "mine_spot",
                "fight_spot", "bank_spot", "burn_spot", "start_mode", "start_spot"}
# Autopilot's own settings: fastest XP, nothing to babysit - not whatever the Settings tab last had
AUTOPILOT_OPTIONS = {"when_full": "drop", "mine_full": "drop", "cook": True, "cooked_action": "drop",
                     "foods": [], "loot": [], "bury_bones": False, "train": "auto", "spawn_tools": True,
                     "clear_at_start": True, "recover": True, "keep_carried": True, "drop_at": 28,
                     # the game's own data finds the way: no pixel-era maps (their learner grabs the screen)
                     "map": None, "spots": None, "chop_spot": None, "fish_spot": None, "mine_spot": None,
                     "fight_spot": None, "bank_spot": None, "burn_spot": None, "start_mode": "here",
                     "start_spot": None}


def step_level(step, levels):
    """The step's skill level (combat: the lowest of attack/strength/defence), or None."""
    have = [levels[k] for k in TASK_LEVEL_SKILLS.get(step.task, [step.task]) if k in levels]
    return min(have) if have else None


def next_step(plan, i, levels):
    """Index of the step after step i (-1 = before the first). Steps whose target level is already
    reached are skipped; None when that's all of them. 'lowest' order picks the step whose skill
    is lowest (ties: the next one after i)."""
    n = len(plan.steps)
    order = [(i + k) % n for k in range(1, n + 1)]

    def reached(j):
        st, lv = plan.steps[j], step_level(plan.steps[j], levels)
        return bool(st.level and lv is not None and lv >= st.level)
    open_ = [j for j in order if not reached(j)]
    if not open_:
        return None
    if plan.order == "lowest" and levels:
        return min(open_, key=lambda j: (step_level(plan.steps[j], levels) or 0, order.index(j)))
    return open_[0]


def load_plan():
    try:
        return Plan(**json.loads(PLAN.read_text()))
    except Exception:
        return Plan()


def save_plan(p):
    PLAN.parent.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(p.model_dump_json(indent=2))


def _until_text(step):
    parts = ([f"{step.minutes:g} min"] if step.minutes else []) + ([f"level {step.level}"] if step.level else [])
    return "until " + " or ".join(parts) if parts else "no limit"


def load_settings():
    try:
        data = json.loads(CONFIG.read_text())
        if "tree" in data:  # pre-multi-select config
            data["trees"] = [data.pop("tree")]
        return Settings(**data)
    except Exception:
        return Settings()


def save_settings(s):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(s.model_dump_json(indent=2))


def preflight(s: "Settings"):
    """What a run needs, checked before Start. Never touches the game's mouse/keyboard.

    Returns {"errors": [...], "fixes": [...], "warnings": [...]} - errors block Start,
    fixes are things the bot will sort out itself, warnings are worth knowing.
    """
    from lumberjack.core import agent
    from lumberjack.ui import mouseover
    errors, fixes, warnings = [], [], []

    # the game
    try:
        try:
            window().grab()
        except Exception:
            global _win
            with _win_lock:
                _win = None                              # look the window up afresh, once
            window().grab()
    except Exception as e:
        msg = str(e)
        if "not found" in msg and agent.is_running():
            errors.append("The game is running (its add-on answers) but its window can't be found - "
                          f"{msg}. Send the log if this keeps happening.")
        elif "not found" in msg:
            errors.append("The game isn't running - start it with the Grindstone icon and log in.")
        elif "Fixed" in msg or "canvas" in msg.lower():
            errors.append("The game must be in Fixed screen mode (765x503) - switch it in the game's settings.")
        else:
            errors.append(f"Can't see the game: {msg}")
    if not errors and not agent.is_running():
        fixes.append("The input add-on isn't loaded - it'll be attached automatically "
                     "(or start the game with 'lumberjack client').")

    # map & spots
    spots = {}
    if s.map:
        try:
            from lumberjack.nav.worldmap import WorldMap
            spots = WorldMap.load(s.map).spots
        except Exception:
            errors.append(f"Map '{s.map}' can't be loaded - pick another under Tools › Maps & route, or record it in Tools › Maps & route.")
    banks = [n for n, v in spots.items() if v.get("kind") == "bank"]
    needs_map = []
    if s.task == "firemaking" and not _fm_spawns(s):
        needs_map.append("the Firemaking task")
    if s.task == "woodcutting" and s.when_full == "bank" and not _names_from_game():
        needs_map.append("banking")       # (with game data the nearest booth is found directly;
    # fishing banks without a map too: by game data, else by the minimap's $ icon)
    if s.task == "fishing" and s.fish_spot:
        needs_map.append("'Fish at'")
    if s.start_mode == "spot":
        needs_map.append("'Start from: I'm at'")
    if s.chop_spot:
        needs_map.append("'Chop at'")
    if needs_map and not s.map:
        errors.append(f"Pick a map under Tools › Maps & route - {', '.join(needs_map)} needs one.")
    if s.task == "woodcutting" and s.when_full == "bank" and not s.map and _names_from_game():
        fixes.append(MAPLESS_BANK)
    if s.map and spots:
        for label, name in (("Chop at", s.chop_spot), ("I'm at", s.start_spot if s.start_mode == "spot" else None),
                            ("Bank at", s.bank_spot), ("Burn at", s.burn_spot if s.task == "firemaking" else None),
                            ("Fish at", s.fish_spot if s.task == "fishing" else None)):
            if name and name not in spots:
                errors.append(f"'{label}' spot '{name}' isn't on map '{s.map}' - pick another under Tools › Maps & route.")
        bank_needed = ((s.task == "firemaking" and not _fm_spawns(s)) or (s.task == "woodcutting" and s.when_full == "bank")
                       or (s.task == "fishing" and s.cooked_action == "bank"))
        if bank_needed and not banks:
            errors.append(f"Map '{s.map}' has no bank spot - stand by a bank booth and use "
                          f"Map tools > Save spot here > Bank.")
    if s.start_mode == "teleport" and not mouseover.available("home_tp"):
        errors.append("Home Teleport isn't calibrated yet.")

    # task specifics
    if s.task == "woodcutting":
        if s.auto_trees:
            if not any(mouseover.available(t) for t in ("tree", "oak", "willow")) and not _names_from_game():
                errors.append("No tree names learned yet - stand by a tree and use Map tools > Learn.")
        elif not s.trees:
            errors.append("Tick at least one tree under Task, or tick 'Best trees for my levels'.")
        else:
            for t in s.trees:
                if not mouseover.available(t) and not _names_from_game():
                    errors.append(f"'{t}' isn't learned yet - stand next to one and use Map tools > Learn.")
        has_bank = bool(s.map and banks)
        for mode, tool in (("burn", "tinderbox"), ("fletch", "knife")):
            if s.when_full != mode:
                continue
            if s.spawn_tools:
                fixes.append(f"{mode.capitalize()} needs a {tool} - spawned with ::item if you don't carry one.")
            elif has_bank:
                fixes.append(f"{mode.capitalize()} needs a {tool} - fetched from the bank if you don't carry one.")
            else:
                warnings.append(f"{mode.capitalize()} needs a {tool} - carry one (no bank spot, spawning is off).")
        fixes.append("No axe? " + ("The best axe for your Woodcutting level is spawned if chopping keeps failing."
                                   if s.spawn_tools else "Turn on 'Spawn missing tools' or carry/wield one."))
        if s.when_full == "drop" and not s.keep_carried:
            warnings.append("'Keep what I'm carrying' is off - Drop will drop your tools too.")
        if s.auto_trees and s.when_full in ("burn", "fletch"):
            fixes.append("Trees are chosen from your Woodcutting and "
                         + ("Firemaking" if s.when_full == "burn" else "Fletching") + " levels, re-checked after every load.")
    elif s.task == "firemaking" and _fm_spawns(s):
        fixes.append("Spawns the best logs your Firemaking level can light and burns them where you "
                     "start - no bank or map needed (untick 'Spawn missing tools' to burn banked logs).")
    elif s.task == "firemaking":
        if not [t for t in s.fm_logs if mouseover.available(t) or _names_from_game()]:
            errors.append("Tick at least one learned log type under Task.")
        if not s.burn_spot:
            warnings.append("No 'Burn at' spot - fires get lit wherever you are after banking "
                            "(that fails inside the bank).")
        fixes.append("Logs you can't light yet are skipped; a tinderbox is fetched from the bank if needed.")
    elif s.task == "autopilot":
        if not (_names_from_game() and s.spawn_tools):
            errors.append("Autopilot needs the game's own data and 'Spawn missing tools' (it spawns tools, "
                          "gear, food and supplies as it goes).")
        else:
            fixes.append(f"Trains every skill to {s.autopilot_target}: combat basics first, quick Prayer, then "
                         "always the lowest skill to its next milestone, at the best spot for the level. "
                         "Wears the best gear for its levels.")
            if s.max_minutes:
                warnings.append(f"Stops after {_hours_text(s.max_minutes / 60)} - clear Settings › Run options › "
                                "'Stop after' to run until you press Stop.")
            else:
                fixes.append("Runs until you press Stop. Each skill is trained to its next milestone (the next "
                             "multiple of 10 or better training spot) before it moves to the next lowest one.")
    elif s.task in SPAWN_TASKS:
        what = SPAWN_TASKS[s.task]
        if not (s.spawn_tools and _names_from_game()):
            errors.append(f"{s.task.capitalize()} spawns its {what}: it needs the game's own data and "
                          "'Spawn missing tools'.")
        elif s.task == "cooking":
            fixes.append("Spawns the best raw fish your Cooking level allows, cooks them on its own fire "
                         "where you start (stand somewhere open), drops them, repeats.")
        elif s.task == "herblore":
            fixes.append("Spawns unfinished potions + ingredients for the best potion your level makes, mixes "
                         "them (Make All), drops the potions, repeats. Herblore needs the Druidic Ritual quest: "
                         "it's marked done with the admin quest command (a level-1 account also gets the "
                         "quest's 250 XP).")
        elif s.task == "runecrafting":
            fixes.append("Teleports into the best altar room for your level (air, mind, water, earth, fire, body, "
                         "cosmic, nature, law), spawns pure essence, crafts it, repeats; teleports back out at the end.")
        else:
            fixes.append(f"Spawns {what} for your level, trains on them where you start, drops the results, repeats.")
    elif s.task == "agility":
        if not _names_from_game():
            errors.append("Agility needs the game's own data (it finds the obstacles in the scene).")
        fixes.append("Teleports to the Gnome Stronghold course (the Barbarian Outpost course from 35) and runs "
                     "laps; back to the start when it gets lost or falls, back where you were at the end.")
    elif s.task == "farming":
        if not (_names_from_game() and s.spawn_tools):
            errors.append("Farming needs the game's own data and 'Spawn missing tools' (seeds and tools).")
        fixes.append("Teleports to the Falador farm: rakes, adds supercompost, plants the best seeds for your level "
                     "in both allotments (and the herb patch from 9), grows them with ::grow, cures or clears sick "
                     "crops, harvests, drops the harvest; back where you were at the end.")
    elif s.task == "hunter":
        if not (_names_from_game() and s.spawn_tools):
            errors.append("Hunter needs the game's own data and 'Spawn missing tools' (it spawns its traps).")
        fixes.append("Teleports to the creatures for your level (crimson swifts, cerulean twitches, tropical "
                     "wagtails, then chinchompas and red chinchompas), lays as many traps as the level allows, "
                     "checks catches, drops the bones and meat; back where you were at the end.")
    elif s.task == "thieving":
        if not _names_from_game():
            errors.append("Thieving needs the game's own data (it finds people to pickpocket in the NPC list).")
        fixes.append(f"Pickpockets {'/'.join(s.thieve)} nearby; eats below {s.eat_below}% HP"
                     + (" (spawns lobsters when out of food)." if s.spawn_tools else "."))
    elif s.task == "fishing":
        fishing_checks(s, errors, fixes, warnings)
    elif s.task == "mining":
        mining_checks(s, errors, fixes, warnings)
    elif s.task == "slayer":
        if not (_names_from_game() and s.spawn_tools):
            errors.append("Slayer needs the game's own data and 'Spawn missing tools' (gear and food).")
        fixes.append("Fights the training route's monsters for your Slayer level (chickens, cows, goblins, hill "
                     "giants, ice warriors, ankou, fire giants) with a matching Slayer task set by the admin "
                     "command, renewed before it runs out. Eats, wears the best gear, trains melee too.")
    elif s.task in ("combat", "ranged"):
        combat_checks(s, errors, fixes, warnings)
        if s.task == "ranged":
            fixes.append("Wears the best shortbow for your Ranged level and spawns its arrows (1000 at a time)."
                         if s.spawn_tools else "Wear a bow and arrows yourself (or tick 'Spawn missing tools').")
    game_data_check(fixes, warnings)
    if s.keep_carried and s.task in ("woodcutting", "firemaking"):
        if not _names_from_game():
            warnings.append("Carry only your tools (axe, tinderbox, knife) - at most 4 non-log items.")
    return {"errors": errors, "fixes": fixes, "warnings": warnings}


_gd = {"t": 0.0, "missing": None}
MAPLESS_BANK = ("No map picked: walks to the nearest bank booth (from the game's data, up to ~45 tiles away), "
                "banks, and walks back to the exact tile it left from.")


def _fm_spawns(s):
    """Firemaking spawns its logs (game data + 'Spawn missing tools') instead of banking."""
    return s.task == "firemaking" and s.spawn_tools and _names_from_game()


def _progress(b):
    """The running bot's main counter (logs, fish, casts, bones...)."""
    if b is None:
        return 0
    try:
        return b.progress() if hasattr(b, "progress") else b.logs_cut
    except Exception:
        return getattr(b, "logs_cut", 0)


def _game_data_up():
    from lumberjack.core import gamestate
    return gamestate._shared is not None          # (no probe here: status is polled twice a second)


def _names_from_game():
    """Is the backpack read from the game (so item names needn't be learned)?"""
    from lumberjack.core import backpack
    return backpack.source() is not None


def game_data_check(fixes, warnings):
    """Can the add-on read the game's own data? (probed at most every 10 s)"""
    from lumberjack.core.gamestate import GameState, GameStateError
    if time.monotonic() - _gd["t"] > 10:
        try:
            _gd["missing"] = GameState().probe()
        except GameStateError:
            _gd["missing"] = None
        _gd["t"] = time.monotonic()
    if _gd["missing"] == []:
        fixes.append("Tools are kept by name - carry as many as you like; only logs, ore and fish are "
                     "dropped or banked.")
        fixes.append("Reads the game's own data: spots, menu options, the backpack (items by name) and "
                     "levels/XP (live) come from the client, not from pixels - no item names to learn.")
    elif _gd["missing"]:
        warnings.append("Game data partly unreadable (" + ", ".join(_gd["missing"]) + ") - using the screen.")
    else:
        warnings.append("Game data isn't readable yet - close the game and start it again with the Grindstone "
                        "icon so the add-on is rebuilt. Until then the bot reads the screen.")


def fishing_checks(s, errors, fixes, warnings):
    """Pre-flight for the Fishing task (templates still to learn are errors with the button to press)."""
    from lumberjack.skills import cooking, fishing
    from lumberjack.skills.fish_cook import usable_trees
    from lumberjack.ui import mouseover
    if s.fish_method not in fishing.METHOD_LEVELS:
        errors.append(f"Unknown fishing method '{s.fish_method}'.")
        return
    missing = [] if _names_from_game() else fishing.missing_now(s.fish_method)
    if missing:
        errors.append(f"{s.fish_method.capitalize()} fishing isn't calibrated - stand by a fishing spot and press "
                      f"Fishing > Learn fishing spot (missing: {', '.join(missing)}).")
    if not s.keep_carried:
        errors.append("Tick 'Keep what I'm carrying' - the bot keeps your fishing tools by remembering them.")
    if s.spawn_tools:
        fixes.append(f"Missing fishing gear ({fishing.TOOLS[s.fish_method]}) is spawned with ::item"
                     + (" - bait/feathers again when they run out." if s.fish_method in fishing.USES_BAIT else "."))
    if not _names_from_game():
        warnings.append(f"Carry only your tools ({fishing.TOOLS[s.fish_method]}"
                        + (", tinderbox, axe" if s.cook else "") + ") - at most 4 items.")
    if s.auto_fish:
        fixes.append("Switches method as your Fishing level goes up: bait at 5 (same spot), lure at 20, "
                     "harpoon 35 / cage 40 - those need their spot calibrated and saved as a Fishing spot.")
    if not s.cook:
        if s.cooked_action == "drop":
            fixes.append("Raw fish are dropped when the backpack is full.")
        return
    for m in cooking.missing_templates(s.cooked_action, names_known=_names_from_game()):
        errors.append(f"Cooking needs {m}.")
    if not usable_trees(s.fire_trees, {}, names_known=_names_from_game()):
        errors.append("Tick a tree for fire logs whose name and logs are learned (e.g. Willow + Willow logs).")
    fixes.append("Fires: a log is chopped from a nearby " + "/".join(s.fire_trees or ["tree"])
                 + " and lit; a fire already burning nearby is used first.")
    fixes.append("A missing tinderbox is spawned with ::item." if s.spawn_tools
                 else "Carry a tinderbox (spawning is off).")
    fixes.append("Burnt fish are dropped; cooked fish are " + ("banked." if s.cooked_action == "bank" else "dropped."))
    if s.cooked_action == "bank" and not s.map:
        fixes.append(MAPLESS_BANK if _names_from_game() else
                     "No map picked: walks to the bank by its $ icon on the minimap and back again - "
                     "the icon must be on the minimap from the fishing spot (fine at Draynor).")
    if not mouseover.available("eat"):
        fixes.append("'Eat' is learned from the first fish it cooks (that's how cooked fish are told apart).")
    if not mouseover.available("burnt_shrimp") and not mouseover.available("burnt_fish"):
        warnings.append("Burnt fish names aren't learned - they're still dropped, just counted by elimination.")


def mining_checks(s, errors, fixes, warnings):
    from lumberjack.skills import mining
    from lumberjack.ui import mouseover
    missing = [n for n in (mining.MINE_ACTION, mining.ROCK_TARGET) if not mouseover.available(n)]
    if missing and not _names_from_game():
        errors.append("Mining isn't calibrated - stand next to a rock and press Mining > Learn rock.")
    if not s.auto_ores and not s.ores:
        errors.append("Tick at least one ore.")
    if s.mine_full == "bank" and not s.map:
        if _names_from_game():
            fixes.append(MAPLESS_BANK)
        else:
            errors.append("Banking needs a map with a bank spot (pick one under Tools › Maps & route).")
    if not s.keep_carried:
        errors.append("Tick 'Keep what I'm carrying' - that's how the bot knows not to drop your pickaxe.")
    if not _names_from_game():
        warnings.append("Carry only your pickaxe (max 4 items) - everything else counts as ore.")
    if _names_from_game():
        fixes.append("Rocks come from the game's scene data; which ore each rock holds is learned as you mine "
                     "(and shared between PCs) - the screen's ore colours are only a first guess.")
    else:
        warnings.append("Rock vision is new: press Mining > Test vision at a mine and check the Activity log.")
    if s.auto_ores:
        fixes.append("The ore is picked from your Mining level (and the rock spots saved on the map).")
    fixes.append("Ore is " + ("banked." if s.mine_full == "bank" else "dropped when the backpack is full."))


def combat_checks(s, errors, fixes, warnings):
    from lumberjack.ui import mouseover
    if not s.targets:
        errors.append("Tick at least one monster to fight.")
    missing = [t for t in ["attack"] + list(s.targets) if not mouseover.available(t)]
    if missing and not _names_from_game():
        errors.append("Not learned yet: " + ", ".join(m.replace("_", " ") for m in missing)
                      + " - stand next to one and press Combat > Learn monster.")
    if s.foods:
        if not mouseover.available("eat") and not _names_from_game():
            errors.append("Food needs 'Eat' learned - Fishing > Learn item on a cooked fish.")
        fixes.append(f"Eats below {s.eat_below}% HP; stops when the food runs out and HP is low.")
    elif s.spawn_tools and _names_from_game():
        fixes.append(f"No food ticked - lobsters are spawned and eaten below {s.eat_below}% HP "
                     "(cooked fish you carry is eaten too).")
    else:
        warnings.append(f"No food ticked - the bot stops when HP drops below {s.eat_below}%.")
    fixes.append("Trains the lowest melee stat (switches attack style)." if s.train == "auto"
                 else f"Trains {s.train} only.")
    if s.loot:
        missing = [i for i in ["take"] + list(s.loot) if not mouseover.available(i)]
        if missing and not _names_from_game():
            errors.append("Loot not learned yet: " + ", ".join(m.replace("_", " ") for m in missing)
                          + " - stand on the item and press Combat > Learn loot.")
        fixes.append("Picks up " + ", ".join(i.replace("_", " ") for i in s.loot) + " after each kill.")
    if s.bury_bones:
        if not (mouseover.available("bury") and mouseover.available("bones")) and not _names_from_game():
            errors.append("Burying needs 'bones' (Learn loot) and 'Bury' (Learn bury) learned.")
        elif "bones" not in s.loot:
            warnings.append("Tick Bones under Loot too, or there won't be any bones to bury.")
    warnings.append("Combat is new: press Combat > Test vision next to a monster and check the Activity log.")


class LogBuffer(logging.Handler):
    def __init__(self, n=200):
        super().__init__()
        self.lines = collections.deque(maxlen=n)  # (seq, text)
        self.seq = 0
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.seq += 1
        self.lines.append((self.seq, self.format(record)))

    def since(self, seq):
        return [(s, t) for s, t in list(self.lines) if s > seq]


def task_problem(s: Settings):
    """Task-specific reasons not to start (beyond preflight), or None."""
    from lumberjack.ui import mouseover
    if s.task == "firemaking" and _fm_spawns(s):
        return None
    if s.task in SPAWN_TASKS:
        return None if s.spawn_tools and _names_from_game() else \
            f"{s.task.capitalize()} needs the game's own data and 'Spawn missing tools' (it spawns its {SPAWN_TASKS[s.task]})"
    if s.task in ("thieving", "agility"):
        return None if _names_from_game() else f"{s.task.capitalize()} needs the game's own data"
    if s.task == "slayer":
        return None if s.spawn_tools and _names_from_game() else \
            "Slayer needs the game's own data and 'Spawn missing tools'"
    if s.task == "farming":
        return None if s.spawn_tools and _names_from_game() else \
            "Farming needs the game's own data and 'Spawn missing tools'"
    if s.task == "hunter":
        return None if s.spawn_tools and _names_from_game() else \
            "Hunter needs the game's own data and 'Spawn missing tools' (it spawns its traps)"
    if s.task == "firemaking":
        if not [t for t in s.fm_logs if mouseover.available(t) or _names_from_game()]:
            return "None of the ticked logs have been learned yet (open the bank and use 'Learn bank items')"
        if not s.map:
            return "Firemaking needs a map with a bank spot (pick one under Tools › Maps & route)"
        return None
    if s.task != "woodcutting":
        return None
    if (s.start_mode == "spot" or s.chop_spot) and not s.map:
        return "pick a map to use spots"
    if s.when_full not in {a["id"] for a in FULL_ACTIONS if a["ready"]}:
        return f"'{s.when_full}' isn't built yet"
    if s.when_full == "bank" and not s.map and not _names_from_game():
        return "Banking needs a map with a bank spot (pick one under Tools › Maps & route)"
    if s.when_full == "bank" and s.map:
        from lumberjack.nav.spots import bank_spots
        from lumberjack.nav.worldmap import WorldMap
        if not bank_spots(WorldMap.load(s.map)):
            return f"No bank spot on map '{s.map}' - save one in Map tools"
    if not s.trees:
        return "Pick at least one tree type"
    missing = [t for t in s.trees if not mouseover.available(t)]
    if missing and not _names_from_game():
        return f"{', '.join(missing)} needs calibrating first (no hover-text template yet)"
    return None


# skills shown live in the XP table per running bot (its .name; the woodcutter has none)
TASK_SKILLS = {
    "woodcutting": ["woodcutting", "firemaking", "fletching"], "bot": ["woodcutting", "firemaking", "fletching"],
    "firemaking": ["firemaking"], "fishing": ["fishing", "cooking", "firemaking", "woodcutting"],
    "mining": ["mining"], "combat": ["attack", "strength", "defence", "hitpoints"],
    "cooking": ["cooking", "firemaking"], "prayer": ["prayer"], "fletching": ["fletching"],
    "crafting": ["crafting"], "thieving": ["thieving", "hitpoints"], "smithing": ["smithing"],
    "herblore": ["herblore"], "runecrafting": ["runecrafting"], "agility": ["agility"], "hunter": ["hunter"],
    "slayer": ["slayer", "attack", "strength", "defence", "hitpoints"], "farming": ["farming"],
    "ranged": ["ranged", "hitpoints"], "magic": ["magic"],
}


class BotController:
    def __init__(self):
        self.thread = None
        self.bot = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.error = None
        self.plan_info = None
        self.doing = None
        self.updating = False
        self._update_failed_at = None
        self.last_reason = None
        self.end_reason = None              # why the last plan / autopilot as a whole ended
        self.check_results = None           # {task: {"xp", "reasons"}} while a skill check runs
        self.last_check = None
        self.plan_ends_at = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, s: Settings):
        if self.running:
            return "already running"
        logging.getLogger("panel").info("Grindstone %s - starting %s", version.label(), s.task)
        if manual.busy:
            return f"wait for '{manual.busy}' to finish"
        if rec.running:
            return "stop the map recorder first"
        check = preflight(s)
        if check["errors"]:
            return "Can't start yet: " + " ".join(check["errors"])
        if s.task == "autopilot":           # the whole account: a plan that picks its own steps
            # only Controls' "Stop after" limits it - not the Plan tab's hours (that's for plans)
            p = load_plan().model_copy(update={"autopilot": True, "target": s.autopilot_target,
                                               "skip": s.autopilot_skip, "steps": [],
                                               "auto_update": True, "resume": True,   # keeps itself current
                                               "max_hours": s.max_minutes / 60 if s.max_minutes else None})
            return self.start_plan(p)
        err = task_problem(s)
        if err:
            return err
        self.plan_info = None
        from lumberjack.core import gamestate
        if s.recover and gamestate.shared() is not None:
            return self._run_single(s)
        return self._run_bot(s.task, lambda: self.build_bot(s))

    def _run_single(self, s: Settings):
        """Start, with the plan's recovery: a crash, stall or death doesn't end the run."""
        self.stop_event.clear()
        self.pause_event.clear()
        self.error = None
        self.end_reason = None
        log = logging.getLogger(s.task)

        resume = load_plan().resume                # the Plan tab's "resume after a restart" applies too
        if resume:                                 # a panel restart (update, crash) carries on with it
            ends_at = time.time() + s.max_minutes * 60 if s.max_minutes else None
            _mark_single(s, ends_at)

        def run():
            try:
                self._run_with_recovery(s, log, label=f"Running {s.task}", first_as_is=True)
            except Exception as e:
                if not self.stop_event.is_set():
                    self.error = _err_text(e)
                    log.exception("Run crashed")
            finally:
                self._doing(None)
                if resume and not self.updating:
                    PLAN_RUNNING.unlink(missing_ok=True)

        self.thread = threading.Thread(target=run, name="bot", daemon=True)
        self.thread.start()
        return None

    def build_bot(self, s: Settings):
        """The bot for s.task, wired to this controller's stop/pause events."""
        common = dict(max_minutes=s.max_minutes, stop_event=self.stop_event, pause_event=self.pause_event,
                      start_mode=s.start_mode, start_spot=s.start_spot, clear_at_start=s.clear_at_start)
        if s.task == "firemaking":
            from lumberjack.skills.firemaking_task import Firemaker
            from lumberjack.ui import mouseover
            return Firemaker(logs=[t for t in s.fm_logs if mouseover.available(t) or _names_from_game()],
                             bank_spot=s.bank_spot,
                             burn_spot=s.burn_spot, max_logs=s.max_logs, map_name=s.map,
                             keep_carried=s.keep_carried, spawn_logs=_fm_spawns(s),
                             **common)
        if s.task == "cooking":
            from lumberjack.skills.cooking_task import Cooker
            return Cooker(spawn_tools=s.spawn_tools, **common)
        if s.task in ("prayer", "fletching", "crafting", "smithing", "herblore", "runecrafting"):
            from lumberjack.skills import spawn_tasks
            cls = {"prayer": spawn_tasks.Prayer, "fletching": spawn_tasks.Fletcher,
                   "crafting": spawn_tasks.Crafter, "smithing": spawn_tasks.Smither,
                   "herblore": spawn_tasks.Herbalist, "runecrafting": spawn_tasks.Runecrafter}[s.task]
            return cls(spawn_tools=s.spawn_tools, **common)
        if s.task == "thieving":
            from lumberjack.skills.thieving_task import Thief
            return Thief(targets=s.thieve, eat_below=s.eat_below, spawn_tools=s.spawn_tools, **common)
        if s.task == "fishing":
            from lumberjack.skills import fishing
            from lumberjack.skills.fish_cook import FishCooker
            method = ([s.fish_method] + [m for m in fishing.METHOD_LEVELS if m != s.fish_method]
                      if s.auto_fish else s.fish_method)
            return FishCooker(cook=s.cook, cooked=s.cooked_action, fire_trees=s.fire_trees,
                              spawn_tools=s.spawn_tools, method=method, auto=s.auto_fish,
                              fish_spot=s.fish_spot, bank_spot=s.bank_spot, drop_at=s.drop_at,
                              max_fish=s.max_logs, map_name=s.map or None, keep_carried=s.keep_carried, **common)
        if s.task == "mining":
            from lumberjack.skills.mining import Miner
            return Miner(ores_allowed=s.ores or ["copper", "tin"], auto_ores=s.auto_ores, when_full=s.mine_full,
                         spawn_tools=s.spawn_tools, drop_at=s.drop_at, mine_spot=s.mine_spot,
                         bank_spot=s.bank_spot, max_logs=s.max_logs, map_name=s.map or None,
                         keep_carried=s.keep_carried, **common)
        if s.task == "farming":
            from lumberjack.skills.farming_task import Farmer
            return Farmer(**common)
        if s.task == "hunter":
            from lumberjack.skills.hunter_task import Hunter
            return Hunter(**common)
        if s.task == "agility":
            from lumberjack.skills.agility_task import Agility
            return Agility(**common)
        if s.task == "magic":
            from lumberjack.skills.magic_task import Mage
            return Mage(spawn_tools=s.spawn_tools, **common)
        if s.task == "ranged":                  # combat with a spawned bow and arrows
            from lumberjack.skills.combat import Fighter
            f = Fighter(targets=s.targets, foods=s.foods, eat_below=s.eat_below / 100, train="ranged",
                        spawn_tools=s.spawn_tools, fight_spot=s.fight_spot, loot=s.loot,
                        bury_bones=s.bury_bones, max_logs=s.max_logs, map_name=s.map or None, **common)
            f.name = "ranged"
            return f
        if s.task == "slayer":
            from lumberjack.core import gamestate
            from lumberjack.nav import training
            from lumberjack.skills.slayer_task import SlayerFighter, task_npc
            targets = s.targets if task_npc(s.targets) else \
                training.pick("slayer", (gamestate.skill("slayer") or {}).get("base"))[1]["targets"]
            return SlayerFighter(targets=targets, foods=s.foods, eat_below=s.eat_below / 100, train=s.train,
                                 spawn_tools=s.spawn_tools, fight_spot=s.fight_spot, loot=s.loot,
                                 bury_bones=s.bury_bones, max_logs=s.max_logs, map_name=s.map or None, **common)
        if s.task == "combat":
            from lumberjack.skills.combat import Fighter
            return Fighter(targets=s.targets, foods=s.foods, eat_below=s.eat_below / 100, train=s.train,
                           spawn_tools=s.spawn_tools, fight_spot=s.fight_spot, loot=s.loot,
                           bury_bones=s.bury_bones, max_logs=s.max_logs, map_name=s.map or None, **common)
        from lumberjack.skills.woodcutting import Woodcutter
        return Woodcutter(trees_allowed=s.trees, drop_at=s.drop_at, max_logs=s.max_logs,
                          map_name=s.map or None, spot_names=s.spots, chop_spot=s.chop_spot,
                          when_full=s.when_full, bank_spot=s.bank_spot, keep_carried=s.keep_carried,
                          auto_trees=s.auto_trees, spawn_tools=s.spawn_tools, **common)

    def _run_bot(self, name, make):
        self.stop_event.clear()
        self.pause_event.clear()
        self.error = None
        self.end_reason = None

        def run():
            try:
                self.bot = make()
                self.bot.run()
            except Exception as e:  # surface e.g. "game window not found" in the UI
                self.error = _err_text(e)
                logging.getLogger(name).exception("Bot crashed")

        self.thread = threading.Thread(target=run, name="bot", daemon=True)
        self.thread.start()
        return None

    # ---- plans: several tasks in a row, non-stop ------------------------------------------
    def start_plan(self, plan: Plan, after=-1, ends_at=None):
        if self.running:
            return "already running"
        if manual.busy or rec.running:
            return "wait for the manual action / map recorder to finish"
        if not plan.steps and not plan.autopilot:
            return "Add at least one step to the plan"
        from lumberjack.core import gamestate
        if gamestate.shared() is None:
            return ("Plans need the game's own data (positions, levels) - restart the game with the "
                    "Grindstone icon so the add-on is rebuilt")
        from lumberjack.nav import places as P
        known = P.load()
        missing = [st.place for st in plan.steps if st.place and st.place not in known and st.place != "auto"]
        if missing:
            return f"Unknown place(s): {', '.join(missing)} - save them first"
        self.stop_event.clear()
        self.pause_event.clear()
        self.error = None
        self.end_reason = None
        self.plan_info = {"step": 0, "of": len(plan.steps), "task": None, "place": None, "attempt": 0}
        if ends_at is None and plan.max_hours:
            ends_at = time.time() + plan.max_hours * 3600     # wall clock: survives a panel restart
        self.plan_ends_at = ends_at
        global _running_plan
        _running_plan = plan if plan.resume and not plan.check else None
        _mark_running(after, ends_at)
        self.thread = threading.Thread(target=self._run_plan, args=(plan, after), name="plan", daemon=True)
        self.thread.start()
        return None

    def _run_plan(self, plan: Plan, after=-1):
        if plan.autopilot:
            return self._run_autopilot(plan)
        log = logging.getLogger("plan")
        log.info("Grindstone %s - plan of %d step(s)%s%s", version.label(), len(plan.steps),
                 ", looping" if plan.loop else "", ", lowest level first" if plan.order == "lowest" else "")
        i, done_well, tried = min(after, len(plan.steps) - 1), False, 0
        try:
            while not self.stop_event.is_set():
                end = self.plan_ends_at
                if end and time.time() >= end:
                    log.info("Plan finished - its %g h are up", plan.max_hours or 0)
                    self.end_reason = f"the plan's time limit ({_hours_text(plan.max_hours)}) is up"
                    break
                i = next_step(plan, i, _levels())
                if i is None:
                    log.info("Plan finished - every step's target level is reached")
                    self.end_reason = "plan finished - every step's target level is reached"
                    break
                if i == 0 and tried and plan.order != "lowest":
                    if not plan.loop:
                        log.info("Plan finished")
                        self.end_reason = "plan finished"
                        break
                    log.info("Plan: starting over")
                if tried and plan.auto_update and plan.resume:
                    self._maybe_update(log)
                if tried and tried % len(plan.steps) == 0:
                    if not done_well:              # nothing worked this round: don't spin
                        log.warning("Nothing worked this round - waiting %d s before the next", IDLE_PASS_WAIT_S)
                        self._doing("waiting - nothing worked last round")
                        self._sleep(IDLE_PASS_WAIT_S)
                        self._doing(None)
                    done_well = False
                step = plan.steps[i]
                if end:                            # the last step only gets the time that's left
                    left = (end - time.time()) / 60
                    if step.minutes is None or step.minutes > left:
                        step = step.model_copy(update={"minutes": max(left, 0.1)})
                self.plan_info.update(step=i + 1, task=step.task, place=step.place, attempt=0,
                                      until=_until_text(step))
                done_well = self._run_step(step, plan, log) or done_well
                tried += 1
                if not self.stop_event.is_set():
                    _mark_running(i, end)           # a resume carries on after this step
        except Exception as e:
            if not self.stop_event.is_set():       # (a stop mid-travel surfaces as an exception)
                self.error = _err_text(e)
                log.exception("Plan crashed")
        finally:
            self.plan_info = None
            if not self.updating:
                PLAN_RUNNING.unlink(missing_ok=True)   # stopped/finished on purpose: no resume
            if self.stop_event.is_set():
                self.end_reason = "stopped from the control panel"
            elif self.error:
                self.end_reason = f"crashed: {self.error}"
            if plan.check:
                self._finish_check(log)
            log.info("Plan stopped")

    def start_check(self, minutes=CHECK_MINUTES, tasks=None):
        """Skill check: every task once, for a few minutes each, at the place for our level -
        then a report of what earned XP and why anything stopped (logs/skill_check.txt)."""
        from lumberjack.nav import training
        tasks = tasks or CHECK_TASKS
        steps = [PlanStep(task=t, minutes=minutes, place=training.AUTO if training.has_route(t) else None)
                 for t in tasks]
        self.check_results = {}
        err = self.start_plan(Plan(steps=steps, loop=False, resume=False, auto_update=False, check=True))
        if err:
            self.check_results = None
        return err

    def _finish_check(self, log):
        results, self.check_results = self.check_results or {}, None
        lines, ok = [], 0
        for task, r in results.items():
            xp = sum(r["xp"].values())
            ok += xp > 0
            mark = "OK  " if xp > 0 else "FAIL"
            gained = ", ".join(f"+{n:,} {k}" for k, n in r["xp"].items()) or "no xp"
            lines.append(f"{mark} {task:<13} {gained:<40} {'; '.join(r['reasons'])}")
        head = f"Skill check {time.strftime('%Y-%m-%d %H:%M')}, Grindstone {version.label()}: " \
               f"{ok} of {len(results)} earned XP"
        report = "\n".join([head, ""] + lines) + "\n"
        try:
            LOGS_DIR.mkdir(exist_ok=True)
            (LOGS_DIR / "skill_check.txt").write_text(report, encoding="utf-8")
        except OSError as e:
            log.warning("Couldn't save the skill check (%s)", e)
        for line in [head] + lines:
            log.info("%s", line)
        self.last_check = report
        if not self.stop_event.is_set():
            self.end_reason = f"skill check done - {ok} of {len(results)} earned XP (Tools › Skill check)"

    def _run_autopilot(self, plan: Plan):
        """Train the whole account: nav.autopilot picks each step (task, level to reach, auto place)."""
        from lumberjack.nav import training
        from lumberjack.nav.autopilot import Autopilot, task_level
        log = logging.getLogger("autopilot")
        ap = Autopilot(target=plan.target, skip=plan.skip)
        log.info("Grindstone %s - autopilot: every skill to %d%s", version.label(), plan.target,
                 f" (skipping {', '.join(plan.skip)})" if plan.skip else "")
        n = 0
        try:
            while not self.stop_event.is_set():
                end = self.plan_ends_at
                if end and time.time() >= end:
                    log.info("Autopilot finished - its %g h are up", plan.max_hours or 0)
                    self.end_reason = f"Autopilot's time limit ({_hours_text(plan.max_hours)}) is up"
                    break
                if n and plan.auto_update and plan.resume:
                    self._maybe_update(log)
                levels = _levels()
                if not levels:
                    self._doing("waiting for the game")
                    self._sleep(10)
                    continue
                nxt = ap.next_step(levels)
                if nxt is None:
                    if all(task_level(t, levels) >= plan.target for t in ap.tasks):
                        log.info("Autopilot finished - every skill is at %d", plan.target)
                        self.end_reason = f"Autopilot finished - every skill is at {plan.target}"
                        break
                    self._doing("every remaining skill is resting after problems - waiting")
                    self._sleep(60)
                    continue
                task, until, minutes, why = nxt
                if end:
                    left = max(0.1, (end - time.time()) / 60)
                    minutes = left if minutes is None else min(minutes, left)
                n += 1
                place = training.AUTO if training.has_route(task) else None
                if place is None and training.underground(_my_tile()):
                    place = training.SURFACE       # e.g. cooking after hill giants: fires won't light below
                step = PlanStep(task=task, minutes=minutes, level=until, options=dict(AUTOPILOT_OPTIONS),
                                place=place)
                self.plan_info.update(step=n, of="auto", task=task, place=step.place, attempt=0,
                                      until=_until_text(step), why=why, autopilot=True,
                                      total=sum(v for k, v in levels.items() if k in TRAINED_SKILLS),
                                      total_target=plan.target * len(TRAINED_SKILLS))
                log.info("Autopilot: %s - %s", task, why)
                ok = self._run_step(step, plan, log)
                ap.done(task, ok)
                if not self.stop_event.is_set():
                    _mark_running(-1, end)
        except Exception as e:
            if not self.stop_event.is_set():
                self.error = _err_text(e)
                log.exception("Autopilot crashed")
        finally:
            if self.stop_event.is_set():
                self.end_reason = "stopped from the control panel"
            elif self.error:
                self.end_reason = f"crashed: {self.error}"
            self.plan_info = None
            if not self.updating:
                PLAN_RUNNING.unlink(missing_ok=True)
            log.info("Autopilot stopped")

    def _run_step(self, step: PlanStep, plan: Plan, log):
        """One plan step, with recovery. True when it ended well (time / level reached). With
        place "auto" the training route picks place + settings by level, and moves up a tier
        as soon as the level allows (within the same step and time limit)."""
        from lumberjack.nav import places, training
        base = load_settings().model_copy(update={"task": step.task, "max_logs": None,
                                                  "start_mode": "here", "start_spot": None})
        own = {k: v for k, v in (step.options or {}).items() if k in STEP_OPTIONS}
        deadline = time.monotonic() + step.minutes * 60 if step.minutes else None
        auto = step.place == training.AUTO and training.has_route(step.task)
        ok = False
        while not self.stop_event.is_set():
            left = None
            if deadline:
                left = (deadline - time.monotonic()) / 60
                if left <= 0:
                    return ok
            update, place_name, level = dict(own), step.place, step.level
            if auto:
                lv = step_level(step, _levels())
                place_name, tier = training.pick(step.task, lv)
                update = {**tier, **own}
                nxt = [t[0] for t in training.ROUTES[step.task] if lv is not None and t[0] > lv]
                if nxt and (level is None or nxt[0] < level):
                    level = nxt[0]                 # stop at the next tier, then carry on there
                self.plan_info["place"] = place_name
            s = base.model_copy(update={**update, "max_minutes": left})
            err = task_problem(s)
            if err:
                log.warning("Skipping step %s: %s", step.task, err)
                if self.check_results is not None:
                    self.check_results.setdefault(step.task, {"xp": {}, "reasons": []})["reasons"].append(
                        f"skipped: {err}")
                return False
            place = places.load().get(place_name) if place_name and place_name != training.AUTO else None
            label = f"Plan step {self.plan_info['step']}/{self.plan_info['of']}: {step.task}" + \
                (f" at {place_name}" if place_name else "") + f" - {_until_text(step)}"
            ok = self._run_with_recovery(s, log, label=label, place=place, place_name=place_name,
                                         teleport=plan.teleport, level=level)
            tier_up = auto and ok and level != step.level and str(self.last_reason).startswith("reached level")
            if not tier_up:
                return ok
            log.info("Level %d - moving up to the next training spot", level)
        return ok

    def _run_with_recovery(self, s, log, label, place=None, place_name=None, teleport=True, level=None,
                           first_as_is=False):
        """Run s.task until it ends well; when it ends badly (crash, stall, died, moved away...) get
        back to a clean state and to where it was working, and try again. Attempts are counted
        from the last run that went well for RESET_AFTER_S, so a long run can recover many times.
        first_as_is: the first attempt starts exactly as the user set it up (single-task Start)."""
        from lumberjack.core import gamestate
        from lumberjack.nav import places
        failures, attempt, back_to, checked = 0, 0, None, False
        deadline = time.monotonic() + s.max_minutes * 60 if s.max_minutes else None
        retries = 1 if self.check_results is not None else STEP_RETRIES     # a check shows failures
        while failures < retries:
            if self.stop_event.is_set():
                return False
            if deadline:                           # retries share the run's time limit
                left = deadline - time.monotonic()
                if left <= 0:
                    log.info("Done: time limit reached")
                    return True
                s = s.model_copy(update={"max_minutes": left / 60})
            attempt += 1
            if self.plan_info is not None:
                self.plan_info["attempt"] = failures + 1
            fresh = first_as_is and attempt == 1
            if not fresh:
                self._wait_for_game(log)
                target = back_to or place
                try:
                    ctx = self._ctx()
                except Exception as e:             # the game went away again: wait and retry
                    log.warning("Can't reach the game (%s) - waiting for it", e)
                    from lumberjack.core import gamestate as _gs
                    _gs.drop_shared()
                    failures += 1
                    self._sleep(10)
                    continue
                try:
                    self._recover(ctx)
                    # tidy first: a bank trip walks away, and the task starts where we stand
                    self._doing("tidying the backpack")
                    self._tidy_backpack(ctx, log, s.task)
                    if target:
                        where = place_name if target is place else f"{target['tile']}"
                        self._doing(f"travelling to {where}")
                        if not places.travel(ctx, gamestate.shared(), target, use_tele=teleport):
                            log.warning("Couldn't get to %s (attempt %d)", where, failures + 1)
                            failures += 1
                            continue
                finally:
                    ctx.inp.close()
                self._doing(None)
                s = s.model_copy(update={"start_mode": "here", "start_spot": None})
            log.info("%s%s", label, f" (attempt {attempt})" if attempt > 1 else "")
            reason, t0 = None, time.monotonic()
            from lumberjack import history
            wall0, before = time.time(), history.snapshot()
            try:
                self.bot = self.build_bot(s)
                if level:
                    self.bot.stop_at_level = (TASK_LEVEL_SKILLS.get(s.task, [s.task]), level)
                self.bot.stall_limit_s = STALL_LIMIT_S
                self.bot.run()
                reason = getattr(self.bot, "stop_reason", None) or "ended"
            except Exception as e:
                reason = f"crashed: {_err_text(e)}"
                log.exception("Run crashed")
            row = history.record(s.task, place_name, wall0, before, history.snapshot(), reason)
            if self.check_results is not None:
                r = self.check_results.setdefault(s.task, {"xp": {}, "reasons": []})
                for k, n in (row.get("xp") or {}).items():
                    r["xp"][k] = r["xp"].get(k, 0) + n
                r["reasons"].append(reason)
            if row["xp"]:
                log.info("This run: %s in %.0f min", ", ".join(f"+{n:,} {k} xp" for k, n in row["xp"].items()),
                         row["minutes"])
            if reason == "F12 pressed":
                self.stop_event.set()              # F12 stops everything, not just this run
            if self.stop_event.is_set():
                return False
            self.last_reason = reason
            if any(reason.startswith(g) for g in GOOD_ENDS):
                log.info("Done: %s", reason)
                return True
            if time.monotonic() - t0 > RESET_AFTER_S:
                failures = 0                       # it had been going well - a fresh start
            failures += 1
            back_to = getattr(self.bot, "displaced_from", None) or back_to
            if place is not None and not checked and failures < retries:
                checked = True
                moved = self._recheck_place(place_name, log)
                if moved:
                    place, back_to = moved, None
            if failures < retries:
                log.warning("Ended early (%s) - recovering, try %d of %d", reason, failures + 1, retries)
        log.warning("Giving up after %d tries in a row", retries)
        return False

    def _recheck_place(self, place_name, log):
        """A run at a training-route place ended badly: maybe the place is off (nothing to work
        on near it). Check it in the game; when its targets are a little way off, the place is
        moved there (saved under the same name). Returns the moved place, or None."""
        from lumberjack.core import gamestate
        from lumberjack.nav import places, training
        tiers = training.places_to_check().get(place_name)
        gs = gamestate.shared()
        if not tiers or gs is None:
            return None
        self._doing(f"checking {place_name}")
        try:
            ctx = self._ctx()
            try:
                res = training.check_place(ctx, gs, place_name, tiers, fix=True, log=log)
            finally:
                ctx.inp.close()
        except Exception as e:                 # a check is a bonus - never stop the plan for it
            log.warning("Couldn't check %s (%s)", place_name, e)
            return None
        finally:
            self._doing(None)
        if res.get("status") == "moved":
            return places.load().get(place_name)
        return None

    def _maybe_update(self, log):
        """Between plan steps: a newer version on GitHub -> pull it and restart the panel on it;
        the plan resumes from the marker. The game keeps running (an add-on change needs a game
        restart - until then the bot uses the add-on it has)."""
        if not version.status().get("update"):
            return
        if self._update_failed_at and time.monotonic() - self._update_failed_at < UPDATE_RETRY_S:
            return
        from lumberjack import updater
        log.info("A newer version is on GitHub - updating between steps")
        self._doing("updating")
        r = updater.update()
        new = version.code_version()
        if not r["ok"] or new[0] is None or version.RUNNING[0] is None or new[0] <= version.RUNNING[0]:
            log.warning("No update (%s) - carrying on with %s", r["message"], version.label())
            self._update_failed_at = time.monotonic()
            self._doing(None)
            return
        if r["addon_changed"]:
            log.warning("The game add-on changed too - restart the game (close it, start Grindstone) "
                        "when convenient to get it; the bot keeps working with the old one")
        log.info("Updated to v%s - restarting the panel, the plan resumes by itself", new[0])
        self.updating = True
        restart_panel_process()

    def _doing(self, what):
        if self.plan_info is not None:
            self.plan_info["doing"] = what
        self.doing = what

    def _wait_for_game(self, log):
        """The game closed (or the add-on is gone): wait for it rather than burning attempts."""
        from lumberjack.core import gamestate
        global _win
        gs = gamestate.shared()
        if gs is not None:
            try:
                gs.player()                       # really answering, not a stale connection
                return
            except gamestate.GameStateError:
                gamestate.drop_shared()
            except Exception:
                return
        log.warning("The game isn't answering - waiting for it (start it with the Grindstone icon)")
        self._doing("waiting for the game")
        while gamestate.shared() is None:
            self._sleep(5.0)
        with _win_lock:
            _win = None                            # a new client = a new window
        log.info("The game is back")
        self._doing(None)

    def _ctx(self):
        from lumberjack.actions import Ctx
        from lumberjack.core.input import AgentInput
        return Ctx(window(), AgentInput(), self._sleep)

    def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop_event.is_set():
                raise RuntimeError("stopped")
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))

    def _recover(self, ctx):
        """Get back to a clean state: no bank, menu, selection or dialog open, standard camera."""
        from lumberjack import actions, bank
        ctx.inp.move(260, 300)
        ctx.sleep(0.3)
        if bank.is_open(ctx.grab()):
            bank.close(ctx)
        actions.dismiss_dialog(ctx)
        actions.cancel_selection(ctx)
        actions.open_tab(ctx, "inventory")
        actions.reset_camera(ctx)

    def _tidy_backpack(self, ctx, log, task=None):
        """Before a task: make room. Near a bank, deposit everything `task` doesn't use - the last
        task's products, loot, other tasks' tools (they're spawned again when needed). Without a
        bank, drop only products, loot and the unused starter kit. Unknown items are only ever
        banked, never dropped."""
        from lumberjack import actions, bank
        from lumberjack.core import backpack
        inv = backpack.slots()
        if inv is None:
            return
        full = [i for i, s in enumerate(inv) if s["id"] >= 0]
        spare = [i for i in full if not (task and backpack.needed_for(inv[i]["key"], task))]
        products = [i for i in full if backpack.is_product(inv[i]["key"], food=False)
                    or (i in spare and inv[i]["key"] in backpack.STARTER)]
        if not products and len(spare) < TIDY_SPARE:
            return
        keep = set(full) - set(spare if task else products)
        if bank.gs_bank_trip(ctx, keep_slots=keep):
            log.info("Banked %d item(s) the next task doesn't need", len(full) - len(keep))
        elif products:
            log.info("No bank nearby - dropping %d item(s) from the last task", len(products))
            from lumberjack.core import gamestate
            gs = gamestate.shared()
            if gs is not None:                    # picked by name: drop them by name (the generic
                actions.drop_known(ctx, gs, products)   # drop refuses a starter sword as "not a product")
            else:
                actions.drop_all(ctx, keep=set(full) - set(products))

    def stop(self):
        self.stop_event.set()

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
        else:
            self.pause_event.set()

    def live_xp(self, b):
        """With game data, refresh XP/levels for the running task's skills every status poll
        (the bots otherwise only read the Stats tab after each load)."""
        from lumberjack.core import gamestate
        skills = TASK_SKILLS.get(getattr(b, "name", None) or "woodcutting", [])
        if not skills or gamestate.shared() is None:
            return
        if not hasattr(b, "xp"):
            from lumberjack.xp import XpTracker
            b.xp = XpTracker()
        for sk in skills:
            g = gamestate.skill(sk)
            if g is None:
                return
            b.xp.update(sk, {**g, "level": g["base"]})   # the table shows real levels, not boosted/current HP
            if isinstance(getattr(b, "levels", None), dict):
                b.levels[sk] = g["base"]

    def status(self):
        b = self.bot
        if b is not None and self.running:
            try:
                self.live_xp(b)
            except Exception:
                logging.getLogger("panel").debug("live xp failed", exc_info=True)
        st = {
            "manual": manual.busy,
            "recording": rec.info if rec.running else None,
            "running": self.running,
            "paused": self.pause_event.is_set(),
            "error": self.error,
            "state": (self.doing if self.running and self.doing else b.state if b else "idle"),
            "logs": _progress(b),
            "banked": getattr(b, "banked", 0) if b else 0,
            "burned": getattr(b, "burned", 0) if b else 0,
            "levels": getattr(b, "levels", {}) if b else {},
            "fletched": getattr(b, "fletched", 0) if b else 0,
            "cooked": getattr(b, "cooked", 0) if b else 0,
            "burnt": getattr(b, "burnt", 0) if b else 0,
            "eaten": getattr(b, "eaten", 0) if b else 0,
            "looted": getattr(b, "looted", 0) if b else 0,
            "task": getattr(b, "name", None) if b else None,
            "xp": b.xp.summary() if b and hasattr(b, "xp") else {},
            "stop_reason": (self.end_reason or getattr(b, "stop_reason", None) or self.error)
            if b and not self.running else None,
            "elapsed": 0,
            "per_hour": 0,
            "version": version.status(),
            "plan": self.plan_info,
            "game_data": _game_data_up(),
        }
        if b:
            el = time.monotonic() - b.started
            st["elapsed"] = int(el)
            st["per_hour"] = round(_progress(b) / (el / 3600)) if el > 30 else 0
        return st


class ManualRunner:
    """Runs one manual action (teleport, drop all, walk to spot...) at a time."""

    def __init__(self):
        self.busy = None

    def run(self, label, fn):
        if ctl.running:
            return "stop the bot first"
        if rec.running and label != "calibrate":
            return "stop the map recorder first"
        if self.busy:
            return f"busy with '{self.busy}'"
        self.busy = label

        def go():
            from lumberjack.actions import Ctx
            from lumberjack.core.input import AgentInput
            inp = None
            try:
                inp = AgentInput()
                fn(Ctx(window(), inp))
            except Exception as e:
                logging.getLogger("manual").exception("'%s' failed: %s", label, e)
            finally:
                if inp:
                    inp.close()
                self.busy = None

        threading.Thread(target=go, name=f"manual-{label}", daemon=True).start()
        return None


class Recorder:
    """Map recording inside the panel: you walk, it stitches the minimap."""

    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.info = {}

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, name, extend):
        from lumberjack.nav.worldmap import MAPS, WorldMap
        if self.running:
            return "already recording"
        if ctl.running:
            return "stop the bot first"
        name = "".join(c for c in name.strip().lower() if c.isalnum() or c in "_-") or "map"
        frame = window().grab()
        if extend and (MAPS / f"{name}.json").exists():
            wm = WorldMap.load(name)
            pos = wm.resume_at(frame)
            if pos is None:
                return f"can't find you on '{name}' - start inside the recorded area, or record a new map"
        else:
            wm = WorldMap(name)
            pos = wm.add(frame)
        self.stop_event.clear()
        self.info = {"map": name, "pos": pos, "points": len(wm.path)}
        log = logging.getLogger("recorder")
        log.info("Recording '%s' from %s - walk around, then press Stop recording", name, pos)

        def run():
            warned = False
            while not self.stop_event.is_set():
                p = wm.add(window().grab())
                if p:
                    self.info.update(pos=p, points=len(wm.path), lost=False)
                    if warned:
                        log.info("Back on track at %s", p)
                        warned = False
                elif wm.lost_frames >= 20 and not warned:  # ~5 s without a placeable frame
                    self.info["lost"] = True
                    log.warning("Lost track of you - walk back toward %s (the last recorded spot)", self.info["pos"])
                    warned = True
                time.sleep(0.15)
            wm.save()
            _localizers.pop(name, None)  # reload the map next time it's used
            log.info("Saved map '%s' (%d path points)", name, len(wm.path))

        self.thread = threading.Thread(target=run, name="recorder", daemon=True)
        self.thread.start()
        return None

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)


logbuf = LogBuffer()
logging.getLogger().addHandler(logbuf)
logging.getLogger().setLevel(logging.INFO)
ctl = BotController()
manual = ManualRunner()
rec = Recorder()
app = FastAPI(title="Grindstone")

_win = None
_win_lock = threading.Lock()


def window():
    global _win
    with _win_lock:
        if _win is not None:
            try:
                import win32gui
                if not win32gui.IsWindow(_win.hwnd):     # the game was restarted: a new window
                    _win = None
            except Exception:
                pass
        if _win is None:
            _win = GameWindow()
        return _win


@app.get("/icon.png")
def icon_png():
    return FileResponse(HERE.parents[0] / "assets" / "grindstone.png", media_type="image/png")


@app.get("/icon.svg")
def icon_svg():
    return FileResponse(HERE.parents[0] / "assets" / "grindstone.svg", media_type="image/svg+xml")


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


@app.get("/api/options")
def options():
    from lumberjack.ui import mouseover
    tt = []
    game = _names_from_game()
    for t in TREE_TYPES:
        ready = (mouseover.TEMPLATES / f"{t['id']}.png").exists() or game
        tt.append({**t, "ready": ready})
    lt = [{**t, "ready": mouseover.available(t["id"]) or game} for t in LOG_TYPES]
    return {"trees": tt, "full_actions": FULL_ACTIONS, "log_types": lt, "fishing": fishing_options(),
            "mining": mining_options(), "combat": combat_options(), "settings": load_settings().model_dump()}


def mining_options():
    from lumberjack.skills import mining
    from lumberjack.ui import mouseover
    return {"ores": [{"id": o, "level": lv} for o, lv in mining.ORE_LEVELS.items()],
            "ready": all(mouseover.available(n) for n in (mining.MINE_ACTION, mining.ROCK_TARGET))}


# monsters offered in the Combat card; names learned with "Learn monster" are added
COMMON_NPCS = ["chicken", "cow", "goblin", "giant_rat", "man", "woman", "guard", "al_kharid_warrior"]
NPC_NAMES = HERE.parents[0] / "assets" / "templates" / "npc_names.json"


COMMON_LOOT = ["bones", "feather", "cowhide", "raw_chicken", "raw_beef", "coins"]
LOOT_NAMES = HERE.parents[0] / "assets" / "templates" / "loot_names.json"


def _names(common, path):
    try:
        extra = json.loads(path.read_text())
    except (OSError, ValueError):
        extra = []
    return common + [n for n in extra if n not in common]


def _remember(name, common, path):
    names = _names(common, path)
    if name not in names:
        path.write_text(json.dumps([n for n in names if n not in common] + [name]))


def _clean_name(raw):
    return "".join(c for c in (raw or "").strip().lower().replace(" ", "_") if c.isalnum() or c == "_")


def npc_names():
    return _names(COMMON_NPCS, NPC_NAMES)


def combat_options():
    from lumberjack.skills import cooking
    from lumberjack.ui import mouseover
    game = _names_from_game()
    return {"npcs": [{"id": n, "name": n.replace("_", " ").capitalize(), "ready": mouseover.available(n) or game}
                     for n in npc_names()],
            "foods": [{"id": f, "name": f.replace("_", " ").capitalize(),
                       "ready": mouseover.available(f) or _names_from_game()}
                      for f in cooking.COOKED],
            "loot": [{"id": n, "name": n.replace("_", " ").capitalize(), "ready": mouseover.available(n) or game}
                     for n in _names(COMMON_LOOT, LOOT_NAMES)],
            "take": mouseover.available("take"), "bury": mouseover.available("bury"),
            "attack": mouseover.available("attack")}


@app.post("/api/mining/learn")
def mining_learn(f: LearnIn):
    from lumberjack.skills import mining
    if f.what == "rock":
        return _reply(manual.run("learn rock", mining.learn_hover))
    if f.what == "test":
        return _reply(manual.run("test rock vision",
                                 lambda ctx: logging.getLogger("mining").info(mining.describe_view(ctx.grab()))))
    return _reply(f"unknown calibration '{f.what}'")


@app.post("/api/combat/learn")
def combat_learn(f: LearnIn):
    from lumberjack.skills import combat
    from lumberjack.vision import health, npcs
    if f.what in ("npc", "loot"):
        name = _clean_name(f.name)
        if not name:
            return _reply("Type the name first")
        if f.what == "npc":
            def go(ctx):
                if npcs.calibrate_npc(ctx, name):
                    _remember(name, COMMON_NPCS, NPC_NAMES)
        else:
            def go(ctx):
                if combat.learn_loot(ctx, name):
                    _remember(name, COMMON_LOOT, LOOT_NAMES)
        return _reply(manual.run(f"learn {name}", go))
    if f.what == "bury":
        return _reply(manual.run("learn bury", combat.learn_bury))
    if f.what == "test":
        def test(ctx):
            frame = ctx.grab()
            found = npcs.find_npcs(frame)
            hp = health.hp_fraction(frame)
            bars = health.find_bars(frame)
            logging.getLogger("combat").info(
                "%d monster point(s) from the minimap%s; HP %s; %d health bar(s); fighting: %s",
                len(found), " - nearest at (%d, %d)" % (found[0].x, found[0].y) if found else "",
                f"{hp:.0%}" if hp is not None else "unreadable", len(bars), health.in_combat(frame, bars))
        return _reply(manual.run("test combat vision", test))
    return _reply(f"unknown calibration '{f.what}'")


def fishing_options():
    from lumberjack.skills import cooking, fishing
    from lumberjack.ui import mouseover
    return {
        "methods": [{"id": m, "level": lv, "tools": fishing.TOOLS[m],
                     "ready": not fishing.missing_now(m) or _names_from_game()}
                    for m, lv in fishing.METHOD_LEVELS.items()],
        "items": [{"id": n, "name": n.replace("_", " ").capitalize(), "ready": mouseover.available(n) or _names_from_game(),
                   "kind": "cooked" if n in cooking.COOKED else "burnt" if n in cooking.BURNT else "raw"}
                  for n in cooking.LEARNABLE],
        "fire": mouseover.available("fire") or _names_from_game(),
        "eat": mouseover.available("eat"),
    }


@app.post("/api/fishing/learn")
def fishing_learn(f: FishLearnIn):
    """Calibration for the Fishing task - each needs the game in a particular state (see the panel)."""
    from lumberjack.skills import cooking, fishing
    if f.what == "spot":
        method = load_settings().fish_method
        action = fishing.SPOT_LEFT_CLICK.get(method, ("net",))[0]
        return _reply(manual.run("learn fishing spot", lambda ctx: fishing.learn_hover(ctx, action)))
    if f.what == "fire":
        return _reply(manual.run("learn fire", cooking.learn_fire))
    if f.what == "cook":
        return _reply(manual.run("test cooking", cooking.test_cook))   # known tool icons are skipped
    if f.what == "test":
        def test(ctx):
            from lumberjack.vision import fishing_spots
            first = ctx.grab()
            ctx.sleep(fishing.FRAME_GAP_S)
            spots = fishing_spots.find_fishing_spots(ctx.grab(), first)
            logging.getLogger("fishing").info(
                "%d fishing spot candidate(s)%s", len(spots),
                ": " + ", ".join(f"({c.x}, {c.y}){' moving' if c.moving else ''}" for c in spots[:6]) if spots else
                " - stand where the ripples are in view (Manual > Reset camera)")
        return _reply(manual.run("test fishing vision", test))
    if f.what == "item":
        if not f.slot or not 1 <= f.slot <= 28:
            return _reply("Pick a backpack slot (1-28)")
        if f.name not in cooking.LEARNABLE:
            return _reply(f"Unknown item '{f.name}'")
        return _reply(manual.run(f"learn {f.name}", lambda ctx: cooking.learn_item(ctx, f.slot - 1, f.name)))
    return _reply(f"unknown calibration '{f.what}'")


@app.post("/api/settings")
def set_settings(s: Settings):
    save_settings(s)
    return {"ok": True}


@app.get("/api/items")
def items_list():
    from lumberjack import items
    return [{"group": g, "key": k, "name": n, "id": i, "note": note, "command": items.command(k)}
            for g, k, n, i, note in items.CATALOG]


@app.post("/api/spawn")
def spawn_item(sp: SpawnIn):
    from lumberjack import items
    if sp.key not in items.BY_KEY:
        return _reply(f"unknown item '{sp.key}'")
    amount = max(1, min(sp.amount, 100000))
    return _reply(manual.run(f"spawn {items.BY_KEY[sp.key][0]}", lambda ctx: items.spawn(ctx, sp.key, amount)))


@app.post("/api/update")
def update_now():
    """The panel's Update button: get the newest version, then restart the panel on it."""
    from lumberjack import updater
    if ctl.running:
        return _reply("Stop the bot first (a plan can update between steps: Plan tab > 'Update between steps')")
    if manual.busy or rec.running:
        return _reply("Wait for the current manual action / map recording to finish")
    r = updater.update()
    log = logging.getLogger("panel")
    if not r["ok"]:
        log.warning("Update: %s", r["message"])
        return _reply(f"Update failed: {r['message']}")
    log.info("Update: %s", r["message"])
    if not r["updated"] and (version.RUNNING[0] or 0) >= (version.code_version()[0] or 0):
        return {"ok": True, "restarting": False, "message": r["message"]}
    msg = f"Updated to v{r['to']} - the panel restarts in a moment"
    if r["addon_changed"]:
        msg += ". The game add-on changed too: close the game and start Grindstone again when convenient"
    threading.Timer(1.5, restart_panel_process).start()    # after this reply has gone out
    return {"ok": True, "restarting": True, "message": msg}


IDLE_UPDATE_EVERY_S = 600
IDLE_FOR_S = 120                       # nothing running this long before an idle update


def idle_updater():
    """While the bot isn't running, take a newer version from GitHub by itself (the panel restarts
    on it - nothing to interrupt), so a fix is there the next time Start is pressed. A running
    plan/Autopilot updates between steps instead."""
    from lumberjack import updater
    log = logging.getLogger("panel")
    idle_since = time.monotonic()
    while True:
        time.sleep(30)
        if ctl.running or manual.busy or rec.running:
            idle_since = time.monotonic()
            continue
        if time.monotonic() - idle_since < IDLE_FOR_S:
            continue
        st = version.status()                # (checks GitHub in the background, at most every few min)
        if not st.get("update"):
            time.sleep(IDLE_UPDATE_EVERY_S - 30)
            continue
        log.info("A newer version is on GitHub (v%s) and the bot is idle - updating", st.get("latest"))
        r = updater.update()
        behind = (version.RUNNING[0] or 0) < (version.code_version()[0] or 0)   # pulled but not running it
        if not r["ok"] or not (r["updated"] or behind):
            log.warning("Idle update didn't go through: %s", r["message"])
            time.sleep(IDLE_UPDATE_EVERY_S)
            continue
        if r["addon_changed"]:
            log.warning("The game add-on changed too - close the game and start it with the Grindstone icon "
                        "when convenient")
        log.info("Updated to v%s - restarting the panel", r["to"] or version.code_version()[0])
        restart_panel_process()
        return


@app.get("/api/running")
def running_get():
    """For the game watcher: is a bot or plan running (so a closed game should be restarted)?"""
    return {"running": ctl.running}


@app.get("/api/history")
def history_get(hours: float = 24):
    from lumberjack import history
    return history.summary(hours)


@app.get("/api/plan")
def plan_get():
    from lumberjack.nav import places
    return {"plan": load_plan().model_dump(), "places": places.load()}


@app.post("/api/plan")
def plan_set(p: Plan):
    save_plan(p)
    return {"ok": True}


@app.post("/api/plan/start")
def plan_start(p: Plan):
    save_plan(p)
    err = ctl.start_plan(p)
    return JSONResponse({"ok": err is None, "error": err}, status_code=200 if err is None else 400)


@app.post("/api/skillcheck")
def skillcheck_start():
    err = ctl.start_check()
    return JSONResponse({"ok": err is None, "error": err}, status_code=200 if err is None else 400)


@app.get("/api/skillcheck")
def skillcheck_get():
    report = ctl.last_check
    if report is None:
        try:
            report = (LOGS_DIR / "skill_check.txt").read_text(encoding="utf-8")
        except OSError:
            report = None
    return {"running": ctl.check_results is not None and ctl.running, "report": report}


@app.post("/api/places/save")
def place_save(pl: PlaceIn):
    from lumberjack.core import gamestate
    from lumberjack.nav import places
    gs = gamestate.shared()
    name = pl.name.strip()
    if not name:
        return _reply("Name the place first")
    if gs is None:
        return _reply("Saving places needs the game's own data - restart the game with the Grindstone icon")
    try:
        p = places.save_here(name, gs)
    except Exception as e:
        return _reply(str(e))
    logging.getLogger("plan").info("Saved place '%s' at %s", name, p["tile"])
    return {"ok": True, "place": p}


@app.post("/api/places/delete")
def place_delete(pl: PlaceIn):
    from lumberjack.nav import places
    places.delete(pl.name)
    return {"ok": True}


place_check = {"running": False, "results": []}


@app.post("/api/places/check")
def places_check(pl: PlaceIn | None = None):
    """Teleport to each built-in ★ place, count its targets, move it when they're elsewhere."""
    from lumberjack.core import gamestate
    from lumberjack.nav import training
    gs = gamestate.shared()
    if gs is None:
        return _reply("Checking places needs the game's own data")
    only = pl.name if pl and pl.name else None

    def go(ctx):
        place_check.update(running=True, results=[])
        try:
            for name, tiers in training.places_to_check().items():
                if only is None or name == only:
                    place_check["results"].append(training.check_place(ctx, gs, name, tiers,
                                                                       log=logging.getLogger("places")))
        finally:
            place_check["running"] = False
    return _reply(manual.run("check places", go))


@app.get("/api/places/check")
def places_check_status():
    return place_check


@app.post("/api/places/go")
def place_go(pl: PlaceIn):
    from lumberjack.core import gamestate
    from lumberjack.nav import places
    place = places.load().get(pl.name)
    if not place:
        return _reply("No such place")
    gs = gamestate.shared()
    if gs is None:
        return _reply("Travel needs the game's own data")
    return _reply(manual.run(f"go to {pl.name}", lambda ctx: places.travel(ctx, gs, place, use_tele=load_plan().teleport)))


@app.post("/api/check")
def check(s: Settings):
    return preflight(s)


@app.post("/api/start")
def start(s: Settings):
    save_settings(s)
    err = ctl.start(s)
    return JSONResponse({"ok": err is None, "error": err}, status_code=200 if err is None else 400)


# ---- maps & spots -----------------------------------------------------------------------
_localizers = {}


def _localizer(name):
    from lumberjack.nav.localizer import Localizer
    from lumberjack.nav.worldmap import WorldMap
    if name not in _localizers:
        _localizers[name] = Localizer(WorldMap.load(name))
    return _localizers[name]


@app.get("/api/maps")
def maps():
    from lumberjack.nav.worldmap import MAPS
    out = {}
    for meta in sorted(MAPS.glob("*.json")):
        try:
            out[meta.stem] = json.loads(meta.read_text()).get("spots", {})
        except Exception:
            continue
    return out


@app.get("/api/where")
def where(map: str):
    """Current position on a map - vision only, no input needed (works while the bot runs)."""
    try:
        fix = _localizer(map).locate(window().grab())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": fix.ok, "x": fix.x, "y": fix.y, "score": round(fix.score, 2)}


@app.post("/api/spots/save")
def spot_save(sp: SpotIn):
    from lumberjack.nav.spots import save_spot
    loc = _localizer(sp.map)
    fix = loc.locate(window().grab())
    if not fix.ok:
        return JSONResponse({"ok": False, "error": "Can't tell where you are on this map - are you inside the recorded area?"},
                            status_code=400)
    trees = [] if sp.kind in ("bank", "combat") else sp.trees   # fish: the methods it offers
    save_spot(loc.map, sp.name.strip() or f"spot{len(loc.map.spots) + 1}", fix.x, fix.y, trees, sp.kind)
    return {"ok": True, "x": fix.x, "y": fix.y}


@app.post("/api/spots/delete")
def spot_delete(sp: SpotIn):
    from lumberjack.nav.spots import delete_spot
    delete_spot(_localizer(sp.map).map, sp.name)
    return {"ok": True}


def _reply(err):
    return JSONResponse({"ok": err is None, "error": err}, status_code=200 if err is None else 400)


@app.post("/api/spots/goto")
def spot_goto(sp: SpotIn):
    s = _localizer(sp.map).map.spots.get(sp.name)
    if not s:
        return _reply("No such spot")

    def go(ctx):
        from lumberjack.nav.walker import Walker
        logging.getLogger("walker").info("Walking to '%s'", sp.name)
        Walker(ctx.win, ctx.inp, _localizer(sp.map).map).walk_to(s["x"], s["y"])

    return _reply(manual.run(f"walk to {sp.name}", go))


@app.post("/api/action/{name}")
def manual_action(name: str):
    from lumberjack import actions, bank
    from lumberjack.skills import firemaking

    def bank_now(ctx):
        keep = actions.carried_keepers(ctx)   # tools stay, logs go
        if bank.open_bank(ctx):
            bank.deposit_all(ctx, keep_slots=keep)
            bank.close(ctx)

    def get_tinderbox(ctx):
        if firemaking.find_tinderbox(ctx) is not None:
            logging.getLogger("manual").info("Already holding a tinderbox")
            return
        if bank.open_bank(ctx):
            bank.withdraw(ctx, "tinderbox")
            bank.close(ctx)

    def burn(ctx):
        t = firemaking.find_tinderbox(ctx)
        if t is None:
            logging.getLogger("manual").warning("No tinderbox in the backpack - use 'Get tinderbox' at a bank first")
            return
        n, _ = firemaking.burn_all(ctx, t, keep_slots=actions.carried_keepers(ctx))  # only logs
        logging.getLogger("manual").info("Burned %d logs", n)

    fns = {
        "teleport": actions.home_teleport,
        "drop_all": lambda ctx: actions.clear_materials(ctx, food=True, log=logging.getLogger("manual")),
        "reset_camera": actions.reset_camera,
        "bank_now": bank_now,
        "get_tinderbox": get_tinderbox,
        "burn": burn,
    }
    if name not in fns:
        return _reply(f"unknown action '{name}'")
    return _reply(manual.run(name, fns[name]))


@app.post("/api/record/start")
def record_start(r: RecordIn):
    return _reply(rec.start(r.map, r.extend))


@app.post("/api/record/stop")
def record_stop():
    rec.stop()
    return _reply(None)


@app.post("/api/calibrate")
def calibrate(c: CalibrateIn):
    """Hover trees in view and learn the unknown name (e.g. 'willow') from the hover text."""
    name = c.name.strip().lower()

    def go(ctx):
        from lumberjack.core import regions as R
        from lumberjack.tools.calibrate_name import save_name, unknown_name
        from lumberjack.tools.survey import loose_blobs
        log = logging.getLogger("calibrate")
        for b in loose_blobs(ctx.grab()):
            x, y, w, h = b["bbox"]
            for px, py in [(b["cx"], b["cy"]), (x + w // 2, y + h // 3)]:
                ctx.inp.move(px, py)
                time.sleep(0.25)
                strip = R.MOUSEOVER_TEXT.crop(ctx.grab())
                if unknown_name(strip, "chop_down", "cyan"):
                    save_name(strip, name, "cyan")
                    log.info("Learned '%s' from the hover text at (%d, %d)", name, px, py)
                    return
        log.warning("No unfamiliar tree name in view - stand right next to a %s and try again", name)

    return _reply(manual.run("calibrate", go))


@app.post("/api/stop")
def stop():
    ctl.stop()
    PLAN_RUNNING.unlink(missing_ok=True)       # also cancels a pending resume
    return {"ok": True}


@app.post("/api/pause")
def pause():
    ctl.toggle_pause()
    return {"ok": True, "paused": ctl.pause_event.is_set()}


def _overlay(f):
    """Draw what the current task's vision sees (the bot's view in the panel)."""
    task = getattr(ctl.bot, "name", None) if ctl.running else load_settings().task
    try:
        if task == "mining":
            from lumberjack.vision import rocks
            return rocks.draw(f, rocks.find_rocks(f))
        if task in ("combat", "ranged"):
            from lumberjack.vision import health, npcs
            return health.draw(npcs.draw(f))
        if task == "fishing":
            from lumberjack.vision import fishing_spots
            return fishing_spots.draw(f, fishing_spots.find_fishing_spots(f))
    except Exception:
        logging.getLogger("panel").debug("overlay failed", exc_info=True)
    return trees.draw(f, trees.find_trees(f))


def _annotated_jpeg():
    global _win
    try:
        f = window().grab()
    except Exception:
        with _win_lock:
            _win = None  # game restarted / closed - re-find next time
        return None
    img = _overlay(f)
    n = inventory.count(f) if inventory.REF.exists() else -1
    st = ctl.status()
    label = f"{st['state']}  |  inv {n}/28" if n >= 0 else st["state"]
    cv2.rectangle(img, (4, 318), (4 + 9 * len(label), 338), (0, 0, 0), -1)
    cv2.putText(img, label, (8, 333), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (120, 255, 120), 1)
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return jpg.tobytes() if ok else None


@app.get("/stream.mjpg")
async def stream():
    async def gen():
        while True:
            jpg = await asyncio.to_thread(_annotated_jpeg)
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            await asyncio.sleep(STREAM_INTERVAL_S)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


STREAM_INTERVAL_S = 0.33           # ~3 frames/s: plenty to watch, light on the game


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    last = max(0, logbuf.seq - 30)  # new viewers get the last 30 lines
    try:
        while True:
            new = logbuf.since(last)
            if new:
                last = new[-1][0]
            await sock.send_json({**ctl.status(), "log": [t for _, t in new]})
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        pass


_running_plan = None        # the plan being run (Autopilot isn't saved in plan.json): resumed as is


def _mark_single(s, ends_at):
    try:
        PLAN_RUNNING.parent.mkdir(parents=True, exist_ok=True)
        PLAN_RUNNING.write_text(json.dumps({"version": version.label(), "single": s.model_dump(),
                                            "ends_at": ends_at}))
    except OSError:
        pass


def _mark_running(last_step, ends_at=None):
    try:
        PLAN_RUNNING.parent.mkdir(parents=True, exist_ok=True)
        mark = {"version": version.label(), "last": last_step, "ends_at": ends_at}
        if _running_plan is not None:
            mark["plan"] = _running_plan.model_dump()
        PLAN_RUNNING.write_text(json.dumps(mark))
    except OSError:
        pass


def restart_panel_process():
    """Start a helper that starts the panel again once this process has exited, then exit."""
    import os
    import subprocess
    import sys
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(pyw if pyw.exists() else py), "-m", "lumberjack.start", "restart-panel", str(os.getpid())],
                     cwd=str(HERE.parents[1]), creationflags=flags)
    logging.shutdown()
    os._exit(0)


# ---- freeze watchdog --------------------------------------------------------------------------
FREEZE_S = 90                      # the game's loop counter stuck this long (bot running) = frozen
LOGS_DIR = HERE.parents[1] / "logs"


def freeze_watchdog():
    """While a bot runs: if the game's own loop counter stops (or the add-on stops answering) for
    FREEZE_S, save a thread dump (what froze, for fixing it) and end the frozen game - the game
    watcher (lumberjack.start) starts it again and the bot carries on after you log in."""
    from lumberjack.core.gamestate import GameState, GameStateError
    log = logging.getLogger("watchdog")
    gs = GameState()
    last, since = None, time.monotonic()
    while True:
        time.sleep(10)
        if not ctl.running:
            last, since = None, time.monotonic()
            continue
        try:
            if window().minimized():
                last, since = None, time.monotonic()        # a minimized game may pause - not frozen
                continue
        except Exception:
            pass
        try:
            loop = gs._q("tick")["loop"]
        except GameStateError as e:
            if "unknown state" in str(e) or "refused" in str(e).lower():
                last, since = None, time.monotonic()        # old add-on, or the game isn't running
                continue
            loop = None                                     # not answering: counts as stuck
        if loop is not None and loop != last:
            last, since = loop, time.monotonic()
            continue
        if time.monotonic() - since < FREEZE_S:
            continue
        log.error("The game has been frozen for %d s - saving a report and restarting it", FREEZE_S)
        handle_freeze(log)
        last, since = None, time.monotonic() + 120          # give the restart time


def handle_freeze(log):
    from lumberjack import procs
    from lumberjack.core import agent
    try:
        pid = agent.find_client_pid()
    except Exception as e:
        log.warning("Couldn't find the game process (%s)", e)
        return
    LOGS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        jcmd = agent.find_jdk_bin() / "jcmd.exe"
        dump = procs.run([str(jcmd), str(pid), "Thread.print"], capture_output=True, text=True, timeout=30)
        (LOGS_DIR / f"game_freeze_{stamp}.log").write_text(dump.stdout + dump.stderr, encoding="utf-8")
        log.info("Saved what the game was doing to logs/game_freeze_%s.log - please send it over", stamp)
    except Exception as e:
        log.warning("Couldn't save the freeze report (%s)", e)
    (LOGS_DIR / f"game_frozen_{pid}.log").write_text(stamp)     # tells the game watcher to restart it
    procs.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


# ---- auto login -------------------------------------------------------------------------------
LOGIN_FILE = HERE.parents[0] / "configs" / "login.json"     # this PC only (configs/ is never synced)
LOGIN_RETRY_S = 45
LOGIN_GIVE_UP = 3                   # failed logins in a row -> pause (a wrong password, a locked account)
LOGIN_PAUSE_S = 1800


def load_login():
    try:
        d = json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
        return {"user": d.get("user", ""), "password": d.get("password", ""), "enabled": bool(d.get("enabled"))}
    except (OSError, ValueError):
        return {"user": "", "password": "", "enabled": False}


def auto_login():
    """Whenever the game sits at its login screen and auto login is on: log in (the client's own
    login action, via the add-on). Covers the first start, a crash restart and a disconnect."""
    from lumberjack.core.gamestate import GameState, GameStateError
    log = logging.getLogger("login")
    gs = GameState()
    last_try, fails, paused_until = float("-inf"), 0, 0.0     # -inf: the clock may start near 0 at boot
    while True:
        time.sleep(5)
        cfg = load_login()
        if not (cfg["enabled"] and cfg["user"] and cfg["password"]) or time.monotonic() < paused_until:
            continue
        try:
            if gs._q("tick").get("state") != 10 or time.monotonic() - last_try < LOGIN_RETRY_S:
                continue
            r = gs.login(cfg["user"], cfg["password"])
        except GameStateError:
            continue                          # no game / an add-on without login: nothing to do
        if not r.get("ok"):
            continue
        last_try = time.monotonic()
        log.info("Logging in as %s", cfg["user"])
        end = time.monotonic() + 30
        state = 10
        while time.monotonic() < end:
            time.sleep(1)
            try:
                state = gs.login_status().get("state")
            except GameStateError:
                break
            if state == 30:
                break
        if state == 30:
            log.info("Logged in")
            fails = 0
            _click_play(gs, log)
            continue
        fails += 1
        log.warning("Login didn't go through (%d of %d)", fails, LOGIN_GIVE_UP)
        if fails >= LOGIN_GIVE_UP:
            log.warning("Auto login paused for %d min - check the username and password (Tools tab)",
                        LOGIN_PAUSE_S // 60)
            paused_until, fails = time.monotonic() + LOGIN_PAUSE_S, 0


PLAY_POINT = (386, 350)             # the welcome screen's "CLICK HERE TO PLAY" (fixed-size client)


def _click_play(gs, log):
    """Past the welcome screen ("Welcome to 2009Scape ... CLICK HERE TO PLAY") that follows a
    login: click its button - found by its text, else at its usual place while the screen shows."""
    from lumberjack.core.input import AgentInput
    from lumberjack.ui import widgets
    end = time.monotonic() + 15
    while time.monotonic() < end:
        time.sleep(1)
        btn = [w for w in widgets.find(gs, "click here to play") if w["w"] > 0 and w["h"] > 0]
        shown = btn or widgets.find(gs, "last logged in") or widgets.find(gs, "welcome to")
        if not shown:
            continue
        x, y = widgets.center(btn[0]) if btn else PLAY_POINT
        try:
            inp = AgentInput()
            inp.click(x + random.randint(-6, 6), y + random.randint(-2, 2))
        except Exception as e:
            log.warning("Couldn't click 'Click here to play' (%s)", e)
            return
        log.info("Clicked 'Click here to play'")
        time.sleep(2)
        if not (widgets.find(gs, "click here to play") or widgets.find(gs, "last logged in")):
            return


class LoginIn(BaseModel):
    user: str = ""
    password: str | None = None       # None = keep the saved one
    enabled: bool = False


@app.get("/api/login")
def login_get():
    c = load_login()
    return {"user": c["user"], "enabled": c["enabled"], "has_password": bool(c["password"])}


@app.post("/api/login")
def login_set(li: LoginIn):
    c = load_login()
    c.update(user=li.user.strip(), enabled=li.enabled)
    if li.password:
        c["password"] = li.password
    LOGIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOGIN_FILE.write_text(json.dumps(c), encoding="utf-8")
    return {"ok": True, "has_password": bool(c["password"])}


def _resume_single(s):
    """Start the task that was running when the panel closed, once the game answers."""
    log = logging.getLogger("panel")

    def go():
        from lumberjack.core import gamestate
        log.info("%s was running when the panel closed - resuming it once the game answers", s.task)
        end = time.monotonic() + RESUME_WAIT_S
        while time.monotonic() < end:
            if ctl.running or not PLAN_RUNNING.exists():
                return
            if gamestate.shared() is not None:
                try:
                    ends_at = json.loads(PLAN_RUNNING.read_text()).get("ends_at")
                except (OSError, ValueError, AttributeError):
                    ends_at = None
                if ends_at:
                    left = (ends_at - time.time()) / 60
                    if left <= 0:
                        log.info("Its time was up while the panel was closed - not resuming")
                        PLAN_RUNNING.unlink(missing_ok=True)
                        return
                    s2 = s.model_copy(update={"max_minutes": left})
                else:
                    s2 = s
                err = ctl.start(s2)
                if err:
                    log.warning("Couldn't resume %s: %s", s.task, err)
                    PLAN_RUNNING.unlink(missing_ok=True)
                return
            time.sleep(10)
        PLAN_RUNNING.unlink(missing_ok=True)

    threading.Thread(target=go, name="resume", daemon=True).start()


def resume_plan():
    """The panel closed while a plan ran (an update, a crash, the PC slept): start it again once
    the game answers. Stopping the plan (Stop / F12 / it finished) removes the marker."""
    if not PLAN_RUNNING.exists():
        return
    plan = load_plan()
    single = None
    try:                                        # the plan that was running (Autopilot included)
        mark = json.loads(PLAN_RUNNING.read_text())
        if mark.get("plan"):
            plan = Plan(**mark["plan"])
        if mark.get("single"):                  # or one task started with Start
            single = Settings(**mark["single"])
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if single is not None:
        return _resume_single(single)
    if not plan.resume or not (plan.steps or plan.autopilot):
        PLAN_RUNNING.unlink(missing_ok=True)
        return
    log = logging.getLogger("plan")

    def go():
        from lumberjack.core import gamestate
        log.info("A plan was running when the panel closed - resuming it once the game answers")
        end = time.monotonic() + RESUME_WAIT_S
        while time.monotonic() < end:
            if ctl.running or not PLAN_RUNNING.exists():
                return                              # the user started something / gave up
            if gamestate.shared() is not None:
                try:
                    mark = json.loads(PLAN_RUNNING.read_text())
                    after, ends_at = int(mark.get("last", -1)), mark.get("ends_at")
                except (OSError, ValueError, AttributeError, TypeError):
                    after, ends_at = -1, None
                if ends_at and time.time() >= ends_at:
                    log.info("The plan's time was up while the panel was closed - not resuming")
                    PLAN_RUNNING.unlink(missing_ok=True)
                    return
                if not plan.loop and plan.order != "lowest" and after >= len(plan.steps) - 1:
                    log.info("The plan had finished its last step - nothing to resume")
                    PLAN_RUNNING.unlink(missing_ok=True)
                    return
                err = ctl.start_plan(plan, after, ends_at)
                if err:
                    log.warning("Couldn't resume the plan: %s", err)
                    PLAN_RUNNING.unlink(missing_ok=True)
                return
            time.sleep(10)
        log.warning("The game didn't answer for %d min - not resuming the plan", RESUME_WAIT_S // 60)
        PLAN_RUNNING.unlink(missing_ok=True)

    threading.Thread(target=go, name="resume", daemon=True).start()


def main():
    print(f"Grindstone control panel {version.label()}: http://127.0.0.1:{PORT}")
    resume_plan()
    threading.Thread(target=freeze_watchdog, name="freeze-watchdog", daemon=True).start()
    threading.Thread(target=auto_login, name="auto-login", daemon=True).start()
    threading.Thread(target=idle_updater, name="idle-updater", daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
