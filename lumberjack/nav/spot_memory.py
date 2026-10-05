"""Where fishing spots have been seen (world tiles, by method verb), so the bot can find its way
back when none are in range - the game only tells us about NPCs within ~15 tiles, so a spot a
little up the shore is invisible until we walk toward it.

    remember(spot)                    a 'Fishing spot' NPC from the game data (tile + ops)
    nearest("Net", me_tile, 80)       the closest remembered tile offering Net, else a ★ fishing
                                      place offering it (nav.training), else None

Saved in assets/fishing_spots.json, synced between PCs with the save like places.json.
"""
import json
import threading
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "assets" / "fishing_spots.json"
SAME_TILES = 3                 # a spot this close to a known one is the same spot (they shuffle)
KEEP = 60                      # tiles remembered per method
# built-in fallbacks: which methods the ★ fishing places offer
PLACE_VERBS = {"★ Draynor fishing": ("Net", "Bait"), "★ Lumbridge river": ("Lure", "Bait"),
               "★ Catherby fishing": ("Cage", "Harpoon", "Net", "Bait")}

_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            _cache = {k: [tuple(t) for t in v] for k, v in json.loads(FILE.read_text()).items()}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _far(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def remember(spot):
    """Note a fishing spot NPC's tile under each of its options. True if something was new."""
    tile = tuple(spot.get("tile") or ())
    if len(tile) != 2:
        return False
    with _lock:
        data = _load()
        new = False
        for verb in spot.get("ops") or []:
            known = data.setdefault(verb, [])
            if all(_far(tile, t) > SAME_TILES for t in known):
                known.append(tile)
                del known[:-KEEP]
                new = True
        if new:
            try:
                FILE.parent.mkdir(parents=True, exist_ok=True)
                FILE.write_text(json.dumps({k: [list(t) for t in v] for k, v in sorted(data.items())}, indent=1))
            except OSError:
                pass
        return new


def nearest(verb, me, max_dist=80):
    """(tile, source) of the closest place a `verb` spot was seen within max_dist, or (None, None)."""
    with _lock:
        cands = [(t, "remembered") for t in _load().get(verb, [])]
    from lumberjack.nav.training import STARTER_PLACES
    cands += [(tuple(STARTER_PLACES[p]["tile"]), p) for p, verbs in PLACE_VERBS.items()
              if verb in verbs and p in STARTER_PLACES]
    cands = [(t, s) for t, s in cands if _far(t, me) <= max_dist]
    if not cands:
        return None, None
    return min(cands, key=lambda c: _far(c[0], me))
