"""
ui/overlay.py — Dual-layer composite desktop overlay.

Renders the music player UI **below** a foreground cutout mask so the
player appears to be embedded *behind* the wallpaper's foreground
subject (the "depth effect").

Architecture
------------
The window is a full-screen, frameless, translucent QWidget that covers
the primary monitor. It is embedded into the Windows *WorkerW* desktop
layer (behind desktop icons, above the wallpaper) using the well-known
Progman 0x052C trick.

Visual layer stack (painter order, bottom → top):
    0. Wallpaper background (invisible — the real wallpaper shows through)
    1. PlayerWidget (glassmorphic card — interactive)
    2. Foreground cutout mask (QLabel — click-through via QRegion)

Click-through
-------------
We use ``setMask(QRegion)`` to declare that *only* the player-widget
rectangle is "solid" for input purposes. Everything else (including the
foreground overlay) passes clicks down to the desktop.

Native Dragging
---------------
The player widget emits a `dragged(dx, dy)` signal when the user drags it.
The overlay listens to this, moves the widget, and immediately refreshes
both the click-through mask and the glassmorphic background blur in
real time.
"""

from __future__ import annotations

import ctypes
import logging

from PyQt6.QtCore import QElapsedTimer, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QPainter, QPixmap, QRegion
from PyQt6.QtWidgets import QLabel, QWidget

from .widget import PlayerWidget

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# Win32 constants
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_HWND_BOTTOM = 1


# ─────────────────────────────────────────────────────────────────────
#  WorkerW finder
# ─────────────────────────────────────────────────────────────────────
def _find_workerw() -> int | None:
    """
    Locate (or create) the WorkerW window that sits between the
    desktop wallpaper and the icon view.
    """
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return None

    # Ask Progman to spawn the extra WorkerW layer
    user32.SendMessageTimeoutW(
        progman, 0x052C, 0, 0, 0x0000, 1000,
        ctypes.byref(ctypes.c_ulong()),
    )

    result = None

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lparam):
        nonlocal result
        shelldll = user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
        if shelldll:
            result = user32.FindWindowExW(0, hwnd, "WorkerW", None)
        return True

    user32.EnumWindows(_cb, 0)
    return result


