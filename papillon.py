#!/usr/bin/env python3
"""
Papillon (Linux) — Beautiful butterflies fill your Ubuntu desktop.
Controls: Ctrl+Shift+B — Quit, Ctrl+Shift+H — Hide/Show
"""

import sys
import time
import platform

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt5.QtGui import QPainter, QCursor

from papillon_engine import *


# ── Overlay window (Linux / X11) ─────────────────────────

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

        if CLICK_THROUGH:
            QTimer.singleShot(300, self._enable_click_through)

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

    def _enable_click_through(self):
        system = platform.system()

        if system == "Windows":
            import ctypes
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, -20)
            style |= 0x00080000 | 0x00000020 | 0x00000080
            user32.SetWindowLongW(hwnd, -20, style)

        elif system == "Linux":
            try:
                from Xlib import display, X, XK
                from Xlib.ext import shape

                self.xdisplay = display.Display()
                self._x11_window = self.xdisplay.create_resource_object(
                    "window", int(self.winId())
                )
                self._x11_shape = shape
                self._x11_X = X
                self._x11_window.shape_rectangles(
                    shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [],
                )

                root = self.xdisplay.screen().root
                self._hotkey_code = self.xdisplay.keysym_to_keycode(
                    XK.string_to_keysym("b")
                )
                self._hotkey_hide = self.xdisplay.keysym_to_keycode(
                    XK.string_to_keysym("h")
                )
                mods = X.ControlMask | X.ShiftMask
                for extra in (0, X.Mod2Mask, X.LockMask,
                              X.Mod2Mask | X.LockMask):
                    root.grab_key(
                        self._hotkey_code, mods | extra,
                        True, X.GrabModeAsync, X.GrabModeAsync,
                    )
                    root.grab_key(
                        self._hotkey_hide, mods | extra,
                        True, X.GrabModeAsync, X.GrabModeAsync,
                    )

                self.xdisplay.sync()

                self._hotkey_timer = QTimer(self)
                self._hotkey_timer.timeout.connect(self._check_hotkey)
                self._hotkey_timer.start(150)
            except Exception as e:
                print("X11 setup error:", e)

    def _check_hotkey(self):
        try:
            from Xlib import X
            while self.xdisplay.pending_events():
                ev = self.xdisplay.next_event()
                if ev.type == X.KeyPress:
                    if ev.detail == self._hotkey_code:
                        self._quit()
                        return
                    elif hasattr(self, '_hotkey_hide') and ev.detail == self._hotkey_hide:
                        self._toggle_hide()
        except Exception:
            pass

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

    def _quit(self):
        self.anim_timer.stop()
        self.spawn_timer.stop()
        if hasattr(self, '_hotkey_timer'):
            self._hotkey_timer.stop()
        try:
            from Xlib import X
            root = self.xdisplay.screen().root
            root.ungrab_key(self._hotkey_code, X.AnyModifier)
            if hasattr(self, '_hotkey_hide'):
                root.ungrab_key(self._hotkey_hide, X.AnyModifier)
            self.xdisplay.sync()
        except Exception:
            pass
        self.close()
        QApplication.quit()

    def _update_input_region(self):
        if not hasattr(self, '_x11_window'):
            return
        if self._dragging:
            return
        if self._net_active:
            try:
                self._x11_window.shape_rectangles(
                    self._x11_shape.SO.Set, self._x11_shape.SK.Input,
                    self._x11_X.Unsorted, 0, 0,
                    [(0, 0, self.sw, self.sh)],
                )
                self.xdisplay.flush()
            except Exception:
                pass
            return
        pad = 10
        rects = []
        for b in self.butterflies:
            if b.interaction == "caught":
                continue
            x1, y1, x2, y2 = b.bbox
            rects.append((
                max(0, int(x1) - pad), max(0, int(y1) - pad),
                int(x2 - x1) + 2 * pad, int(y2 - y1) + 2 * pad,
            ))
        if COLLECTION_ENABLED:
            for r in (self._net_icon_rect, self._badge_rect):
                rects.append((int(r.x()), int(r.y()), int(r.width()), int(r.height())))
            if self._panel_open:
                r = self._panel_rect
                rects.append((int(r.x()), int(r.y()), int(r.width()), int(r.height())))
        try:
            self._x11_window.shape_rectangles(
                self._x11_shape.SO.Set, self._x11_shape.SK.Input,
                self._x11_X.Unsorted, 0, 0, rects,
            )
            self.xdisplay.flush()
        except Exception:
            pass

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
            if hasattr(self, '_x11_window'):
                try:
                    self._x11_window.shape_rectangles(
                        self._x11_shape.SO.Set, self._x11_shape.SK.Input,
                        self._x11_X.Unsorted, 0, 0,
                        [(0, 0, self.sw, self.sh)],
                    )
                    self.xdisplay.flush()
                except Exception:
                    pass

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
        if event.button() == Qt.LeftButton and not self._net_active:
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
        self._update_input_region()

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

        painter.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._quit()


# ── Entry point ─────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    print("Papillon — press Ctrl+Shift+B to close, Ctrl+Shift+H to hide/show")
    print("Rare species appear over time. Keep watching!")
    overlay = ButterflyOverlay()
    sys.exit(app.exec_())
