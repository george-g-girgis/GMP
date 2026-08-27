"""
ui/settings.py — Full GUI Settings window for GMP.

A dark-themed, tabbed QDialog that exposes every configurable setting.
All changes are applied live via the ConfigManager's ``changed`` signal.
"""

from __future__ import annotations

import shutil
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from core.config import ConfigManager, VERSION

log = logging.getLogger(__name__)

# ── Palette ──────────────────────────────────────────────────────────
_BG = "#0e0e1a"
_CARD = "#161630"
_BORDER = "rgba(255,255,255,0.08)"
_TEXT = "#ebebf5"
_DIM = "#aaaac3"
_ACCENT = "#8a5cf6"
_ACCENT_HOVER = "#a07bff"
_INPUT_BG = "#1e1e38"

_SETTINGS_FONT: str | None = None

def _font(size: int = 13, bold: bool = False) -> QFont:
    global _SETTINGS_FONT
    if _SETTINGS_FONT is None:
        _SETTINGS_FONT = "Inter" if "Inter" in QFontDatabase.families() else "Segoe UI"
    f = QFont(_SETTINGS_FONT, size)
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


# ── Stylesheet ───────────────────────────────────────────────────────
_DIALOG_CSS = f"""
QDialog {{
    background: {_BG};
    color: {_TEXT};
}}
QTabWidget::pane {{
    border: 1px solid {_BORDER};
    border-radius: 8px;
    background: {_CARD};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {_DIM};
    padding: 10px 20px;
    margin-right: 2px;
    border-bottom: 2px solid transparent;
    font-size: 13px;
}}
QTabBar::tab:selected {{
    color: {_TEXT};
    border-bottom: 2px solid {_ACCENT};
}}
QTabBar::tab:hover {{
    color: {_TEXT};
}}
QLabel {{
    color: {_TEXT};
    background: transparent;
    font-size: 13px;
}}
QCheckBox {{
    color: {_TEXT};
    spacing: 8px;
    font-size: 13px;
}}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 2px solid rgba(255,255,255,0.25);
    border-radius: 4px;
    background: {_INPUT_BG};
}}
QCheckBox::indicator:checked {{
    background: {_ACCENT};
    border-color: {_ACCENT};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: rgba(255,255,255,0.12);
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0;
    background: #fff;
    border-radius: 8px;
    border: 2px solid {_ACCENT};
}}
QSlider::handle:horizontal:hover {{
    border: 3px solid {_ACCENT_HOVER};
}}
QSlider::sub-page:horizontal {{
    background: {_ACCENT};
    border-radius: 2px;
}}
QComboBox {{
    background: {_INPUT_BG};
    color: {_TEXT};
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
    min-width: 160px;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: {_INPUT_BG};
    color: {_TEXT};
    border: 1px solid rgba(255,255,255,0.15);
    selection-background-color: {_ACCENT};
}}
QSpinBox {{
    background: {_INPUT_BG};
    color: {_TEXT};
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 13px;
    min-width: 80px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}
QPushButton {{
    background: {_INPUT_BG};
    color: {_TEXT};
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 13px;
}}
QPushButton:hover {{
    background: rgba(138, 92, 246, 0.25);
    border-color: {_ACCENT};
}}
QPushButton:pressed {{
    background: rgba(138, 92, 246, 0.40);
}}
"""