# ─────────────────────────────────────────────────────────────────────
#  Overlay Window
# ─────────────────────────────────────────────────────────────────────
class OverlayWindow(QWidget):
    """Full-screen overlay with depth-layered music player."""

    player_moved = pyqtSignal(int, int)   # (x, y) after drag

    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent)
        self._cfg = cfg

        self._ww_hwnd: int | None = None
        self._wp_px: QPixmap | None = None     # full wallpaper
        self._fg_px: QPixmap | None = None     # foreground mask

        # detect the virtual desktop that spans ALL connected screens
        screens = QGuiApplication.screens()
        if screens:
            combined = screens[0].geometry()
            for scr in screens[1:]:
                combined = combined.united(scr.geometry())
            self._scr = QSize(combined.width(), combined.height())
            self._scr_rect = combined
        else:
            self._scr = QSize(1920, 1080)
            self._scr_rect = QRect(0, 0, 1920, 1080)
        log.info("Virtual desktop: %dx%d across %d screen(s)",
                 self._scr.width(), self._scr.height(), len(screens))

        self._blur_timer = QElapsedTimer()
        self._blur_timer.start()
        self._blur_throttle_ms = 50  # min ms between blur recalcs

        self._init_flags()
        self._init_layers()

    # ── window setup ─────────────────────────────────────────────────

    def _init_flags(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnBottomHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(self._scr_rect)

    def _init_layers(self) -> None:
        w = self._cfg.get("player_w", 430)
        h = self._cfg.get("player_h", 240)

        # Layer 1: the player card (interactive)
        self._player = PlayerWidget(self._cfg, w, h, self)
        self._player.move(200, 400)
        
        # Connect native drag & resize
        self._player.dragged.connect(self._on_player_dragged)
        self._player.resized.connect(self._on_player_resized)

        # Layer 2: foreground mask (visual-only, click-through)
        self._fg = QLabel(self)
        self._fg.setGeometry(self._scr_rect)
        self._fg.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._fg.raise_()

        self._refresh_mask()

    # ── public API ───────────────────────────────────────────────────

    @property
    def player(self) -> PlayerWidget:
        return self._player

    def move_player(self, x: int, y: int) -> None:
        self._player.move(x, y)
        self._refresh_mask()
        self._refresh_blur()

    def set_wallpaper(self, px: QPixmap) -> None:
        """Provide the raw wallpaper pixmap (for blur-background capture)."""
        if px and not px.isNull() and px.size() != self._scr:
            self._wp_px = px.scaled(
                self._scr,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            self._wp_px = px
        self._refresh_blur()

    def set_foreground(self, px: QPixmap) -> None:
        """
        Apply the foreground cutout mask.  Must be RGBA with the subject
        opaque and the background transparent.  The pixmap is scaled to
        exact screen resolution for 1:1 alignment with the desktop wallpaper.
        """
        if px.isNull():
            self._fg_px = None
            self._fg.clear()
            return

        # Scale to exact screen resolution — critical for pixel alignment
        scaled = px.scaled(
            self._scr,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._fg_px = scaled
        self._fg.setPixmap(scaled)
        self._fg.raise_()
        log.info("Foreground mask applied (%dx%d)", scaled.width(), scaled.height())

    def clear_foreground(self) -> None:
        self._fg_px = None
        self._fg.clear()

    # ── desktop embedding ────────────────────────────────────────────

    def embed(self) -> None:
        """
        Embed into the WorkerW desktop layer so the overlay sits between
        the wallpaper and the desktop icons.
        """
        self._ww_hwnd = _find_workerw()
        if self._ww_hwnd:
            hwnd = int(self.winId())
            user32.SetParent(hwnd, self._ww_hwnd)
            self.setGeometry(self._scr_rect)
            self._refresh_mask()
            log.info("Embedded into WorkerW (hwnd=%s)", hex(self._ww_hwnd))
        else:
            log.warning("WorkerW not found — falling back to bottom Z-order")
            self._push_bottom()

    def _push_bottom(self) -> None:
        hwnd = int(self.winId())
        user32.SetWindowPos(
            hwnd, _HWND_BOTTOM, 0, 0, 0, 0,
            _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
        )

    # ── native dragging ──────────────────────────────────────────────

    def _on_player_dragged(self, dx: int, dy: int) -> None:
        """Handle continuous dragging from the PlayerWidget."""
        r = self._scr_rect
        x = max(r.left(), min(self._player.x() + dx, r.right() - self._player.width()))
        y = max(r.top(), min(self._player.y() + dy, r.bottom() - self._player.height()))
        self._player.move(x, y)
        self._refresh_mask()
        # Throttle blur during drag — only recalc every _blur_throttle_ms
        if self._blur_timer.elapsed() >= self._blur_throttle_ms:
            self._refresh_blur()
            self._blur_timer.restart()
        self.player_moved.emit(x, y)

    def _on_player_resized(self) -> None:
        """Handle dynamic resizing from the PlayerWidget."""
        self._refresh_mask()
        self._refresh_blur()
        # Persist the new size — write directly to avoid re-triggering
        # widget._on_cfg_changed → resize → resized signal loop
        w = self._player.width()
        h = self._player.height()
        self._cfg._data["player_w"] = w
        self._cfg._data["player_h"] = h
        self._cfg.save()

    # ── click-through mask ───────────────────────────────────────────

    def _refresh_mask(self) -> None:
        """
        Update the input region so only the player card receives clicks.
        """
        pr = QRect(
            self._player.x(), self._player.y(),
            self._player.width(), self._player.height(),
        )
        self.setMask(QRegion(pr))

    # ── blur background ──────────────────────────────────────────────

    def _refresh_blur(self) -> None:
        """
        Capture + blur the wallpaper slice behind the player for the
        glassmorphism effect.
        """
        if not self._wp_px or self._wp_px.isNull():
            return
        wp = self._wp_px
        pr = QRect(
            self._player.x(), self._player.y(),
            self._player.width(), self._player.height(),
        ).intersected(QRect(0, 0, wp.width(), wp.height()))
        if pr.isEmpty():
            return

        crop = wp.copy(pr)
        # fast box blur via scale-down / scale-up
        tiny = crop.scaled(
            max(1, crop.width() // 8),
            max(1, crop.height() // 8),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        blurred = tiny.scaled(
            crop.width(), crop.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._player.set_blur_bg(blurred)
