"""The game's own data - menu entries, NPCs, backpack, skills, player - read inside the client
by the input add-on (agent/src/lumberjack/GameState.java), instead of recognised from pixels.

    gs = GameState()
    if gs.available():
        gs.npcs("Fishing spot")   # [{'name', 'ops', 'tile', 'dist', 'screen': [x, y], ...}]
        gs.menu()                 # {'open', 'targeting', 'entries': [{'verb', 'subject', 'kind', 'row'}...]}
        gs.inv()                  # 28 x {'id', 'count', 'name'}
        gs.skills()               # {'fishing': {'level', 'boosted', 'xp'}, ...}
        gs.player()               # {'tile', 'anim', 'moving', 'yaw', ...}
        gs.ground(10)             # items on the ground nearby, nearest first

Needs the add-on built from this repo's source (rebuilt when the client starts); with an older
add-on or another client every call raises GameStateError and the bots use pixels instead.
"""
import json
import logging
import re
import socket
import threading
import time

from lumberjack.core.agent import PORT

SKILLS = ["attack", "defence", "strength", "hitpoints", "ranged", "prayer", "magic", "cooking",
          "woodcutting", "fletching", "fishing", "firemaking", "crafting", "smithing", "mining",
          "herblore", "agility", "thieving", "slayer", "farming", "runecrafting", "hunter",
          "construction", "summoning"]
# menu subjects carry the client's colour tags: <col=ffff00>Fishing spot
_TAG = re.compile(r"<col=([0-9a-fA-F]{6})>|</col>|<\)4col>|<[^>]*>")
KINDS = {"ffff00": "npc", "00ffff": "object", "ff9040": "item", "ffffff": "player"}
MENU_ROW_H = 15
MENU_FIRST_BASELINE = 31   # row 0's text baseline, below the menu's top edge


TIMEOUT_S = 4.0                 # most queries answer in milliseconds
HEAVY_TIMEOUT_S = 12.0          # scans of the scene / bank
HEAVY = {"locs", "ground", "npcs", "inv", "widgets"}
SLOW_S = 1.5
LOADING_STATES = {25, 28}       # the client's gameState while it loads a new area
LOADING_WAIT_S = 10.0
log = logging.getLogger("gamestate")


class GameStateError(RuntimeError):
    pass


def clean(subject):
    """(text without colour tags, kind from the first colour: npc/object/item/player/None)."""
    if not subject:
        return "", None
    m = _TAG.search(subject)
    kind = KINDS.get(m.group(1).lower()) if m and m.group(1) else None
    text = _TAG.sub("", subject).strip()
    return text, kind


def menu_row_point(menu, row):
    """Canvas point in the middle of an open menu's `row` (0 = top)."""
    return (menu["x"] + menu["w"] // 2,
            menu["y"] + MENU_FIRST_BASELINE + row * MENU_ROW_H - 4)


