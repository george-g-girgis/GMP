"""
core/wallpaper.py — Windows desktop wallpaper change listener.

Detects the current wallpaper path via SystemParametersInfoW (Win32)
and polls for changes on a QTimer. Emits a Qt signal when the wallpaper
file path changes. Handles edge cases: solid-color backgrounds, Windows
Spotlight, slideshow rotation.
"""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

# ── Win32 constants ──────────────────────────────────────────────────
SPI_GETDESKWALLPAPER = 0x0073
MAX_PATH = 260


class WallpaperWatcher(QObject):
    """
    Polls Windows for the current desktop wallpaper path at a configurable
    interval. Emits ``wallpaper_changed(str)`` only when the path actually
    differs from the previously detected one.

    Emits an empty string when a solid-colour background is active (no
    wallpaper file).
    """

    wallpaper_changed = pyqtSignal(str)  # absolute path, or "" for solid

    def __init__(
        self,
        poll_interval_ms: int = 5000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._interval = poll_interval_ms
        self._current: str | None = None  # last-seen path (None = never checked)
        self._current_mtime: float | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ── public API ───────────────────────────────────────────────────

    def start(self) -> None:
        """Begin polling. Fires an immediate check before starting the timer."""
        self._tick()
        self._timer.start(self._interval)
        log.info("WallpaperWatcher started (every %d ms)", self._interval)

    def stop(self) -> None:
        self._timer.stop()
        log.info("WallpaperWatcher stopped")

    @staticmethod
    def get_current_wallpaper() -> str | None:
        """
        Query the OS for the active wallpaper image path.

        Returns *None* when Windows reports a solid colour or the file
        cannot be found on disk.
        """
        buf = ctypes.create_unicode_buffer(MAX_PATH)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETDESKWALLPAPER, MAX_PATH, buf, 0
        )
        if ok and buf.value:
            p = Path(buf.value)
            if p.is_file():
                return str(p)
            log.warning("Wallpaper path reported but file missing: %s", buf.value)
        return None

    # ── internals ────────────────────────────────────────────────────

    def _tick(self) -> None:
        path = self.get_current_wallpaper() or ""
        mtime = None
        if path:
            try:
                mtime = Path(path).stat().st_mtime
            except OSError:
                pass

        if path != self._current or (mtime is not None and mtime != self._current_mtime):
            self._current = path
            self._current_mtime = mtime
            log.info("Wallpaper changed → %s", path or "(solid colour)")
            self.wallpaper_changed.emit(path)
