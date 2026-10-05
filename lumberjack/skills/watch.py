"""Checks a long-running plan needs from every bot, done from their check_stop():

    stop_at_level   (skill(s), level): stop once the level is reached ("Fishing until 30");
                    a list of skills means all of them (combat: attack, strength, defence)
    stall_limit_s   no progress and no XP for this long -> stop, so the plan can recover
    logged out      wait (state "logged out") until we're back in, instead of failing
    random events   an event NPC after us (it offers "Dismiss") is dismissed - for every run
    moved away      we're suddenly far from where we were (died -> Lumbridge, a random event
                    took us somewhere) -> stop with bot.displaced_from set, so the plan / the
                    single-task runner can go back there and carry on - for every run

All need the game's own data; without it they do nothing. Throttled to every CHECK_EVERY_S.
"""
import logging
import re
import time

log = logging.getLogger("watch")

CHECK_EVERY_S = 10.0
JUMP_TILES = 25         # farther than this between two checks...
JUMP_SPEED = 4.0        # ...and faster than running (~3.3 tiles/s) = we didn't walk there


def _total_xp(gs):
    return sum(s["xp"] for s in gs.skills().values())


def plan_checks(bot, stop_cls):
    """Raise stop_cls(reason) when a plan condition says so. Call from check_stop()."""
    now = time.monotonic()
    if now < getattr(bot, "_watch_next", 0.0) or getattr(bot, "_watch_busy", False):
        return
    bot._watch_next = now + CHECK_EVERY_S
    level = getattr(bot, "stop_at_level", None)
    stall = getattr(bot, "stall_limit_s", None)
    from lumberjack.core import gamestate
    gs = gamestate.shared()
    if gs is None:
        return
    try:
        bot._watch_busy = True              # dismissing sleeps, and sleeping calls us again
        dismiss_random_event(bot, gs)
    except gamestate.GameStateError:
        return
    finally:
        bot._watch_busy = False
    try:
        me = gs.player(raw=True)
        if not me.get("logged_in", True):
            _wait_logged_in(bot, gs, stop_cls)
            bot._watch_marker = None
            bot._watch_pos = None
            return
        check_moved(bot, me, now, stop_cls)
        check_chat(bot, gs, now, stop_cls)
        if level:
            skills, target = level
            skills = [skills] if isinstance(skills, str) else list(skills)
            have = gs.skills()
            if skills and all(have.get(s, {}).get("level", 0) >= target for s in skills):
                raise stop_cls(f"reached level {target} in {'/'.join(skills)}")
        if stall:
            marker = (bot.progress() if hasattr(bot, "progress") else getattr(bot, "logs_cut", 0), _total_xp(gs))
            if marker != getattr(bot, "_watch_marker", None):
                bot._watch_marker, bot._watch_since = marker, now
            elif now - getattr(bot, "_watch_since", now) > stall:
                raise stop_cls(f"no progress for {int(stall // 60)} min")
    except gamestate.GameStateError:
        return


PROBLEM = re.compile(r"\b(need|needs|can't|cannot|can not|already|not enough|don't have|do not have|"
                     r"nothing interesting|unable|too low|must|isn't|is not)\b", re.I)
REPEAT_LIMIT = 6        # the same refusal this often...
REPEAT_WINDOW_S = 90.0  # ...within this long: stop, saying it (the bot keeps doing something the game refuses)


def check_chat(bot, gs, now, stop_cls):
    """The game's own messages: log the ones that sound like a problem ("You need a Mining level
    of 15...", "You can't pickpocket while in combat."), and stop when one keeps repeating - the
    bot is doing something the game refuses, over and over. An add-on without chat: nothing."""
    from lumberjack.core import gamestate
    if getattr(bot, "_chat_off", False) or not hasattr(gs, "chat"):
        return
    try:
        chat = gs.chat(30)
    except gamestate.GameStateError as e:
        if "unknown state" in str(e):
            bot._chat_off = True
        return
    count = chat.get("count", 0)
    seen = getattr(bot, "_chat_seen", None)
    bot._chat_seen = count
    if seen is None or count <= seen:
        return                                  # the first look only sets where we are
    new = [gamestate.clean(l.get("text"))[0] for l in chat.get("lines", [])[:count - seen]
           if l.get("type") == 0]
    times = getattr(bot, "_chat_times", None)
    if times is None:
        times = bot._chat_times = {}
    for text in reversed(new):                  # oldest first
        if not text or not PROBLEM.search(text):
            continue
        hits = [t for t in times.get(text, []) if now - t < REPEAT_WINDOW_S] + [now]
        times[text] = hits
        if len(hits) == 1:
            log.info("Game says: %s", text)
        if len(hits) >= REPEAT_LIMIT:
            raise stop_cls(f'the game keeps saying "{text}"')


def check_moved(bot, me, now, stop_cls):
    """Stop when we jumped far between two checks (died, teleported by a random event). The bot's
    own teleports are left alone: their state says "teleporting" and the next check starts over."""
    from lumberjack.nav import places
    tile, plane = me.get("tile"), me.get("plane", 0)
    last = getattr(bot, "_watch_pos", None)
    ours = last is not None and places.teleported_at >= last[2] - 1.0   # we ::tele'd since the last check
    if tile is None or "teleport" in str(getattr(bot, "state", "")):
        bot._watch_pos = None
        return
    if ours:                                   # start over from where the teleport took us
        bot._watch_pos = (tuple(tile), plane, now)
        return
    bot._watch_pos = (tuple(tile), plane, now)
    if last is None:
        return
    lt, lp, lnow = last
    d = max(abs(tile[0] - lt[0]), abs(tile[1] - lt[1]))
    if d > JUMP_TILES and d / max(now - lnow, 1.0) > JUMP_SPEED:
        bot.displaced_from = {"tile": list(lt), "plane": lp}
        log.warning("We're suddenly %d tiles from where we were %s - died, or a random event moved us?",
                    d, list(lt))
        raise stop_cls(f"moved away ({d} tiles) - died or teleported?")


def _wait_logged_in(bot, gs, stop_cls):
    from lumberjack.core import gamestate
    before = getattr(bot, "state", "")
    log.warning("Logged out - waiting to be logged back in")
    while True:
        bot.state = "logged out - waiting"
        stop = getattr(bot, "stop_event", None)
        if stop is not None and stop.is_set():
            raise stop_cls("stopped from control panel")
        time.sleep(3.0)
        try:
            if gs.player(raw=True).get("logged_in", True):
                break
        except gamestate.GameStateError:
            continue
    log.info("Logged back in")
    bot.state = before


def dismiss_random_event(bot, gs):
    """A random-event NPC that's after us offers "Dismiss" - do that, so it can't teleport us
    away or get in the way. True if one was dismissed."""
    ctx = getattr(bot, "ctx", None)
    if ctx is None:
        return False
    me = gs.player()
    mine = 32768 + me.get("index", -1)
    for n in gs.npcs():
        if "Dismiss" not in n["ops"] or n["dist"] > 6:
            continue
        if n["interacting"] != mine and n["dist"] > 1:
            continue
        from lumberjack.core import interact
        log.info("Random event: dismissing %s", n["name"])
        if interact.use_option(ctx, gs, interact.points_for(n), "Dismiss", n["name"]):
            ctx.sleep(1.2)
            return True
    return False
