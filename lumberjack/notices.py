"""Notices the panel and start-up show, and what this PC has said about them (configs/notices.json).

    sp_accepted()                -> when "don't show this again" was ticked on the singleplayer
                                    notice (ISO time), or None
    accept_sp()                  -> tick it
    note_update(frm, to, addon)  -> an update was installed (the panel shows the restart tip once)
    pending_update()             -> {'from', 'to', 'addon_changed'} not yet dismissed, or None
    dismiss_update()

Ticking the box only hides the notice. The singleplayer check itself (singleplayer_guard) runs
every time regardless - it has no switch.
"""
import json
import time
from pathlib import Path

FILE = Path(__file__).resolve().parent / "configs" / "notices.json"

SERVER_ERROR_TIP = ("If the game shows a server error after you restart it: close the game, open the "
                    "2009scape launcher and run Update singleplayer, close the launcher, then start "
                    "Grindstone again.")


def _load():
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def sp_accepted():
    return _load().get("singleplayer_accepted")


def accept_sp():
    d = _load()
    d["singleplayer_accepted"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save(d)


def note_update(frm, to, addon_changed=False):
    d = _load()
    prev = d.get("update") or {}
    d["update"] = {"from": prev.get("from", frm), "to": to,
                   "addon_changed": bool(addon_changed or prev.get("addon_changed"))}
    _save(d)


def pending_update():
    return _load().get("update")


def dismiss_update():
    d = _load()
    if d.pop("update", None) is not None:
        _save(d)
