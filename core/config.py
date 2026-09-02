"""
core/config.py — Centralised configuration manager for GMP.

Provides a single source of truth for every setting the application uses.
All modules receive a reference to the same ``ConfigManager`` instance and
react to live changes via the ``changed`` signal.

Settings are persisted to ``%APPDATA%/GMP/config.json``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

VERSION = "1.1.0"

_CFG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "GMP"
_CFG_FILE = _CFG_DIR / "config.json"
_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

DEFAULTS: dict[str, Any] = {
    # player position & size
    "player_x": 200,
    "player_y": 400,
    "player_w": 430,
    "player_h": 240,
    # appearance
    "alpha": 150,
    "glow": 140,
    "locked": False,
    "always_on_top": True,
    "click_through": False,
    "lyrics_color": "#aaaac3",
    "bg_color": "#16162c",
    "glow_color": "#ffffff",
    "auto_theme": True,
    # depth effect
    "depth_enabled": True,
    "model": "u2net",
    # playback
    "poll_ms": 200,
    "lyrics_enabled": True,
    # media source & video mode
    "media_source_mode": "auto",       # "auto" | "manual"
    "selected_media_source": "",       # app_id when in manual mode
    # captions & speech AI
    "captions_mode": "auto",           # "auto" | "speech_only" | "lrclib_only" | "disabled"
    "captions_lang_mode": "auto",      # "auto" | "manual"
    "captions_manual_lang": "en",      # language code when in manual mode
    "captions_whisper_model": "base",  # "base" | "small"
    "captions_show_badge": True,       # show language tag [EN], [AR], etc.
    # startup / system
    "autostart": False,
    "wallpaper_poll_ms": 5000,
    # internal
    "first_run": True,
    "version": VERSION,
}


class ConfigManager(QObject):
    """
    Thread-safe, signal-driven settings store.

    Usage::

        cfg = ConfigManager()
        cfg.changed.connect(on_setting_changed)
        cfg["alpha"] = 180          # emits changed("alpha", 180)
        v = cfg["alpha"]            # → 180
    """

    changed = pyqtSignal(str, object)  # (key, new_value)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._data: dict[str, Any] = {}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._do_save)
        self._load()

    # ── dict-like access ─────────────────────────────────────────────

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        if self._data.get(key) != value:
            self._data[key] = value
            self.changed.emit(key, value)

    def get(self, key: str, default: Any = None) -> Any:
        v = self._data.get(key)
        if v is not None:
            return v
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        """Set a value, optionally auto-saving to disk."""
        self[key] = value
        if save:
            self.save()

    def update(self, d: dict[str, Any], *, save: bool = True) -> None:
        """Batch-update multiple keys."""
        for k, v in d.items():
            self[k] = v
        if save:
            self.save()

    # ── persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load from disk, falling back to defaults for missing keys."""
        self._data = dict(DEFAULTS)
        if _CFG_FILE.exists():
            try:
                with open(_CFG_FILE, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                self._data.update(stored)
                log.info("Config loaded from %s", _CFG_FILE)
            except Exception as exc:
                log.warning("Config load failed, using defaults: %s", exc)

    def save(self) -> None:
        """Schedule a debounced persist to disk (coalesces rapid changes)."""
        if not self._save_timer.isActive():
            self._save_timer.start()

    def save_now(self) -> None:
        """Persist immediately (for shutdown or critical saves)."""
        self._save_timer.stop()
        self._do_save()

    def _do_save(self) -> None:
        """Actually write to disk."""
        _CFG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(_CFG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as exc:
            log.warning("Config save failed: %s", exc)

    def reset(self) -> None:
        """Reset all settings to defaults and save."""
        self._data = dict(DEFAULTS)
        self.save_now()
        log.info("Config reset to defaults")

    # ── convenience properties ───────────────────────────────────────

    @staticmethod
    def config_dir() -> Path:
        return _CFG_DIR

    @staticmethod
    def cache_dir() -> Path:
        return _CACHE_DIR
