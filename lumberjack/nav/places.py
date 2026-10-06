"""Named places (world tiles from the game's own data) and getting there.

A place is where a task should run - "Draynor fish", "Lumbridge trees" - saved by standing
on it and pressing Save in the panel. Stored in assets/places.json, which syncs between PCs
with the save. Travel:

  1. already there (within ARRIVE tiles)        -> done
  2. teleport (singleplayer admin: ::tele x y plane), checked in the game data -> done
  3. close enough to walk (WALK_MAX tiles)      -> minimap clicks toward the tile
  4. far: Home Teleport to Lumbridge, then 3

Everything here needs the game's own data (core.gamestate) - positions are world tiles.
"""
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("travel")

PLACES = Path(__file__).resolve().parents[1] / "assets" / "places.json"
ARRIVE = 3              # tiles from the place that count as "there"
WALK_MAX = 60           # walk without teleporting up to this far
TELE_WAIT_S = 8.0
teleported_at = float("-inf")   # (monotonic) our last ::tele - the jump guard leaves it alone


def load_saved():
    """Only the places saved from the panel (assets/places.json)."""
    try:
        data = json.loads(PLACES.read_text())
        return {k: v for k, v in data.get("places", {}).items() if "tile" in v}
    except (OSError, ValueError):
        return {}


def load():
    """Saved places plus the built-in training places (nav.training); a saved place with the
    same name as a built-in one replaces it."""
    from lumberjack.nav.training import STARTER_PLACES
    out = {k: dict(v, builtin=True) for k, v in STARTER_PLACES.items()}
    out.update(load_saved())
    return out


def save_all(places):
    PLACES.parent.mkdir(parents=True, exist_ok=True)
    PLACES.write_text(json.dumps({"places": dict(sorted(places.items()))}, indent=1))


def save_here(name, gs, note=""):
    """Save where we're standing as place `name`. Returns the place."""
    me = gs.player()
    if not me.get("logged_in", True) or "tile" not in me:
        raise RuntimeError("not logged in")
    place = {"tile": list(me["tile"]), "plane": me.get("plane", 0)}
    if note:
        place["note"] = note
    places = load_saved()
    places[name] = place
    save_all(places)
    return place


def delete(name):
    places = load_saved()
    if places.pop(name, None) is not None:
        save_all(places)


def tiles_apart(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _wait_arrival(ctx, gs, tile, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ctx.sleep(0.5)
        try:
            if tiles_apart(gs.player()["tile"], tile) <= ARRIVE:
                return True
        except Exception as e:                  # the new area still loading: look again
            log.debug("arrival check: %s", e)
    return False


def _clear_dialogs(ctx, gs):
    try:
        from lumberjack.ui import widgets
        for _ in range(4):
            if not widgets.continue_dialog(ctx, gs):
                break
            ctx.sleep(0.6)
    except Exception as e:
        log.debug("clearing dialogs: %s", e)


def teleport(ctx, gs, tile, plane=0):
    """Admin teleport (::tele). True once the game data shows us there - with the standard
    camera, so what we came for is on screen (an off-screen altar cost Runecrafting its clicks)."""
    global teleported_at
    arrived = False
    for attempt in range(2):
        if attempt:                            # an open dialog swallows the typing: clear it, once more
            log.info("::tele didn't take - clearing any dialog and trying again")
            _clear_dialogs(ctx, gs)
        ctx.inp.move(260, 300)
        teleported_at = time.monotonic()
        ctx.inp.type_text(f"::tele {tile[0]} {tile[1]} {plane}", enter=True)
        arrived = _wait_arrival(ctx, gs, tile, TELE_WAIT_S)
        teleported_at = time.monotonic()
        if arrived:
            break
    if not arrived:
        return False
    try:
        from lumberjack import actions
        actions.reset_camera(ctx)
    except Exception as e:                     # a camera hiccup isn't a failed teleport
        log.debug("camera reset after teleport: %s", e)
    return True


def travel(ctx, gs, place, use_tele=True):
    """Get to `place` ({'tile', 'plane'}). True when we're there."""
    from lumberjack import actions
    from lumberjack.core import interact
    tile, plane = place["tile"], place.get("plane", 0)
    me = gs.player()
    if tiles_apart(me["tile"], tile) <= ARRIVE and me.get("plane", 0) == plane:
        return True
    if use_tele:
        log.info("Teleporting to %s", tile)
        if teleport(ctx, gs, tile, plane):
            actions.reset_camera(ctx)
            return True
        log.warning("::tele didn't take us there - walking instead")
    d = tiles_apart(gs.player()["tile"], tile)
    if d > WALK_MAX or gs.player().get("plane", 0) != plane:
        log.info("%d tiles away - Home Teleport first", d)
        actions.home_teleport(ctx)
        d = tiles_apart(gs.player()["tile"], tile)
        if d > WALK_MAX * 2:
            log.warning("Still %d tiles away after Home Teleport - can't walk that far", d)
            return False
    log.info("Walking to %s (%d tiles)", tile, d)
    ok = interact.walk_to_tile(ctx, gs, tile, arrive=ARRIVE, max_clicks=40)
    actions.reset_camera(ctx)
    return ok
