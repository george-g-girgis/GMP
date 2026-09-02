"""
ui/settings.py — Full GUI Settings window for GMP.

A dark-themed, tabbed QDialog that exposes every configurable setting.
All changes are applied live via the ConfigManager's ``changed`` signal.
"""

from __future__ import annotations

import shutil
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
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

    def __init__(self, cfg: ConfigManager, media=None, parent=None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._media = media
        self._latest_sessions: list = []
        self.setWindowTitle("GMP Settings")
        from pathlib import Path
        ico_file = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
        if ico_file.exists():
            self.setWindowIcon(QIcon(str(ico_file)))
        self.setFixedSize(600, 560)
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
        tabs.addTab(self._build_sources(), "Media Sources")
        tabs.addTab(self._build_captions(), "Captions & AI")
        tabs.addTab(self._build_screens(), "Screens")
        tabs.addTab(self._build_colors(), "Colors")
        tabs.addTab(self._build_depth(), "Depth Effect")
        tabs.addTab(self._build_playback(), "Playback")
        tabs.addTab(self._build_startup(), "Startup")
        tabs.addTab(self._build_about(), "About")
        root.addWidget(tabs)

        if self._media:
            self._media.sessions_updated.connect(self._populate_sources_ui)
            self._media.refresh_sessions()

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

        # Always on top
        top_cb = QCheckBox("📌 Always on Top (Float above all windows across any screen)")
        top_cb.setChecked(self._cfg.get("always_on_top", False))
        top_cb.toggled.connect(lambda v: self._cfg.set("always_on_top", v))
        v.addWidget(top_cb)

        hint = QLabel("When enabled, the player hovers over all applications and can move across any screen. Desktop depth effect is temporarily disabled.")
        hint.setStyleSheet(f"color: {_DIM}; font-size: 11px; margin-left: 26px; margin-top: -8px; margin-bottom: 4px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

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

    def _build_sources(self) -> QWidget:
        """Media Sources tab: choose between Auto (smart priority) or Manual selection via dropdown."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(16)

        v.addWidget(self._section("Playback Selection Mode"))

        # Mode Radio buttons
        mode_group = QVBoxLayout()
        mode_group.setSpacing(10)

        self._auto_rb = QRadioButton("Auto Priority (Spotify → YouTube → VLC → Other)")
        self._auto_rb.setFont(_font(12, True))
        self._auto_rb.setStyleSheet(f"color: {_TEXT};")

        self._manual_rb = QRadioButton("Manual Selection (Lock to chosen player from dropdown below)")
        self._manual_rb.setFont(_font(12, True))
        self._manual_rb.setStyleSheet(f"color: {_TEXT};")

        current_mode = self._cfg.get("media_source_mode", "auto")
        if current_mode == "manual":
            self._manual_rb.setChecked(True)
        else:
            self._auto_rb.setChecked(True)

        mode_group.addWidget(self._auto_rb)
        mode_group.addWidget(self._manual_rb)
        v.addLayout(mode_group)

        desc = QLabel(
            "• Auto: Only currently playing media is displayed, with strict priority: Spotify → YouTube → VLC.\n"
            "• Manual: GMP ignores priority and strictly displays your chosen player below."
        )
        desc.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        desc.setWordWrap(True)
        v.addWidget(desc)

        # Dropdown section
        v.addWidget(self._section("Active Media Player (Manual Mode)"))

        combo_row = QHBoxLayout()
        combo_row.setSpacing(10)

        self._source_combo = QComboBox()
        self._source_combo.setFont(_font(11))
        self._source_combo.setStyleSheet(f"""
            QComboBox {{
                background: {_INPUT_BG};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 12px;
                min-height: 28px;
            }}
            QComboBox:hover {{
                border-color: {_ACCENT};
            }}
            QComboBox:disabled {{
                background: rgba(20, 20, 35, 0.4);
                color: {_DIM};
                border-color: rgba(255, 255, 255, 0.08);
            }}
            QComboBox QAbstractItemView {{
                background: #181828;
                color: {_TEXT};
                selection-background-color: rgba(138, 92, 246, 0.4);
                selection-color: #ffffff;
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 4px;
            }}
        """)
        self._source_combo.setEnabled(current_mode == "manual")

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setFixedHeight(34)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_INPUT_BG};
                color: {_TEXT};
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px;
                padding: 4px 14px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                border-color: {_ACCENT};
                background: rgba(138, 92, 246, 0.25);
            }}
        """)

        combo_row.addWidget(self._source_combo, 1)
        combo_row.addWidget(refresh_btn)
        v.addLayout(combo_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        self._status_lbl.setWordWrap(True)
        v.addWidget(self._status_lbl)

        def _on_mode_toggled():
            is_manual = self._manual_rb.isChecked()
            mode = "manual" if is_manual else "auto"
            self._cfg.set("media_source_mode", mode)
            self._source_combo.setEnabled(is_manual)
            if is_manual:
                cur_data = self._source_combo.currentData()
                if cur_data:
                    self._cfg.set("selected_media_source", cur_data)
                self._status_lbl.setText("Manual mode: locked to selected player above.")
            else:
                self._status_lbl.setText("Auto mode active: prioritizing Spotify → YouTube → VLC.")
            if self._media:
                self._media.refresh_sessions()

        self._auto_rb.toggled.connect(_on_mode_toggled)
        self._manual_rb.toggled.connect(_on_mode_toggled)

        def _on_combo_changed(idx):
            if idx >= 0 and self._manual_rb.isChecked():
                selected_val = self._source_combo.currentData()
                if selected_val:
                    self._cfg.set("selected_media_source", selected_val)
                    if self._media:
                        self._media.refresh_sessions()

        self._source_combo.currentIndexChanged.connect(_on_combo_changed)

        if self._media:
            refresh_btn.clicked.connect(self._media.refresh_sessions)

        self._populate_sources_ui([])

        v.addStretch()
        return w

    def _populate_sources_ui(self, sessions: list) -> None:
        """Populate the media player dropdown list."""
        self._latest_sessions = sessions or []
        if not hasattr(self, "_source_combo"):
            return

        self._source_combo.blockSignals(True)
        self._source_combo.clear()

        selected_app = (self._cfg.get("selected_media_source", "")).strip().lower()
        active_idx = -1

        for i, s in enumerate(self._latest_sessions):
            app_id = s.get("app_id", "Unknown")
            title = s.get("title", "")
            artist = s.get("artist", "")
            is_playing = s.get("is_playing", False)
            status_symbol = "▶ Playing" if is_playing else "⏸ Paused"

            clean_name = app_id.replace(".exe", "").capitalize()
            if "spotify" in app_id.lower():
                clean_name = "Spotify"
            elif "vlc" in app_id.lower():
                clean_name = "VLC Media Player"
            elif "brave" in app_id.lower():
                clean_name = "Brave"
            elif "chrome" in app_id.lower():
                clean_name = "Google Chrome"
            elif "msedge" in app_id.lower() or "edge" in app_id.lower():
                clean_name = "Microsoft Edge"

            track_info = f" — {artist} - {title}" if (title and title != "Unknown") else ""
            display_text = f"{clean_name}{track_info} [{status_symbol}]"

            self._source_combo.addItem(display_text, app_id)

            if selected_app and (selected_app == app_id.lower() or selected_app in app_id.lower()):
                active_idx = i

        if self._source_combo.count() == 0:
            self._source_combo.addItem("No active media sessions detected", "")
            if hasattr(self, "_status_lbl"):
                self._status_lbl.setText("No media sessions found. Play a track or video in Spotify, YouTube, or VLC.")
        else:
            if active_idx >= 0:
                self._source_combo.setCurrentIndex(active_idx)
            else:
                self._source_combo.setCurrentIndex(0)
                if hasattr(self, "_manual_rb") and self._manual_rb.isChecked():
                    self._cfg.set("selected_media_source", self._source_combo.currentData())

            if hasattr(self, "_status_lbl"):
                if hasattr(self, "_auto_rb") and self._auto_rb.isChecked():
                    self._status_lbl.setText("Auto mode active: prioritizing Spotify → YouTube → VLC.")
                else:
                    self._status_lbl.setText(f"Manual mode locked onto: {self._source_combo.currentText()}")

        self._source_combo.blockSignals(False)

    def _build_captions(self) -> QWidget:
        """Captions & AI tab: auto vs manual speech recognition and lyrics configuration."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(14)

        # ── Caption Source Mode ──
        v.addWidget(self._section("Caption & Lyrics Engine Mode"))

        mode_vbox = QVBoxLayout()
        mode_vbox.setSpacing(8)

        rb_auto = QRadioButton("Auto (Synced Lyrics + AI Speech Recognition Fallback)")
        rb_auto.setFont(_font(11, True))
        rb_auto.setStyleSheet(f"color: {_TEXT};")

        rb_speech = QRadioButton("Force AI Live Captions (Transcribe desktop audio with Whisper)")
        rb_speech.setFont(_font(11, True))
        rb_speech.setStyleSheet(f"color: {_TEXT};")

        rb_lrclib = QRadioButton("Synced Lyrics Only (LrcLib online database only)")
        rb_lrclib.setFont(_font(11, True))
        rb_lrclib.setStyleSheet(f"color: {_TEXT};")

        rb_disabled = QRadioButton("Disabled (Turn off all lyrics and captions)")
        rb_disabled.setFont(_font(11, True))
        rb_disabled.setStyleSheet(f"color: {_TEXT};")

        current_mode = self._cfg.get("captions_mode", "auto")
        if current_mode == "speech_only":
            rb_speech.setChecked(True)
        elif current_mode == "lrclib_only":
            rb_lrclib.setChecked(True)
        elif current_mode == "disabled":
            rb_disabled.setChecked(True)
        else:
            rb_auto.setChecked(True)

        mode_vbox.addWidget(rb_auto)
        mode_vbox.addWidget(rb_speech)
        mode_vbox.addWidget(rb_lrclib)
        mode_vbox.addWidget(rb_disabled)
        v.addLayout(mode_vbox)

        def _on_captions_mode_changed():
            if rb_speech.isChecked():
                m = "speech_only"
            elif rb_lrclib.isChecked():
                m = "lrclib_only"
            elif rb_disabled.isChecked():
                m = "disabled"
            else:
                m = "auto"
            self._cfg.set("captions_mode", m)
            if self._media:
                self._media._update_caption_state()

        rb_auto.toggled.connect(_on_captions_mode_changed)
        rb_speech.toggled.connect(_on_captions_mode_changed)
        rb_lrclib.toggled.connect(_on_captions_mode_changed)
        rb_disabled.toggled.connect(_on_captions_mode_changed)

        # ── Language Detection & Selection ──
        v.addWidget(self._section("Spoken Language Detection"))

        lang_group = QVBoxLayout()
        lang_group.setSpacing(8)

        lang_auto_rb = QRadioButton("Auto-Detect Language (Multilingual, YouTube style)")
        lang_auto_rb.setFont(_font(11, True))
        lang_auto_rb.setStyleSheet(f"color: {_TEXT};")

        lang_manual_rb = QRadioButton("Choose Language Manually:")
        lang_manual_rb.setFont(_font(11, True))
        lang_manual_rb.setStyleSheet(f"color: {_TEXT};")

        current_lang_mode = self._cfg.get("captions_lang_mode", "auto")
        if current_lang_mode == "manual":
            lang_manual_rb.setChecked(True)
        else:
            lang_auto_rb.setChecked(True)

        lang_group.addWidget(lang_auto_rb)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        lang_row.addWidget(lang_manual_rb)

        lang_combo = QComboBox()
        lang_combo.setFont(_font(11))
        lang_options = [
            ("English", "en"),
            ("Arabic (العربية)", "ar"),
            ("Spanish (Español)", "es"),
            ("French (Français)", "fr"),
            ("German (Deutsch)", "de"),
            ("Japanese (日本語)", "ja"),
            ("Korean (한국어)", "ko"),
            ("Chinese (中文)", "zh"),
            ("Italian (Italiano)", "it"),
            ("Russian (Русский)", "ru"),
            ("Portuguese (Português)", "pt"),
            ("Turkish (Türkçe)", "tr"),
            ("Hindi (हिन्दी)", "hi"),
        ]
        for name, code in lang_options:
            lang_combo.addItem(name, code)

        current_lang_code = self._cfg.get("captions_manual_lang", "en")
        for i in range(lang_combo.count()):
            if lang_combo.itemData(i) == current_lang_code:
                lang_combo.setCurrentIndex(i)
                break

        lang_combo.setEnabled(current_lang_mode == "manual")
        lang_row.addWidget(lang_combo)
        lang_row.addStretch()
        lang_group.addLayout(lang_row)
        v.addLayout(lang_group)

        def _on_lang_mode_changed():
            manual = lang_manual_rb.isChecked()
            lang_combo.setEnabled(manual)
            self._cfg.set("captions_lang_mode", "manual" if manual else "auto")

        lang_auto_rb.toggled.connect(_on_lang_mode_changed)
        lang_manual_rb.toggled.connect(_on_lang_mode_changed)
        lang_combo.currentIndexChanged.connect(
            lambda idx: self._cfg.set("captions_manual_lang", lang_combo.itemData(idx))
        )

        # ── AI Model & Badge ──
        v.addWidget(self._section("Whisper AI Model & Display"))

        model_row = QHBoxLayout()
        model_row.setSpacing(12)
        model_lbl = QLabel("Whisper AI Model:")
        model_lbl.setFont(_font(11))
        model_lbl.setStyleSheet(f"color: {_TEXT};")
        model_row.addWidget(model_lbl)

        model_combo = QComboBox()
        model_combo.addItem("Tiny (~75 MB, ultra-fast 300ms — Recommended for live speech)", "tiny")
        model_combo.addItem("Distil-Small English (~330 MB, ultra-fast & high accuracy)", "distil-small.en")
        model_combo.addItem("Base (~140 MB, multilingual)", "base")
        model_combo.addItem("Small (~460 MB, high accuracy multilingual)", "small")

        cur_model = self._cfg.get("captions_whisper_model", "tiny")
        for i in range(model_combo.count()):
            if model_combo.itemData(i) == cur_model:
                model_combo.setCurrentIndex(i)
                break
        model_combo.currentIndexChanged.connect(
            lambda idx: self._cfg.set("captions_whisper_model", model_combo.itemData(idx))
        )
        model_row.addWidget(model_combo)
        model_row.addStretch()
        v.addLayout(model_row)

        badge_cb = QCheckBox("Show Language Tag Badge (e.g. [EN], [AR])")
        badge_cb.setChecked(self._cfg.get("captions_show_badge", True))
        badge_cb.toggled.connect(lambda val: self._cfg.set("captions_show_badge", val))
        v.addWidget(badge_cb)

        v.addStretch()
        return w

    def _build_screens(self) -> QWidget:
        """Screens tab: detect connected monitors and choose which screen(s) GMP appears on."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(14)

        # ── Always on Top Banner if active ──
        if self._cfg.get("always_on_top", False):
            banner = QFrame()
            banner.setStyleSheet(f"""
                QFrame {{
                    background: rgba(138, 92, 246, 0.15);
                    border: 1px solid {_ACCENT};
                    border-radius: 8px;
                    padding: 8px 12px;
                }}
            """)
            b_layout = QVBoxLayout(banner)
            b_layout.setContentsMargins(8, 6, 8, 6)
            b_layout.setSpacing(4)
            b_title = QLabel("📌 Always on Top Active")
            b_title.setFont(_font(12, True))
            b_title.setStyleSheet(f"color: {_ACCENT};")
            b_text = QLabel("The player is currently floating over all applications and can move freely across any connected monitor. Desktop depth effect is temporarily disabled. Disable Always on Top in Appearance to restore per-screen desktop embedding.")
            b_text.setStyleSheet(f"color: {_TEXT}; font-size: 11px;")
            b_text.setWordWrap(True)
            b_layout.addWidget(b_title)
            b_layout.addWidget(b_text)
            v.addWidget(banner)

        # Section header
        v.addWidget(self._section("Connected Displays"))
        desc = QLabel("Select which monitor(s) Glass Media Player will appear on. Each selected screen maintains its own wallpaper depth effect.")
        desc.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        desc.setWordWrap(True)
        v.addWidget(desc)

        # Quick Actions
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        
        btn_all = QPushButton("Select All Screens")
        btn_all.setFont(_font(11))
        btn_primary = QPushButton("Primary Screen Only")
        btn_primary.setFont(_font(11))
        
        actions_row.addWidget(btn_all)
        actions_row.addWidget(btn_primary)
        actions_row.addStretch()
        v.addLayout(actions_row)

        # Scroll area for screen cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        card_container = QWidget()
        card_vbox = QVBoxLayout(card_container)
        card_vbox.setContentsMargins(0, 4, 0, 4)
        card_vbox.setSpacing(10)

        screens = QGuiApplication.screens()
        primary_scr = QGuiApplication.primaryScreen()
        saved_screens = self._cfg.get("enabled_screens", [])
        if not saved_screens and primary_scr:
            saved_screens = [primary_scr.name()]

        checkboxes: list[tuple[str, QCheckBox]] = []

        for i, s in enumerate(screens):
            geo = s.geometry()
            s_name = s.name()
            is_primary = (s == primary_scr)

            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: {_CARD};
                    border: 1px solid {_BORDER};
                    border-radius: 8px;
                    padding: 10px 14px;
                }}
            """)
            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(10, 8, 10, 8)
            c_layout.setSpacing(12)

            info_layout = QVBoxLayout()
            info_layout.setSpacing(3)
            
            title_text = f"🖥️ Screen {i + 1}: {s_name}"
            if is_primary:
                title_text += " (Primary)"
            lbl_title = QLabel(title_text)
            lbl_title.setFont(_font(12, True))
            lbl_title.setStyleSheet(f"color: {_TEXT if not is_primary else _ACCENT};")
            
            lbl_geo = QLabel(f"Resolution: {geo.width()} × {geo.height()}  •  Position: ({geo.x()}, {geo.y()})  •  Scale: {s.devicePixelRatio():.1f}x")
            lbl_geo.setFont(_font(10))
            lbl_geo.setStyleSheet(f"color: {_DIM};")
            
            info_layout.addWidget(lbl_title)
            info_layout.addWidget(lbl_geo)
            c_layout.addLayout(info_layout, 1)

            cb = QCheckBox("Enable")
            cb.setFont(_font(11, True))
            is_checked = (s_name in saved_screens) or (i in saved_screens)
            cb.setChecked(is_checked)
            c_layout.addWidget(cb)

            checkboxes.append((s_name, cb))
            card_vbox.addWidget(card)

        card_vbox.addStretch()
        scroll.setWidget(card_container)
        v.addWidget(scroll, 1)

        def _save_screen_selection():
            selected = [name for name, cb in checkboxes if cb.isChecked()]
            if not selected and primary_scr:
                selected = [primary_scr.name()]
                for name, cb in checkboxes:
                    if name == primary_scr.name():
                        cb.blockSignals(True)
                        cb.setChecked(True)
                        cb.blockSignals(False)
            self._cfg.set("enabled_screens", selected)

        for _, cb in checkboxes:
            cb.toggled.connect(_save_screen_selection)

        def _select_all():
            for _, cb in checkboxes:
                cb.setChecked(True)
            _save_screen_selection()

        def _select_primary():
            for name, cb in checkboxes:
                cb.setChecked(name == (primary_scr.name() if primary_scr else ""))
            _save_screen_selection()

        btn_all.clicked.connect(_select_all)
        btn_primary.clicked.connect(_select_primary)

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
