"""The game's frame straight from the add-on (`frame` command): the software renderer's buffer,
read inside the client. No PrintWindow / BitBlt on the game window, so capturing can't wedge the
game's UI thread (a frozen game was traced to screen grabs waiting on it).

    grab() -> 503x765x3 BGR array, or None (add-on too old / HD mode / not answering)

An add-on without the command is asked again after RETRY_S (a restarted game has the new one).
"""
import socket
import threading
import time

import numpy as np

from lumberjack.core.agent import PORT

TIMEOUT_S = 3.0
RETRY_S = 300

_lock = threading.Lock()
_sock = None
_file = None
_off_until = 0.0


def _close():
    global _sock, _file
    try:
        if _sock:
            _sock.close()
    except OSError:
        pass
    _sock = _file = None


def grab():
    global _sock, _file, _off_until
    if time.monotonic() < _off_until:
        return None
    with _lock:
        try:
            if _sock is None:
                _sock = socket.create_connection(("127.0.0.1", PORT), timeout=TIMEOUT_S)
                _file = _sock.makefile("rb")
            _sock.sendall(b"frame\n")
            head = _file.readline().decode("ascii", "replace").split()
            if not head or head[0] != "ok":
                if head and "unknown command" in " ".join(head):
                    _off_until = time.monotonic() + RETRY_S       # old add-on: use the screen grab
                    _close()
                return None
            w, h = int(head[1]), int(head[2])
            data = _file.read(w * h * 4)
            if len(data) != w * h * 4:
                _close()
                return None
        except (OSError, ValueError, IndexError):
            _close()
            return None
    img = np.frombuffer(data, np.uint8).reshape(h, w, 4)[..., :3].copy()
    return img if img.max() >= 8 else None                         # not drawn yet
