"""Clicking things the game's own data located (core.gamestate) - shared by the bots.

    use_option(ctx, gs, points, verb, subject)   hover the points; left-click when the menu's
                                                 top entry is "<verb> <subject>", or right-click
                                                 and pick that row when it's further down
    walk_toward(ctx, me_tile, tile)              click the minimap toward a world tile
    walk_to_tile(ctx, gs, tile, arrive)          ...repeatedly, until we're within `arrive` tiles
    on_screen(x, y)                              inside the game view, clear of the hover text
"""
import logging
import random

from lumberjack.core import regions as R
from lumberjack.core.gamestate import menu_row_point, top_entry

log = logging.getLogger("interact")

MM_PER_TILE = 4          # minimap px per tile
MAX_WALK_MM = 46         # minimap clicks stay well inside the disc


def on_screen(x, y, margin=4):
    v = R.VIEWPORT
    if not (v.x + margin <= x < v.x + v.w - margin and v.y + margin <= y < v.y + v.h - margin):
        return False
    return not R.MOUSEOVER_TEXT.contains(x, y)


def _match(entry, verb, subject):
    if not entry or entry["verb"].lower() != verb.lower():
        return False
    # NPC subjects carry a "(level-N)" suffix; compare the name part only
    return entry["subject"].split("(level")[0].strip().lower() == subject.lower()


def use_option(ctx, gs, points, verb, subject, settle=(0.08, 0.14)):
    """Hover each on-screen point; do "<verb> <subject>" there. Returns the point used or None."""
    for x, y in points:
        if not on_screen(x, y):
            continue
        x, y = x + random.randint(-2, 2), y + random.randint(-2, 2)
        ctx.inp.move(x, y)
        ctx.sleep(random.uniform(*settle))
        menu = gs.menu()
        if _match(top_entry(menu), verb, subject):
            ctx.inp.click()
            return x, y
        if any(_match(e, verb, subject) for e in menu.get("entries") or []):
            ctx.inp.right_click(x, y)
            ctx.sleep(random.uniform(0.2, 0.3))
            menu = gs.menu()
            e = next((e for e in menu.get("entries") or [] if _match(e, verb, subject)), None)
            if menu.get("open") and e:
                rx, ry = menu_row_point(menu, e["row"])
                ctx.inp.click(rx + random.randint(-12, 12), ry + random.randint(-1, 1))
                return x, y
            ctx.inp.move(x, max(R.VIEWPORT.y + 30, y - 90))   # leaving closes the menu
            ctx.sleep(0.2)
    return None


SPREAD_PX = 26           # per extra tile of a big object: how far around its point to also try


def valid_screen(pt):
    """The add-on answers [-1, -1] when it couldn't place something on screen."""
    return bool(pt) and pt[0] > 0 and pt[1] > 0


def points_for(thing):
    """Hover points for a located NPC/loc/item: between ground and body first. A big object
    (an altar, anvil, patch, obstacle - "size" over one tile) gets a spread around those too:
    its reported point can sit on the floor next to the model ("Walk here"). No usable screen
    point: no points (a spread around [-1, -1] hovered the top-left corner - "Walk here")."""
    if not valid_screen(thing.get("screen")):
        return []
    (gx, gy) = thing["screen"]
    (bx, by) = thing.get("body") if valid_screen(thing.get("body")) else thing["screen"]
    pts = [((gx + bx) // 2, (gy + by) // 2), (gx, gy - 6), (bx, by), (gx, gy)]
    size = max(thing.get("size") or [1])
    if size > 1:
        r = SPREAD_PX * min(size - 1, 2)
        cx, cy = (gx + bx) // 2, (gy + by) // 2
        pts += [(cx + dx, cy + dy) for dy in (-r, 0, r) for dx in (-r, 0, r) if (dx, dy) != (0, 0)]
    return pts


def screen_fit(locs):
    """A tile -> screen map fitted to the one-tile objects the add-on did place on screen (the
    standard camera is a fixed top-down view, so it's close to linear). None with too few."""
    import numpy as np
    pts = [(l["tile"][0], l["tile"][1], l["screen"][0], l["screen"][1]) for l in locs
           if valid_screen(l.get("screen")) and max(l.get("size") or [1]) == 1 and on_screen(*l["screen"])]
    if len(pts) < 4:
        return None
    a = np.array([[tx, ty, 1.0] for tx, ty, _, _ in pts])
    coef_x = np.linalg.lstsq(a, np.array([p[2] for p in pts], float), rcond=None)[0]
    coef_y = np.linalg.lstsq(a, np.array([p[3] for p in pts], float), rcond=None)[0]
    if np.linalg.matrix_rank(a) < 3:
        return None
    return lambda tx, ty: (int(round(coef_x @ [tx, ty, 1.0])), int(round(coef_y @ [tx, ty, 1.0])))


def footprint_points(loc, fit):
    """Screen points over every tile a (big) object covers - both ways round, it may be
    rotated - centre tiles first. From screen_fit's map."""
    if fit is None:
        return []
    w, l = (loc.get("size") or [1, 1])[:2]
    x0, y0 = loc["tile"]
    tiles = {(x0 + i, y0 + j) for i in range(w) for j in range(l)} | \
            {(x0 + i, y0 + j) for i in range(l) for j in range(w)}
    cx, cy = x0 + (max(w, l) - 1) / 2, y0 + (max(w, l) - 1) / 2
    out = []
    for tx, ty in sorted(tiles, key=lambda t: abs(t[0] - cx) + abs(t[1] - cy)):
        x, y = fit(tx, ty)
        if on_screen(x, y):
            out.append((x, y))
    return out


def walk_toward(ctx, me_tile, tile, frame=None):
    """Click the minimap toward a world tile (north-up offset turned to the compass)."""
    from lumberjack.nav.localizer import Localizer
    from lumberjack.vision import minimap as MM
    dx = (tile[0] - me_tile[0]) * MM_PER_TILE
    dy = -(tile[1] - me_tile[1]) * MM_PER_TILE      # world y grows north, the minimap's up
    angle = MM.heading(frame if frame is not None else ctx.grab()) or 0.0
    s = MM.click_scale()
    lx, ly = Localizer.map_to_minimap(dx * s, dy * s, angle)
    r = (lx * lx + ly * ly) ** 0.5
    if r > MAX_WALK_MM:
        lx, ly = lx * MAX_WALK_MM / r, ly * MAX_WALK_MM / r
    ctx.inp.click(MM.CENTER[0] + lx, MM.CENTER[1] + ly)


def tiles_apart(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def wait_until_still(ctx, gs, timeout=12.0):
    import time
    end = time.monotonic() + timeout
    ctx.sleep(0.6)
    while time.monotonic() < end and gs.player().get("moving"):
        ctx.sleep(0.2)


def walk_to_tile(ctx, gs, tile, arrive=2, max_clicks=15):
    """Walk to a world tile by minimap clicks (each up to ~11 tiles), checking where we are in
    the game's own data after every one. True once within `arrive` tiles."""
    stuck = 0
    for _ in range(max_clicks):
        me = gs.player()["tile"]
        d = tiles_apart(me, tile)
        if d <= arrive:
            return True
        walk_toward(ctx, me, tile)
        wait_until_still(ctx, gs)
        stuck = stuck + 1 if tiles_apart(gs.player()["tile"], tile) >= d else 0
        if stuck >= 2:
            log.warning("Can't get closer to %s (%d tiles away)", tile, d)
            return False
    return tiles_apart(gs.player()["tile"], tile) <= arrive
