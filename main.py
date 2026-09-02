"""
main.py — Entry point for the Glassmorphic Depth Music Player v1.0.

Orchestrates all modules:
  • ConfigManager    → centralised settings store
  • WallpaperWatcher → detects wallpaper changes
  • Segmenter        → extracts foreground mask (threaded, cached)
  • MediaController  → Windows GSMTC media bridge
  • OverlayWindow    → composited desktop widget
  • SettingsWindow   → full GUI settings dialog
  • SetupWizard      → first-run configuration flow

Runs entirely from the system tray; no taskbar presence.
"""

from __future__ import annotations

import logging
import sys
from collections import Counter

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from core.config import ConfigManager, VERSION
from core.media import MediaController
from core.segmenter import Segmenter
from core.wallpaper import WallpaperWatcher
from ui.overlay import OverlayWindow

log = logging.getLogger("GMP")


# ── tray icon helpers ────────────────────────────────────────────────

def _make_icon() -> QIcon:
    """Load the application icon from assets or draw a fallback."""
    from pathlib import Path
    base = Path(__file__).resolve().parent
    png_path = base / "assets" / "icon.png"
    ico_path = base / "assets" / "app.ico"
    if png_path.exists():
        return QIcon(str(png_path))
    if ico_path.exists():
        return QIcon(str(ico_path))

    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(138, 92, 246))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 28, 28)
    p.setPen(QColor(255, 255, 255))
    p.setFont(QFont("Segoe UI Emoji", 14))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "♫")
    p.end()
    return QIcon(pm)


_MENU_CSS = """
QMenu {
    background-color: #1a1a2e;
    color: #ebebf5;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 8px;
    padding: 6px 2px;
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}
QMenu::item {
    padding: 8px 20px;
    border-radius: 4px;
    margin: 2px 6px;
}
QMenu::item:selected {
    background-color: rgba(138,92,246,0.30);
}
QMenu::separator {
    height: 1px;
    background: rgba(255,255,255,0.08);
    margin: 4px 12px;
}
"""


