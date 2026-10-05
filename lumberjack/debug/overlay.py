"""Live debug view: what the bot sees, with every UI region outlined.

Run from the project folder:
    %USERPROFILE%\\.venvs\\lumberjack\\Scripts\\python.exe -m lumberjack.debug.overlay
    ... add --once to just save one annotated frame to lumberjack/captures/overlay.png

Keys in the live window:  s = save frame   q / Esc = quit
"""
import sys
import time
from pathlib import Path

import cv2

from lumberjack.core import regions as R
from lumberjack.core.window import GameWindow

OUT = Path(__file__).resolve().parents[1] / "captures"
SCALE = 1.5  # enlarge the debug window so it's easier to read

COLORS = {
    "viewport": (0, 255, 255),
    "mouseover": (255, 0, 255),
    "player": (0, 0, 255),
    "chat": (255, 200, 0),
    "minimap": (0, 255, 0),
    "orb": (0, 165, 255),
    "inv": (255, 255, 255),
}


def annotate(frame):
    img = frame.copy()

    def box(rect, color, label=None):
        cv2.rectangle(img, (rect.x, rect.y), (rect.x + rect.w - 1, rect.y + rect.h - 1), color, 1)
        if label:
            cv2.putText(img, label, (rect.x + 2, rect.y + rect.h + 11), cv2.FONT_HERSHEY_PLAIN, 0.8, color, 1)

    box(R.VIEWPORT, COLORS["viewport"])
    box(R.MOUSEOVER_TEXT, COLORS["mouseover"], "mouseover")
    box(R.PLAYER, COLORS["player"], "player")
    box(R.CHATBOX, COLORS["chat"])
    box(R.CHAT_INPUT, COLORS["chat"])
    cv2.circle(img, R.MINIMAP_CENTER, R.MINIMAP_R, COLORS["minimap"], 1)
    cv2.circle(img, R.COMPASS_CENTER, R.COMPASS_R, COLORS["minimap"], 1)
    for orb in (R.HP_ORB, R.PRAYER_ORB, R.RUN_ORB):
        box(orb, COLORS["orb"])
    box(R.INV_TAB, COLORS["orb"])
    for i, slot in enumerate(R.INV_SLOTS):
        box(slot, COLORS["inv"])
        cv2.putText(img, str(i), (slot.x + 1, slot.y + 9), cv2.FONT_HERSHEY_PLAIN, 0.6, COLORS["inv"], 1)
    return img


def main():
    win = GameWindow()
    print(f"Attached to '{win.title}'")
    OUT.mkdir(exist_ok=True)

    if "--once" in sys.argv:
        path = OUT / "overlay.png"
        cv2.imwrite(str(path), annotate(win.grab()))
        print(f"Saved {path}")
        return

    last, fps = time.perf_counter(), 0.0
    while True:
        img = annotate(win.grab())
        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 * (1 / max(now - last, 1e-6))
        last = now
        cv2.putText(img, f"{fps:4.1f} fps", (440, 334), cv2.FONT_HERSHEY_PLAIN, 0.9, (0, 255, 0), 1)
        cv2.imshow("Grindstone - bot vision", cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_NEAREST))
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("s"):
            path = OUT / f"frame_{int(time.time())}.png"
            cv2.imwrite(str(path), win.grab())
            print(f"Saved {path}")
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
