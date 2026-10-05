"""Fixed-mode (765x503) layout of the 530-revision client. All coords are canvas-relative.

Measured from a 2009scape Singleplayer capture. Inventory slot geometry is the classic
fixed-mode grid; verify it once the backpack has items (debug overlay draws it).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2

    def crop(self, img):
        return img[self.y:self.y + self.h, self.x:self.x + self.w]

    def contains(self, px, py):
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


VIEWPORT = Rect(4, 4, 512, 334)
MOUSEOVER_TEXT = Rect(4, 4, 420, 18)     # "Chop down Tree / 2 more options"
PLAYER = Rect(240, 150, 32, 44)          # roughly where our character stands (viewport centre)
CHATBOX = Rect(7, 344, 490, 116)
CHAT_INPUT = Rect(7, 458, 490, 14)

COMPASS_CENTER, COMPASS_R = (542, 22), 18
MINIMAP_CENTER, MINIMAP_R = (626, 85), 72
HP_ORB = Rect(690, 12, 50, 30)
PRAYER_ORB = Rect(706, 54, 50, 30)
RUN_ORB = Rect(706, 92, 50, 30)

SIDE_PANEL = Rect(547, 205, 190, 260)
INV_TAB = Rect(626, 168, 33, 36)         # backpack tab button

INV_COLS, INV_ROWS = 4, 7
INV_X0, INV_Y0 = 563, 213                # top-left of slot 0's item sprite
INV_DX, INV_DY = 42, 36
INV_SLOT_SIZE = 32


def inv_slot(i):
    """Rect of inventory slot i (0..27, row-major)."""
    col, row = i % INV_COLS, i // INV_COLS
    return Rect(INV_X0 + col * INV_DX, INV_Y0 + row * INV_DY, INV_SLOT_SIZE, INV_SLOT_SIZE)


INV_SLOTS = [inv_slot(i) for i in range(INV_COLS * INV_ROWS)]
