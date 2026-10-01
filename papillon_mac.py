#!/usr/bin/env python3
"""
Papillon (macOS) — Beautiful butterflies fill your desktop.
Controls: Ctrl+Shift+B — Quit, Ctrl+Shift+H — Hide/Show
"""

import sys
import time
import threading

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, QRect
from PyQt5.QtGui import QPainter, QCursor, QRegion

from papillon_engine import *


# ── Overlay window (macOS) ──────────────────────────────

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
        self._quit_requested = False
        self._hide_requested = False
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

        QTimer.singleShot(300, self._setup_hotkey)

    def _setup_hotkey(self):
        def hotkey_thread():
            try:
                import Quartz

                def callback(proxy, type_, event, refcon):
                    flags = Quartz.CGEventGetFlags(event)
                    keycode = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode)
                    ctrl = flags & Quartz.kCGEventFlagMaskControl
                    shift = flags & Quartz.kCGEventFlagMaskShift
                    if ctrl and shift and keycode == 11:  # 'b' key
                        self._quit_requested = True
                    elif ctrl and shift and keycode == 4:  # 'h' key
                        self._hide_requested = True
                    return event

                mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                tap = Quartz.CGEventTapCreate(
                    Quartz.kCGSessionEventTap,
                    Quartz.kCGHeadInsertEventTap,
                    Quartz.kCGEventTapOptionListenOnly,
                    mask, callback, None)

                if tap:
                    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
                    loop = Quartz.CFRunLoopGetCurrent()
                    Quartz.CFRunLoopAddSource(
                        loop, source, Quartz.kCFRunLoopDefaultMode)
                    Quartz.CGEventTapEnable(tap, True)
                    Quartz.CFRunLoopRun()
                else:
                    print("Could not create event tap.")
                    print("Grant Accessibility permission in:")
                    print("  System Settings > Privacy & Security > Accessibility")
            except ImportError:
                print("Quartz not available — global hotkey disabled.")
                print("Install with: pip install pyobjc-framework-Quartz")

        t = threading.Thread(target=hotkey_thread, daemon=True)
        t.start()

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
        self.close()
        QApplication.quit()

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

    def _update_input_region(self):
        if self._dragging or self._net_active:
            self.clearMask()
            return
        region = QRegion()
        pad = 10
        for b in self.butterflies:
            if getattr(b, '_caught_done', False):
                continue
            x1, y1, x2, y2 = b.bbox
            region += QRegion(QRect(
                max(0, int(x1) - pad), max(0, int(y1) - pad),
                int(x2 - x1) + 2 * pad, int(y2 - y1) + 2 * pad,
            ))
        r = self._net_icon_rect
        region += QRegion(QRect(int(r.x()) - 4, int(r.y()) - 4,
                                int(r.width()) + 8, int(r.height()) + 8))
        r = self._badge_rect
        region += QRegion(QRect(int(r.x()) - 4, int(r.y()) - 4,
                                int(r.width()) + 8, int(r.height()) + 8))
        if self._panel_open:
            r = self._panel_rect
            region += QRegion(QRect(int(r.x()) - 4, int(r.y()) - 4,
                                    int(r.width()) + 8, int(r.height()) + 8))
        self.setMask(region)

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
        if event.button() == Qt.RightButton and self._net_active:
            self._net_active = False
            return
        if event.button() != Qt.LeftButton:
            return
        x, y = event.x(), event.y()

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
        if event.button() == Qt.LeftButton and not self._net_active:
            x, y = event.x(), event.y()
            b = self._hit_butterfly(x, y)
            if b and b.interaction not in ("held", "caught"):
                b.startle(x, y)

    def _tick(self):
        if self._quit_requested:
            self._quit()
            return
        if self._hide_requested:
            self._hide_requested = False
            self._toggle_hide()
            return

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
    print("Papillon (macOS) — press Ctrl+Shift+B to close, Ctrl+Shift+H to hide/show")
    print("Rare species appear over time. Keep watching!")
    overlay = ButterflyOverlay()
    sys.exit(app.exec_())