# ─────────────────────────────────────────────────────────────────────
#  Application controller
# ─────────────────────────────────────────────────────────────────────
class App:
    """Wires modules together and manages the tray lifecycle."""

    def __init__(self, cfg: ConfigManager) -> None:
        self._cfg = cfg
        self._wp_path: str | None = None
        self._settings_win = None

        # ── core modules ──
        self._watcher = WallpaperWatcher(
            poll_interval_ms=self._cfg["wallpaper_poll_ms"],
        )
        self._segmenter = Segmenter(
            cache_dir=ConfigManager.cache_dir(),
            model=self._cfg["model"],
        )
        self._media = MediaController(poll_ms=self._cfg["poll_ms"], cfg=self._cfg)

        # ── UI ──
        self._overlay = OverlayWindow(self._cfg)

        self._wire()
        self._tray = self._build_tray()

        # React to live config changes
        self._cfg.changed.connect(self._on_cfg_changed)

    # ── live config changes ──────────────────────────────────────────

    def _on_cfg_changed(self, key: str, value) -> None:
        if key == "model":
            self._segmenter.model = value
        elif key == "depth_enabled":
            self._handle_depth_toggle(value)
        elif key == "player_w" or key == "player_h":
            # widget listens directly; just persist
            self._cfg.save()

    # ── signal wiring ────────────────────────────────────────────────

    def _wire(self) -> None:
        p = self._overlay.player

        # wallpaper → segmenter pipeline
        self._watcher.wallpaper_changed.connect(self._on_wp)

        # segmenter → overlay
        self._segmenter.ready.connect(self._on_mask_ready)
        self._segmenter.error.connect(self._on_mask_error)
        self._segmenter.progress.connect(self._on_seg_progress)

        # media → player
        self._media.track_changed.connect(p.set_track)
        self._media.lyrics_changed.connect(p.set_lyrics)
        self._media.caption_ready.connect(p.set_caption)
        self._media.playback_changed.connect(p.set_playing)
        self._media.position_changed.connect(p.set_position)
        self._media.session_lost.connect(p.set_idle)
        self._media.auth_failed.connect(p.set_auth_failed)
        self._media.shuffle_changed.connect(p.set_shuffle)
        self._media.repeat_changed.connect(p.set_repeat)

        # player → media
        p.play_pause.connect(self._media.play_pause)
        p.next_track.connect(self._media.next_track)
        p.prev_track.connect(self._media.previous_track)
        p.shuffle.connect(self._media.trigger_shuffle)
        p.repeat.connect(self._media.trigger_repeat)
        p.seek.connect(self._media.trigger_seek)

        # player right-click → settings
        p.open_settings.connect(self._open_settings)

        # overlay position save
        self._overlay.player_moved.connect(self._on_player_moved)

    # ── tray ─────────────────────────────────────────────────────────

    def _build_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon()
        tray.setIcon(_make_icon())
        tray.setToolTip(f"GMP v{VERSION}")

        menu = QMenu()
        menu.setStyleSheet(_MENU_CSS)

        a1 = QAction("⚙  Settings", menu)
        a1.triggered.connect(self._open_settings)
        menu.addAction(a1)

        menu.addSeparator()

        a2 = QAction("🔄  Re-segment Wallpaper", menu)
        a2.triggered.connect(self._resegment)
        menu.addAction(a2)

        a3 = QAction("🌊  Toggle Depth Effect", menu)
        a3.triggered.connect(self._toggle_depth)
        menu.addAction(a3)

        menu.addSeparator()

        a4 = QAction("✖  Quit", menu)
        a4.triggered.connect(self._quit)
        menu.addAction(a4)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray)
        return tray

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        self._overlay.move_player(self._cfg["player_x"], self._cfg["player_y"])
        self._overlay.show()
        self._overlay.embed()

        self._tray.show()
        self._tray.showMessage(
            f"GMP v{VERSION}",
            "Running in the system tray. Right-click for options.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

        self._watcher.start()
        self._media.start()

    # ── handlers ─────────────────────────────────────────────────────

    def _on_wp(self, path: str) -> None:
        if not path:
            self._overlay.clear_foreground()
            self._overlay.set_wallpaper(QPixmap())
            self._wp_path = None
            return
        self._wp_path = path
        self._overlay.set_wallpaper(QPixmap(path))

        # Auto-theme: extract dominant colors from wallpaper
        if self._cfg.get("auto_theme", True):
            self._apply_wallpaper_theme(path)

        if self._cfg["depth_enabled"]:
            cached = self._segmenter.try_cache(path)
            if cached:
                self._overlay.set_foreground(cached)
            else:
                self._segmenter.segment(path)

    def _on_mask_ready(self, px: QPixmap, _path: str) -> None:
        self._overlay.set_foreground(px)
        self._tray.showMessage(
            "Depth Effect Ready", "Foreground segmentation complete ✓",
            QSystemTrayIcon.MessageIcon.Information, 2000,
        )

    def _on_mask_error(self, msg: str, _path: str) -> None:
        self._overlay.clear_foreground()
        self._tray.showMessage(
            "Segmentation Failed", msg[:120],
            QSystemTrayIcon.MessageIcon.Warning, 4000,
        )

    def _on_seg_progress(self, text: str) -> None:
        self._tray.setToolTip(f"GMP — {text}")

    def _on_player_moved(self, x: int, y: int) -> None:
        # Write directly to avoid 2 changed signals per drag pixel
        self._cfg._data["player_x"] = x
        self._cfg._data["player_y"] = y
        self._cfg.save()

    def _on_tray(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_settings()

    # ── settings window ──────────────────────────────────────────────

    def _open_settings(self) -> None:
        if self._settings_win and self._settings_win.isVisible():
            self._settings_win.raise_()
            self._settings_win.activateWindow()
            return

        from ui.settings import SettingsWindow
        self._settings_win = SettingsWindow(self._cfg, self._media)
        self._settings_win.resegment_requested.connect(self._resegment)
        self._settings_win.show()

    # ── actions ──────────────────────────────────────────────────────

    def _resegment(self) -> None:
        if self._wp_path:
            self._segmenter.segment(self._wp_path)
        else:
            self._tray.showMessage(
                "No Wallpaper",
                "No wallpaper image detected to segment.",
                QSystemTrayIcon.MessageIcon.Warning, 2000,
            )

    def _toggle_depth(self) -> None:
        new_val = not self._cfg["depth_enabled"]
        self._cfg.set("depth_enabled", new_val)

    def _handle_depth_toggle(self, enabled: bool) -> None:
        if enabled:
            if self._wp_path:
                cached = self._segmenter.try_cache(self._wp_path)
                if cached:
                    self._overlay.set_foreground(cached)
                else:
                    self._segmenter.segment(self._wp_path)
            self._tray.showMessage("Depth", "Enabled ✓",
                                   QSystemTrayIcon.MessageIcon.Information, 1500)
        else:
            self._overlay.clear_foreground()
            self._tray.showMessage("Depth", "Disabled",
                                   QSystemTrayIcon.MessageIcon.Information, 1500)

    def _apply_wallpaper_theme(self, path: str) -> None:
        """Extract dominant colors from wallpaper and apply to player theme."""
        try:
            from PIL import Image

            img = Image.open(path).convert("RGB")
            # Fast subsample: resize to tiny for speed
            img = img.resize((64, 64), Image.Resampling.LANCZOS)
            # Extract pixels as (R, G, B) tuples — fast batch via tobytes
            raw = img.tobytes()
            pixels = [(raw[i], raw[i+1], raw[i+2]) for i in range(0, len(raw), 3)]

            # Quantise to reduce noise: round channels to nearest 32
            def q(c):
                return (c[0] // 32 * 32, c[1] // 32 * 32, c[2] // 32 * 32)

            quantised = [q(p) for p in pixels]
            counts = Counter(quantised).most_common(10)

            # Pick a dark dominant color for background (luminance < 120)
            bg = None
            for color, _ in counts:
                lum = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
                if lum < 120:
                    bg = color
                    break

            if bg is None:
                # All colors are bright — darken the most common one
                c = counts[0][0]
                bg = (c[0] // 3, c[1] // 3, c[2] // 3)

            # Glow: brighten the dominant color
            glow = (
                min(255, bg[0] + 100),
                min(255, bg[1] + 100),
                min(255, bg[2] + 100),
            )

            # Lyrics: use a light tint of the brightest dominant color
            bright = counts[0][0]
            lyrics = (
                min(255, bright[0] + 80),
                min(255, bright[1] + 80),
                min(255, bright[2] + 80),
            )

            bg_hex = "#{:02x}{:02x}{:02x}".format(*bg)
            glow_hex = "#{:02x}{:02x}{:02x}".format(*glow)
            lyrics_hex = "#{:02x}{:02x}{:02x}".format(*lyrics)

            self._cfg.set("bg_color", bg_hex, save=False)
            self._cfg.set("glow_color", glow_hex, save=False)
            self._cfg.set("lyrics_color", lyrics_hex, save=False)
            self._cfg.save()

            log.info("Auto-theme applied: bg=%s glow=%s lyrics=%s",
                     bg_hex, glow_hex, lyrics_hex)

        except Exception as exc:
            log.warning("Auto-theme extraction failed: %s", exc)


    def _quit(self) -> None:
        self._watcher.stop()
        self._media.stop()
        self._cfg.save_now()  # flush any pending saves
        self._overlay.close()
        self._tray.hide()
        if self._settings_win:
            self._settings_win.close()
        QApplication.instance().quit()


# ─────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-18s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
    )
    
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

    if "--uninstall" in sys.argv:
        from uninstall import main as uninstall_main
        uninstall_main()
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("GMP — Glass Media Player")
    app.setApplicationVersion(VERSION)
    app.setWindowIcon(_make_icon())
    app.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Check for first run → show setup wizard
    cfg = ConfigManager()
    if cfg["first_run"]:
        from ui.setup import SetupWizard
        wizard = SetupWizard(cfg)
        result = wizard.exec()
        if result != wizard.DialogCode.Accepted:
            # User closed wizard without finishing — still mark done
            cfg["first_run"] = False
            cfg.save()

    controller = App(cfg)
    controller.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