class GameState:
    def __init__(self, port=PORT):
        self.port = port
        self.sock = None
        self.file = None
        self.lock = threading.Lock()
        self._ok = None
        self._slow_logged = 0.0

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = self.file = None

    def _q(self, cmd):
        """Ask the add-on; one line of JSON back. Scans of the scene get longer to answer. On a
        timeout the connection is dropped (a late reply would be read as the next answer) and
        the question asked once more on a fresh one."""
        heavy = cmd.split(" ", 1)[0] in HEAVY
        with self.lock:
            for attempt in (1, 2):
                t0 = time.monotonic()
                try:
                    if self.sock is None:
                        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
                        self.file = self.sock.makefile("r", encoding="utf-8", errors="replace")
                    self.sock.settimeout(HEAVY_TIMEOUT_S if heavy else TIMEOUT_S)
                    self.sock.sendall(f"state {cmd}\n".encode())
                    line = self.file.readline().strip()
                    break
                except OSError as e:
                    self.close()
                    if attempt == 2:
                        raise GameStateError(f"input add-on not reachable: {e}")
                    log.warning("Game data query '%s' failed (%s) - retrying", cmd, e)
            took = time.monotonic() - t0
            if took > SLOW_S and time.monotonic() - self._slow_logged > 60:
                self._slow_logged = time.monotonic()
                log.info("Game data query '%s' took %.1f s", cmd, took)
        if not line.startswith("ok "):
            raise GameStateError(line or "no reply")
        try:
            return json.loads(line[3:])
        except ValueError as e:
            raise GameStateError(f"bad reply: {e}")

    def probe(self):
        """List of client fields the add-on couldn't read (empty = everything works)."""
        return self._q("probe").get("missing", [])

    def available(self):
        if self._ok is None:
            try:
                self._ok = not self.probe()
            except GameStateError:
                self._ok = False
        return self._ok

    def menu(self):
        m = self._q("menu")
        for e in m.get("entries", []):
            e["subject"], e["kind"] = clean(e.get("subject"))
            e["verb"] = clean(e.get("verb"))[0]
        return m

    def npcs(self, name=None):
        out = self._q("npcs" + (f" {name}" if name else ""))
        for n in out:
            n["ops"] = [o for o in n.get("ops") or [] if o]
        return out

    def inv(self, inv_id=None):
        """28 backpack slots, or another inventory (94 = worn equipment)."""
        return self._q("inv" if inv_id is None else f"inv {int(inv_id)}")

    def skills(self):
        s = self._q("skills")
        return {name: {"level": s["base"][i], "boosted": s["boosted"][i], "xp": s["xp"][i]}
                for i, name in enumerate(SKILLS) if i < len(s.get("base", []))}

    def player(self, raw=False):
        """Where we are: {'logged_in', 'index', 'in_combat', 'tile', 'plane', ...}. While the game
        loads a new area (after a teleport, crossing into a new region) it briefly isn't "in
        game" and the answer has no tile - wait that out (LOADING_WAIT_S). Logged out for real:
        GameStateError. raw=True: the answer as it is ({'logged_in': False} when out)."""
        me = self._q("player")
        if raw or me.get("logged_in", True):
            return me
        end = time.monotonic() + LOADING_WAIT_S
        while time.monotonic() < end:
            if self._q("tick").get("state") not in LOADING_STATES:
                break                                   # the login screen / connection lost
            time.sleep(0.3)
            me = self._q("player")
            if me.get("logged_in", True):
                return me
        raise GameStateError("not in the game right now (logged out, or the world is still loading)")

    def locs(self, radius=15, name=None):
        """Scene objects (trees, rocks, fires, booths...) within `radius` tiles, nearest first:
        [{'id', 'name', 'ops', 'tile', 'dist', 'size', 'screen', 'body'}]."""
        return self._q(f"locs {int(radius)}" + (f" {name}" if name else ""))

    def camera(self, yaw=None, pitch=None, zoom=None):
        """Read the camera, or set any of yaw (0-2047, 0 = north), pitch (128-383, 383 = most
        top-down), zoom (1-2000, 600 default). The client glides toward the new target."""
        if yaw is None and pitch is None and zoom is None:
            return self._q("camera")
        f = lambda v: "-" if v is None else str(int(round(v)))
        return self._q(f"camera {f(yaw)} {f(pitch)} {f(zoom)}")

    def minimap(self):
        """How the minimap is drawn: {'angle' (0-2047, clockwise from up to north, including the
        client's login-time skew the compass doesn't show), 'scale' (minimap px per map px)}."""
        return self._q("minimap")

    def widgets(self, match=None):
        """Visible interface components (see ui.widgets)."""
        return self._q("widgets" + (f" {match}" if match is not None else ""))

    def login(self, user, password):
        """Log in from the login screen (the client's own login action). {'ok', 'why'}."""
        import base64
        enc = lambda t: base64.b64encode(t.encode("utf-8")).decode("ascii")
        return self._q(f"login {enc(user)} {enc(password)}")

    def chat(self, n=20):
        """The chat box, newest first: {'count': messages ever added, 'lines': [{'type', 'text'}]}
        (type 0: the game's own messages)."""
        return self._q(f"chat {int(n)}")

    def varbits(self, *ids):
        """Varbits (farming patch states...): {id: value}."""
        return {int(k): v for k, v in self._q("varbit " + " ".join(str(i) for i in ids)).items()}

    def varps(self, *ids):
        """Server-set varps (quest progress...): {id: value}."""
        return {int(k): v for k, v in self._q("varp " + " ".join(str(i) for i in ids)).items()}

    def login_status(self):
        """{'state' (10 login screen, 30 in game), 'step', 'reply'}."""
        return self._q("loginstatus")

    def ground(self, radius=15):
        """Items on the ground within `radius` tiles (this floor), nearest first:
        [{'id', 'count', 'name', 'tile', 'dist', 'screen'}]."""
        return self._q(f"ground {int(radius)}")


