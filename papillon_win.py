#!/usr/bin/env python3
"""
Papillon (Windows) — Beautiful butterflies fill your desktop.
Controls:
  Ctrl+Shift+B — Quit
  Ctrl+Shift+I — Toggle interaction mode
  Ctrl+Shift+H — Hide/Show
"""

import sys
import time
import ctypes
import ctypes.wintypes

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QCursor, QFont

from papillon_engine import *


# ── Win32 constants ─────────────────────────────────────

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_QUIT = 1
HOTKEY_TOGGLE = 2
HOTKEY_HIDE = 3

user32 = ctypes.windll.user32


def _set_click_through(hwnd, through):
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if through:
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    else:
        style &= ~WS_EX_TRANSPARENT
        style |= WS_EX_LAYERED | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


# ── Overlay window (Windows) ───────────────────────────

class ButterflyOverlay(QWidget):

    def __init__(self):
        super().__init__()

        screen = QApplication.primaryScreen().geometry()
        self.sw = screen.width()
        self.sh = screen.height()
        self.setGeometry(screen)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)

        self.mouse_x = -10000
        self.mouse_y = -10000

        self.butterflies = []
        self._dragging = None
        self._drag_prev = (0.0, 0.0)
        self._initial_fill_done = False
        self._interact_mode = False
        self._hidden = False

        self._collection = load_collection()
        self._net_active = False
        self._panel_open = False
        avail = QApplication.primaryScreen().availableGeometry()
        aw, ah = avail.width(), avail.height()
        self._badge_rect = QRectF(aw - 68, ah - 50, 52, 30)
        self._net_icon_rect = QRectF(aw - 120, ah - 56, 48, 48)
        self._panel_rect = QRectF(aw - 370, ah - 490, 350, 430)

        self.start_time = time.monotonic()
        self.physics_steps = 0

        self.spawn_timer = QTimer(self)
        self.spawn_timer.timeout.connect(self._spawn)
        self.spawn_timer.start(SPAWN_INTERVAL_MS)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick)
        self.anim_timer.start(int(1000 / FPS))

        self.show()
        self.raise_()

        QTimer.singleShot(300, self._setup_win32)

    def _setup_win32(self):
        self._hwnd = int(self.winId())
        _set_click_through(self._hwnd, True)

        mods_quit = MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
        user32.RegisterHotKey(None, HOTKEY_QUIT, mods_quit, 0x42)     # B
        user32.RegisterHotKey(None, HOTKEY_TOGGLE, mods_quit, 0x49)   # I
        user32.RegisterHotKey(None, HOTKEY_HIDE, mods_quit, 0x48)     # H

        self._hotkey_timer = QTimer(self)
        self._hotkey_timer.timeout.connect(self._poll_hotkeys)
        self._hotkey_timer.start(100)

    def _poll_hotkeys(self):
        msg = ctypes.wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None,
                                   WM_HOTKEY, WM_HOTKEY, 0x0001):
            hk_id = msg.wParam
            if hk_id == HOTKEY_QUIT:
                self._quit()
                return
            elif hk_id == HOTKEY_TOGGLE:
                self._toggle_interact()
            elif hk_id == HOTKEY_HIDE:
                self._toggle_hide()

    def _toggle_interact(self):
        self._interact_mode = not self._interact_mode
        _set_click_through(self._hwnd, not self._interact_mode)

    def _toggle_hide(self):
        self._hidden = not self._hidden
        if self._hidden:
            self.anim_timer.stop()
            self.spawn_timer.stop()
            self.hide()
        else:
            self.show()
            self.raise_()
            self.spawn_timer.start()
            self.anim_timer.start(int(1000 / FPS))

    def _spawn(self):
        elapsed = time.monotonic() - self.start_time
        active = [b for b in self.butterflies if not b.retiring]

        if len(active) >= MAX_ON_SCREEN:
            if not self._initial_fill_done:
                self._initial_fill_done = True
                self.spawn_timer.setInterval(ROTATION_SPAWN_MS)
                return
            oldest = min(active, key=lambda b: b.born)
            oldest.start_retire()

        species = pick_species(elapsed)
        x, y, vx, vy = spawn_position(self.sw, self.sh)
        self.butterflies.append(
            Butterfly(x, y, vx, vy, self.sw, self.sh, species)
        )

        if not self._initial_fill_done and len(active) + 1 >= MAX_ON_SCREEN:
            self._initial_fill_done = True
            self.spawn_timer.setInterval(ROTATION_SPAWN_MS)

    def _quit(self):
        self.anim_timer.stop()
        self.spawn_timer.stop()
        if hasattr(self, '_hotkey_timer'):
            self._hotkey_timer.stop()
        user32.UnregisterHotKey(None, HOTKEY_QUIT)
        user32.UnregisterHotKey(None, HOTKEY_TOGGLE)
        user32.UnregisterHotKey(None, HOTKEY_HIDE)
        self.close()
        QApplication.quit()

    def _hit_butterfly(self, x, y):
        for b in reversed(self.butterflies):
            bx1, by1, bx2, by2 = b.bbox
            if bx1 <= x <= bx2 and by1 <= y <= by2:
                return b
        return None

    def _catch_butterfly(self, b):
        b.catch(self.mouse_x, self.mouse_y)
        name = b.species["name"]
        self._collection[name] = self._collection.get(name, 0) + 1
        save_collection(self._collection)

    def mousePressEvent(self, event):
        if not self._interact_mode:
            return
        if COLLECTION_ENABLED:
            if event.button() == Qt.RightButton and self._net_active:
                self._net_active = False
                return
        if event.button() != Qt.LeftButton:
            return
        x, y = event.x(), event.y()

        if COLLECTION_ENABLED:
            if self._net_icon_rect.contains(QPointF(x, y)):
                self._net_active = not self._net_active
                if self._net_active:
                    self._panel_open = False
                return
            if self._badge_rect.contains(QPointF(x, y)):
                self._panel_open = not self._panel_open
                if self._panel_open:
                    self._net_active = False
                return
            if self._panel_open:
                if not self._panel_rect.contains(QPointF(x, y)):
                    self._panel_open = False
                return

            if self._net_active:
                b = self._hit_butterfly(x, y)
                if b and b.interaction != "caught":
                    self._catch_butterfly(b)
                return

        b = self._hit_butterfly(x, y)
        if b:
            if b.interaction == "held":
                return
            b.hold(x, y)
            self._dragging = b
            self._drag_prev = (x, y)
            self.grabMouse()

    def mouseMoveEvent(self, event):
        x, y = event.x(), event.y()
        if self._dragging:
            b = self._dragging
            b.x = x + b.held_offset_x
            b.y = y + b.held_offset_y
            self._drag_prev = (x, y)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            b = self._dragging
            x, y = event.x(), event.y()
            px, py = self._drag_prev
            b.release(x - px, y - py)
            self._dragging = None
            self.releaseMouse()
            return
        if self._interact_mode and event.button() == Qt.LeftButton and not self._net_active:
            x, y = event.x(), event.y()
            b = self._hit_butterfly(x, y)
            if b and b.interaction not in ("held", "caught"):
                b.startle(x, y)

    def _tick(self):
        cursor = QCursor.pos()
        self.mouse_x = cursor.x()
        self.mouse_y = cursor.y()

        margin = 120
        self.butterflies = [
            b for b in self.butterflies
            if not getattr(b, '_caught_done', False)
            and ((not b.retiring
                  or (-margin < b.x < self.sw + margin
                      and -margin < b.y < self.sh + margin))
                 or b is self._dragging)
        ]

        elapsed = time.monotonic() - self.start_time
        steps = min(4, int(elapsed * FPS) - self.physics_steps)
        self.physics_steps += steps
        for _ in range(steps):
            for b in self.butterflies:
                b.update(self.mouse_x, self.mouse_y, self._net_active)

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        for b in self.butterflies:
            draw_butterfly(painter, b)

        if COLLECTION_ENABLED:
            caught_count = len([s for s in SPECIES
                               if self._collection.get(s["name"], 0) > 0])
            draw_net_icon(painter, self._net_icon_rect, self._net_active)
            draw_badge(painter, self._badge_rect, caught_count)

            if self._panel_open:
                draw_collection_panel(painter, self._panel_rect,
                                      self._collection, SPECIES)

            if self._net_active:
                draw_net_cursor(painter, self.mouse_x, self.mouse_y)

        if self._interact_mode:
            painter.setOpacity(0.6)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(QBrush(QColor(0, 0, 0, 120)))
            painter.drawRoundedRect(QRectF(self.sw - 220, 10, 210, 30), 8, 8)
            painter.setOpacity(1.0)
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(self.sw - 220, 10, 210, 30),
                             Qt.AlignCenter,
                             "Interact Mode — Ctrl+Shift+I")

        painter.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._quit()


# ── Entry point ─────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    print("Papillon (Windows) — Ctrl+Shift+B to close")
    print("Ctrl+Shift+I to toggle interaction mode")
    print("Ctrl+Shift+H to hide/show")
    print("Rare species appear over time. Keep watching!")
    overlay = ButterflyOverlay()
    sys.exit(app.exec_())
