"""Virtual mouse/keyboard for the game, via the in-client Java agent (see agent/).

Your real cursor and keyboard are never touched, and the game window can be covered or
unfocused. Coordinates are canvas-relative (0..764, 0..502).
"""
import random
import socket
import time

import win32con

from lumberjack.core.agent import PORT, ensure_agent

LEFT, MIDDLE, RIGHT = 1, 2, 3


class AgentInput:
    def __init__(self, port=PORT):
        ensure_agent(port)
        self.port = port
        self._connect()
        self.pos = (382, 250)

    def _connect(self):
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.reader = self.sock.makefile("r")

    def close(self):
        try:
            self.reader.close()
            self.sock.close()
        except OSError:
            pass

    def _cmd(self, *parts):
        """Send one command. A lost or slow answer (the game hitched) reconnects and sends it once
        more - a late reply on the old connection would otherwise be read as the next answer."""
        line = (" ".join(str(int(p)) if isinstance(p, float) else str(p) for p in parts) + "\n").encode()
        for attempt in (1, 2):
            try:
                self.sock.sendall(line)
                reply = self.reader.readline()
                if not reply:                       # the add-on closed the connection
                    raise ConnectionResetError("input add-on closed the connection")
                reply = reply.strip()
                break
            except OSError:
                self.close()
                if attempt == 2:
                    raise
                time.sleep(0.5)
                self._connect()
        if not reply.startswith("ok"):
            raise RuntimeError(f"agent: {reply}")
        return reply

    # ---- mouse -----------------------------------------------------------------------
    def move(self, x, y, steps=None):
        """Glide to (x, y). Intermediate moves matter: the game updates hover text on motion."""
        x, y = int(round(x)), int(round(y))
        x0, y0 = self.pos
        dist = max(abs(x - x0), abs(y - y0))
        steps = steps or max(2, min(12, dist // 25))
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)  # ease in/out
            self._cmd("move", int(x0 + (x - x0) * t), int(y0 + (y - y0) * t))
            time.sleep(0.008)
        self.pos = (int(x), int(y))

    def click(self, x=None, y=None, button=LEFT):
        if x is not None:
            self.move(x, y)
            time.sleep(0.05)
        self._cmd("press", *self.pos, button)
        time.sleep(random.uniform(0.04, 0.09))
        self._cmd("release", *self.pos, button)

    def right_click(self, x=None, y=None):
        self.click(x, y, RIGHT)

    # ---- keyboard --------------------------------------------------------------------
    def key_down(self, vk):
        self._cmd("key_down", vk)

    def key_up(self, vk):
        self._cmd("key_up", vk)

    def hold_key(self, vk, seconds):
        self.key_down(vk)
        time.sleep(seconds)
        self.key_up(vk)

    def tap(self, vk):
        self.hold_key(vk, random.uniform(0.05, 0.1))

    def type_text(self, text, enter=False):
        """Type into the game (e.g. a chat command). Uses the typing add-on, which can send
        any character - the main add-on can't send spaces."""
        from lumberjack.core.agent import TYPE_PORT, ensure_type_agent
        ensure_type_agent()
        with socket.create_connection(("127.0.0.1", TYPE_PORT), timeout=3) as s:
            f = s.makefile("r")
            for ch in text:
                s.sendall(f"typecode {ord(ch)}\n".encode())
                reply = f.readline().strip()
                if not reply.startswith("ok"):
                    raise RuntimeError(f"typing add-on: {reply}")
                time.sleep(random.uniform(0.03, 0.07))
        if enter:
            time.sleep(0.1)
            self.tap(10)   # Java's VK_ENTER (the agent sends Java key codes; Windows' VK_RETURN is 13)


VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN = win32con.VK_LEFT, win32con.VK_RIGHT, win32con.VK_UP, win32con.VK_DOWN
VK_SHIFT = win32con.VK_SHIFT