def top_entry(menu):
    """The left-click option (what the hover text shows), or None."""
    entries = menu.get("entries") or []
    return entries[0] if entries else None


def find_entry(menu, verb, subject):
    """The entry whose verb and subject match (case-insensitive), or None."""
    for e in menu.get("entries") or []:
        if e["verb"].lower() == verb.lower() and e["subject"].lower() == subject.lower():
            return e
    return None


# ---- one shared connection for everything that reads game data (backpack, skills, ...) ----
SHARED_RETRY_S = 20
_shared = None
_shared_retry_at = 0.0
_shared_lock = threading.Lock()


def shared():
    """A working GameState, or None (probed again at most every SHARED_RETRY_S)."""
    global _shared, _shared_retry_at
    with _shared_lock:
        if _shared is not None:
            return _shared
        if time.monotonic() < _shared_retry_at:
            return None
        gs = GameState()
        if gs.available():
            _shared = gs
            return gs
        gs.close()
        _shared_retry_at = time.monotonic() + SHARED_RETRY_S
        return None


def drop_shared():
    """The shared connection failed (game closed?): fall back to pixels for a while."""
    global _shared, _shared_retry_at
    with _shared_lock:
        if _shared is not None:
            _shared.close()
        _shared = None
        _shared_retry_at = time.monotonic() + SHARED_RETRY_S


def xp_for_level(level):
    """Total XP needed for `level` (the standard RuneScape table; 99 = 13,034,431)."""
    pts = 0
    for lv in range(1, level):
        pts += int(lv + 300 * 2 ** (lv / 7.0))
    return pts // 4


def skill(name):
    """{'level' (current/boosted), 'base', 'xp', 'next', 'remainder'} from the game, or None."""
    gs = shared()
    if gs is None:
        return None
    try:
        s = gs.skills().get(name)
    except GameStateError:
        drop_shared()
        return None
    if not s:
        return None
    nxt = xp_for_level(s["level"] + 1) if s["level"] < 99 else None
    return {"level": s["boosted"], "base": s["level"], "xp": s["xp"], "next": nxt,
            "remainder": (nxt - s["xp"]) if nxt is not None else None}


GIVE_UP_AFTER = 3          # failed game-data calls in a row before a bot falls back to pixels


def tolerant_call(bot, fn, *a, logger=None):
    """Run a bot's game-data step. A failure is logged and skipped (returns None) - one slow or
    lost answer shouldn't end a run; only GIVE_UP_AFTER failures in a row switch the bot to the
    screen (bot.gs = None). Successes reset the count."""
    lg = logger or log
    try:
        out = fn(*a)
        bot._gs_failures = 0
        return out
    except GameStateError as e:
        bot._gs_failures = getattr(bot, "_gs_failures", 0) + 1
        if bot._gs_failures >= GIVE_UP_AFTER:
            lg.warning("Game data keeps failing (%s) - using the screen from now on", e)
            drop_shared()
            bot.gs = None
        else:
            lg.warning("Game data hiccup (%s) - skipping this step", e)
        return None