# ─────────────────────────────────────────────────────────────────────
#  Color swatch button
# ─────────────────────────────────────────────────────────────────────
class _ColorButton(QPushButton):
    """A clickable color swatch that opens a QColorDialog."""

    color_changed = pyqtSignal(str)  # hex string

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedSize(36, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()
        self.clicked.connect(self._pick)

    def _update_style(self) -> None:
        self.setStyleSheet(
            f"QPushButton {{ background: {self._color}; border: 2px solid rgba(255,255,255,0.3);"
            f" border-radius: 6px; min-width: 0; padding: 0; }}"
            f"QPushButton:hover {{ border-color: {_ACCENT}; }}"
        )

    def _pick(self) -> None:
        c = QColorDialog.getColor(
            QColor(self._color), self, "Choose Color",
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if c.isValid():
            self._color = c.name()
            self._update_style()
            self.color_changed.emit(self._color)

    def set_color(self, hex_str: str) -> None:
        self._color = hex_str
        self._update_style()


# ─────────────────────────────────────────────────────────────────────
#  Settings Dialog
# ─────────────────────────────────────────────────────────────────────
class SettingsWindow(QDialog):
    """Full settings dialog with tabbed navigation."""

    resegment_requested = pyqtSignal()
    depth_toggled = pyqtSignal(bool)

    def __init__(self, cfg: ConfigManager, parent=None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle("GMP Settings")
        from pathlib import Path
        ico_file = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
        if ico_file.exists():
            self.setWindowIcon(QIcon(str(ico_file)))
        self.setFixedSize(560, 500)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(_DIALOG_CSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(0)

        # Header
        hdr = QLabel("Settings")
        hdr.setFont(_font(18, True))
        hdr.setStyleSheet(f"color: {_TEXT}; margin-bottom: 8px;")
        root.addWidget(hdr)

        tabs = QTabWidget()
        tabs.setFont(_font(12))
        tabs.addTab(self._build_appearance(), "Appearance")
        tabs.addTab(self._build_colors(), "Colors")
        tabs.addTab(self._build_depth(), "Depth Effect")
        tabs.addTab(self._build_playback(), "Playback")
        tabs.addTab(self._build_startup(), "Startup")
        tabs.addTab(self._build_about(), "About")
        root.addWidget(tabs)

    # ── Tab builders ─────────────────────────────────────────────────

    def _build_appearance(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(16)

        v.addLayout(self._slider_row(
            "Card Opacity", "alpha", 0, 255, self._cfg["alpha"],
        ))
        v.addLayout(self._slider_row(
            "Glow Intensity", "glow", 0, 255, self._cfg["glow"],
        ))

        # Lock layout
        lock_cb = QCheckBox("Lock Layout (prevent dragging & resizing)")
        lock_cb.setChecked(self._cfg["locked"])
        lock_cb.toggled.connect(lambda v: self._cfg.set("locked", v))
        v.addWidget(lock_cb)

        # Player size
        v.addWidget(self._section("Player Size"))
        size_row = QHBoxLayout()
        size_row.setSpacing(12)

        w_spin = QSpinBox()
        w_spin.setRange(320, 800)
        w_spin.setValue(self._cfg["player_w"])
        w_spin.setSuffix(" px")
        w_spin.setPrefix("W: ")
        w_spin.valueChanged.connect(lambda v: self._cfg.set("player_w", v))

        h_spin = QSpinBox()
        h_spin.setRange(190, 600)
        h_spin.setValue(self._cfg["player_h"])
        h_spin.setSuffix(" px")
        h_spin.setPrefix("H: ")
        h_spin.valueChanged.connect(lambda v: self._cfg.set("player_h", v))

        size_row.addWidget(w_spin)
        size_row.addWidget(h_spin)
        size_row.addStretch()
        v.addLayout(size_row)

        v.addStretch()
        return w

    def _build_colors(self) -> QWidget:
        """Colors tab: lyrics color, background color, glow color, auto-theme."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(16)

        # Auto-theme toggle
        auto_cb = QCheckBox("Auto-Theme from Wallpaper")
        auto_cb.setChecked(self._cfg.get("auto_theme", True))
        auto_cb.toggled.connect(lambda on: self._cfg.set("auto_theme", on))
        v.addWidget(auto_cb)

        hint = QLabel("Automatically extract colors from your wallpaper to theme the player.")
        hint.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        v.addSpacing(4)
        v.addWidget(self._section("Manual Colors (overridden when Auto-Theme is on)"))

        # Lyrics color
        v.addLayout(self._color_row("Lyrics Color", "lyrics_color",
                                    self._cfg.get("lyrics_color", "#aaaac3")))

        # Background color
        v.addLayout(self._color_row("Card Background", "bg_color",
                                    self._cfg.get("bg_color", "#16162c")))

        # Glow color
        v.addLayout(self._color_row("Glow Color", "glow_color",
                                    self._cfg.get("glow_color", "#ffffff")))

        v.addStretch()
        return w

    def _build_depth(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(16)

        # Enable toggle
        depth_cb = QCheckBox("Enable Depth Effect")
        depth_cb.setChecked(self._cfg["depth_enabled"])
        depth_cb.toggled.connect(self._on_depth_toggle)
        v.addWidget(depth_cb)

        # Model dropdown
        v.addWidget(self._section("AI Model"))
        model_combo = QComboBox()
        models = ["u2net", "u2netp", "isnet-general-use"]
        model_combo.addItems(models)
        current = self._cfg["model"]
        if current in models:
            model_combo.setCurrentIndex(models.index(current))
        model_combo.currentTextChanged.connect(
            lambda m: self._cfg.set("model", m)
        )
        v.addWidget(model_combo)

        # Action buttons
        v.addWidget(self._section("Actions"))
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        reseg = QPushButton("Re-segment Now")
        reseg.clicked.connect(self.resegment_requested.emit)
        btn_row.addWidget(reseg)

        clear = QPushButton("Clear Cache")
        clear.clicked.connect(self._clear_cache)
        btn_row.addWidget(clear)

        btn_row.addStretch()
        v.addLayout(btn_row)

        v.addStretch()
        return w

    def _build_playback(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(16)

        v.addLayout(self._slider_row(
            "Poll Rate", "poll_ms", 100, 500, self._cfg["poll_ms"],
            suffix=" ms", tooltip="Lower = smoother seek bar, slightly more CPU",
        ))

        lyrics_cb = QCheckBox("Show Synced Lyrics")
        lyrics_cb.setChecked(self._cfg["lyrics_enabled"])
        lyrics_cb.toggled.connect(lambda v: self._cfg.set("lyrics_enabled", v))
        v.addWidget(lyrics_cb)

        v.addStretch()
        return w

    def _build_startup(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(16)

        # Autostart
        from core.autostart import is_enabled as autostart_enabled
        auto_cb = QCheckBox("Run at Windows Login")
        auto_cb.setChecked(autostart_enabled())
        auto_cb.toggled.connect(self._on_autostart)
        v.addWidget(auto_cb)

        hint = QLabel("GMP will start silently in the system tray.")
        hint.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        v.addWidget(hint)

        v.addLayout(self._slider_row(
            "Wallpaper Check Interval", "wallpaper_poll_ms",
            1000, 30000, self._cfg["wallpaper_poll_ms"],
            suffix=" ms",
            tooltip="How often to check if your wallpaper changed",
        ))

        v.addStretch()
        return w

    def _build_about(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 24, 20, 20)
        v.setSpacing(12)

        title = QLabel("GMP — Glass Media Player")
        title.setFont(_font(16, True))
        v.addWidget(title)

        ver = QLabel(f"Version {VERSION}")
        ver.setStyleSheet(f"color: {_ACCENT}; font-size: 13px;")
        v.addWidget(ver)

        desc = QLabel(
            "A depth-layered desktop music overlay for Windows 11.\n"
            "Sits on your desktop behind foreground wallpaper elements\n"
            "with real-time glassmorphism, synced lyrics, and AI depth."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {_DIM}; font-size: 12px; line-height: 1.5;")
        v.addWidget(desc)

        v.addSpacing(16)

        reset = QPushButton("Reset All Settings")
        reset.setStyleSheet(
            "QPushButton { background: rgba(220,50,50,0.15); border-color: rgba(220,50,50,0.4); }"
            "QPushButton:hover { background: rgba(220,50,50,0.30); }"
        )
        reset.clicked.connect(self._reset_all)
        v.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)

        v.addStretch()
        return w

    # ── Helpers ───────────────────────────────────────────────────────

    def _section(self, title: str) -> QLabel:
        lbl = QLabel(title)
        lbl.setFont(_font(11, True))
        lbl.setStyleSheet(f"color: {_DIM}; margin-top: 4px;")
        return lbl

    def _slider_row(
        self, label: str, key: str,
        lo: int, hi: int, val: int,
        suffix: str = "", tooltip: str = "",
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        lbl = QLabel(label)
        lbl.setFixedWidth(160)
        row.addWidget(lbl)

        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(val)
        sl.setFixedHeight(20)
        if tooltip:
            sl.setToolTip(tooltip)
        row.addWidget(sl, 1)

        val_lbl = QLabel(f"{val}{suffix}")
        val_lbl.setFixedWidth(60)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        row.addWidget(val_lbl)

        def _on_change(v: int):
            val_lbl.setText(f"{v}{suffix}")
            self._cfg.set(key, v)

        sl.valueChanged.connect(_on_change)
        return row

    def _color_row(self, label: str, key: str, current: str) -> QHBoxLayout:
        """A row with a label, hex code, and color swatch button."""
        row = QHBoxLayout()
        row.setSpacing(12)

        lbl = QLabel(label)
        lbl.setFixedWidth(140)
        row.addWidget(lbl)

        hex_lbl = QLabel(current.upper())
        hex_lbl.setFixedWidth(70)
        hex_lbl.setStyleSheet(f"color: {_DIM}; font-size: 12px; font-family: 'Consolas';")
        row.addWidget(hex_lbl)

        btn = _ColorButton(current)
        btn.color_changed.connect(lambda c: (
            self._cfg.set(key, c),
            hex_lbl.setText(c.upper()),
        ))
        row.addWidget(btn)
        row.addStretch()
        return row

    # ── Actions ──────────────────────────────────────────────────────

    def _on_depth_toggle(self, on: bool) -> None:
        self._cfg.set("depth_enabled", on)
        # No need to emit depth_toggled — cfg.changed triggers
        # _handle_depth_toggle via _on_cfg_changed in main.py

    def _on_autostart(self, on: bool) -> None:
        from core.autostart import set_enabled
        set_enabled(on)
        self._cfg.set("autostart", on)

    def _clear_cache(self) -> None:
        cache = ConfigManager.cache_dir()
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
            cache.mkdir(parents=True, exist_ok=True)
            log.info("Cache cleared: %s", cache)
        QMessageBox.information(
            self, "Cache Cleared",
            "Segmentation cache has been cleared.\n"
            "The depth effect will re-generate on next wallpaper change.",
        )

    def _reset_all(self) -> None:
        reply = QMessageBox.question(
            self, "Reset Settings",
            "Reset all settings to defaults?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._cfg.reset()
            self.close()
