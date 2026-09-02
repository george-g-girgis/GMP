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


def _fit_to_screen(px: QPixmap, target_size: QSize) -> QPixmap:
    """
    Scale and center-crop pixmap to match Windows 11 default desktop wallpaper
    rendering (WallpaperStyle: 10 / Fill) with 100% pixel-perfect alignment.
    """
    if px.isNull() or target_size.isEmpty():
        return px
    scaled = px.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    if scaled.size() == target_size:
        return scaled
    cx = max(0, (scaled.width() - target_size.width()) // 2)
    cy = max(0, (scaled.height() - target_size.height()) // 2)
    return scaled.copy(cx, cy, target_size.width(), target_size.height())


# ─────────────────────────────────────────────────────────────────────
#  Overlay Window
# ─────────────────────────────────────────────────────────────────────
class OverlayWindow(QWidget):
    """
    Multi-mode overlay window:
    - Desktop Mode: Full-screen per monitor, embedded in WorkerW, 3D depth cutout mask.
    - Always-on-Top Floating Mode: Single compact card hovering over any connected screen,
      depth effect disabled, free to move anywhere across all monitors.
    """

    player_moved = pyqtSignal(int, int)   # (x, y) after drag

    def __init__(self, cfg, target_screen=None, is_floating: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._is_floating = is_floating
        self._screen = target_screen or QGuiApplication.primaryScreen()

        self._ww_hwnd: int | None = None
        self._wp_px: QPixmap | None = None     # full wallpaper
        self._fg_px: QPixmap | None = None     # foreground mask
        self._fg: QLabel | None = None

        if self._is_floating:
            # Compact floating window sizing to player card
            w = self._cfg.get("player_w", 430)
            h = self._cfg.get("player_h", 240)
            self._scr = QSize(w, h)
            self._scr_rect = QRect(0, 0, w, h)
        else:
            # Full-screen overlay covering the target monitor
            geo = self._screen.geometry() if self._screen else QRect(0, 0, 1920, 1080)
            self._scr = QSize(geo.width(), geo.height())
            self._scr_rect = QRect(0, 0, geo.width(), geo.height())
            log.info("Screen overlay [%s]: %dx%d at (%d,%d)", self.screen_name, geo.width(), geo.height(), geo.x(), geo.y())

        self._blur_timer = QElapsedTimer()
        self._blur_timer.start()
        self._blur_throttle_ms = 50  # min ms between blur recalcs

        self._init_flags()
        self._init_layers()

    @property
    def screen_name(self) -> str:
        return self._screen.name() if self._screen else "Primary"

    @property
    def is_floating(self) -> bool:
        return self._is_floating

    # ── window setup ─────────────────────────────────────────────────

    def _init_flags(self) -> None:
        if self._is_floating:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            px = self._cfg.get("player_x", 200)
            py = self._cfg.get("player_y", 400)
            self.setGeometry(px, py, self._scr.width(), self._scr.height())
        else:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnBottomHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            if self._screen:
                self.setGeometry(self._screen.geometry())
            else:
                self.setGeometry(self._scr_rect)

    def _init_layers(self) -> None:
        w = self._cfg.get("player_w", 430)
        h = self._cfg.get("player_h", 240)

        # Layer 1: the player card (interactive)
        self._player = PlayerWidget(self._cfg, w, h, self)
        if self._is_floating:
            self._player.move(0, 0)
        else:
            self._player.move(200, 400)
        
        # Connect native drag & resize
        self._player.dragged.connect(self._on_player_dragged)
        self._player.resized.connect(self._on_player_resized)

        # Layer 2: foreground mask (visual-only, click-through) - only in desktop mode!
        if not self._is_floating:
            self._fg = QLabel(self)
            self._fg.setGeometry(0, 0, self._scr.width(), self._scr.height())
            self._fg.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._fg.raise_()

        self._refresh_mask()

    # ── public API ───────────────────────────────────────────────────

    @property
    def player(self) -> PlayerWidget:
        return self._player

    def move_player(self, x: int, y: int) -> None:
        if self._is_floating:
            self.move(x, y)
        else:
            self._player.move(x, y)
            self._refresh_mask()
            self._refresh_blur()

    def set_wallpaper(self, px: QPixmap) -> None:
        """Provide the wallpaper pixmap (fitted 1:1 to screen for blur-background capture)."""
        if px is None or px.isNull():
            self._wp_px = None
            return

        if self._is_floating:
            self._wp_px = px
        else:
            self._wp_px = _fit_to_screen(px, self._scr)
            self._refresh_blur()

    def set_foreground(self, px: QPixmap) -> None:
        """Apply an RGBA foreground mask cutout over the overlay."""
        if self._is_floating or not self._fg:
            return  # Depth effect is disabled in floating always-on-top mode!

        if px is None or px.isNull():
            self._fg_px = None
            self._fg.clear()
            return

        # Scale and align matching Windows 11 wallpaper geometry
        scaled = _fit_to_screen(px, self._scr)
        self._fg_px = scaled
        self._fg.setPixmap(scaled)
        self._fg.raise_()
        log.info("Foreground mask applied for %s (%dx%d)", self.screen_name, scaled.width(), scaled.height())

    def clear_foreground(self) -> None:
        self._fg_px = None
        if self._fg:
            self._fg.clear()

    # ── desktop embedding ────────────────────────────────────────────

    def embed(self) -> None:
        """
        Embed into the WorkerW desktop layer if in Desktop mode,
        or stay topmost if in floating mode.
        """
        if self._is_floating:
            hwnd = int(self.winId())
            user32.SetParent(hwnd, None)
            user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0,  # HWND_TOPMOST
                _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
            )
            self.clearMask()
            return

        self._ww_hwnd = _find_workerw()
        if self._ww_hwnd:
            hwnd = int(self.winId())
            user32.SetParent(hwnd, self._ww_hwnd)
            if self._screen:
                self.setGeometry(self._screen.geometry())
            else:
                self.setGeometry(self._scr_rect)
            self._refresh_mask()
            log.info("Embedded into WorkerW (hwnd=%s, screen=%s)", hex(self._ww_hwnd), self.screen_name)
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
        if self._is_floating:
            nx = self.x() + dx
            ny = self.y() + dy
            self.move(nx, ny)
            self._cfg._data["player_x"] = nx
            self._cfg._data["player_y"] = ny
            self._cfg.save()
            self.player_moved.emit(nx, ny)
            return

        x = max(0, min(self._player.x() + dx, self._scr.width() - self._player.width()))
        y = max(0, min(self._player.y() + dy, self._scr.height() - self._player.height()))
        self._player.move(x, y)
        self._refresh_mask()
        # Throttle blur during drag — only recalc every _blur_throttle_ms
        if self._blur_timer.elapsed() >= self._blur_throttle_ms:
            self._refresh_blur()
            self._blur_timer.restart()
        self.player_moved.emit(x, y)

    def _on_player_resized(self) -> None:
        """Handle dynamic resizing from the PlayerWidget."""
        if self._is_floating:
            w = self._player.width()
            h = self._player.height()
            self.resize(w, h)
            self._cfg._data["player_w"] = w
            self._cfg._data["player_h"] = h
            self._cfg.save()
            return

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
        Update the input region so only the player card receives clicks,
        or make completely click-through if Overlay Mode (click_through) is enabled.
        """
        if self._is_floating:
            self.clearMask()
            return

        if self._cfg.get("click_through", False) and self._cfg.get("locked", False):
            # Overlay Mode: click-through
            self.setMask(QRegion())
            return

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
