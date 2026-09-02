"""
ui/setup.py — First-run setup wizard for GMP.

A dark-themed, 4-step stacked dialog that guides the user through
initial configuration on first launch. Includes an AI model download
step with progress feedback. Sets ``first_run`` to False when complete
so it never appears again.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root (GMP) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ConfigManager, VERSION

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect, QThread, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# ── Palette (matches settings.py) ───────────────────────────────────
_BG = "#0e0e1a"
_CARD = "#161630"
_TEXT = "#ebebf5"
_DIM = "#aaaac3"
_ACCENT = "#8a5cf6"
_ACCENT_HOVER = "#a07bff"
_INPUT_BG = "#1e1e38"


def _font(size: int = 13, bold: bool = False) -> QFont:
    name = "Inter" if "Inter" in QFontDatabase.families() else "Segoe UI"
    f = QFont(name, size)
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


_SETUP_CSS = f"""
QDialog {{
    background: {_BG};
    color: {_TEXT};
}}
QLabel {{
    background: transparent;
    color: {_TEXT};
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
}}
QComboBox QAbstractItemView {{
    background: {_INPUT_BG};
    color: {_TEXT};
    border: 1px solid rgba(255,255,255,0.15);
    selection-background-color: {_ACCENT};
}}
"""


# ─────────────────────────────────────────────────────────────────────
#  Step indicator (dot navigation)
# ─────────────────────────────────────────────────────────────────────
class _StepDots(QWidget):
    """Three dots indicating the current wizard step."""

    def __init__(self, total: int = 3, parent=None):
        super().__init__(parent)
        self._total = total
        self._current = 0
        self.setFixedHeight(24)

    def set_step(self, idx: int) -> None:
        self._current = idx
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dot_r = 5
        gap = 16
        total_w = self._total * (dot_r * 2) + (self._total - 1) * gap
        x0 = (self.width() - total_w) / 2
        cy = self.height() / 2

        for i in range(self._total):
            cx = x0 + i * (dot_r * 2 + gap) + dot_r
            if i == self._current:
                p.setBrush(QBrush(QColor(_ACCENT)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(int(cx - dot_r - 1), int(cy - dot_r - 1),
                              (dot_r + 1) * 2, (dot_r + 1) * 2)
            else:
                p.setBrush(QBrush(QColor(255, 255, 255, 60)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(int(cx - dot_r), int(cy - dot_r),
                              dot_r * 2, dot_r * 2)
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  Welcome illustration (programmatic)
# ─────────────────────────────────────────────────────────────────────
class _WelcomeArt(QWidget):
    """A simple programmatic illustration for the welcome step."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 140)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Glassmorphic card outline
        card = QRect(30, 20, 140, 100)
        gradient = QLinearGradient(30, 20, 30, 120)
        gradient.setColorAt(0.0, QColor(22, 22, 44, 120))
        gradient.setColorAt(1.0, QColor(10, 10, 26, 160))

        path = QPainterPath()
        path.addRoundedRect(float(card.x()), float(card.y()),
                            float(card.width()), float(card.height()), 16, 16)
        p.fillPath(path, QBrush(gradient))
        p.setPen(QPen(QColor(255, 255, 255, 50), 1.5))
        p.drawRoundedRect(card, 16, 16)

        # Glow rings
        for i in range(1, 8):
            alpha = int(80 * (1.0 - i / 8.0) ** 2)
            p.setPen(QPen(QColor(138, 92, 246, alpha), 1.5))
            p.drawRoundedRect(
                card.adjusted(-i * 2, -i * 2, i * 2, i * 2),
                16 + i, 16 + i,
            )

        # Application icon or music note
        ico_file = Path(__file__).resolve().parent.parent / "assets" / "icon.png"
        if not ico_file.exists():
            ico_file = Path(__file__).resolve().parent.parent / "assets" / "app.ico"

        if ico_file.exists():
            pix = QPixmap(str(ico_file)).scaled(
                card.width() - 20, card.height() - 20,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(
                card.x() + (card.width() - pix.width()) // 2,
                card.y() + (card.height() - pix.height()) // 2,
                pix,
            )
        else:
            p.setPen(QPen(QColor(138, 92, 246)))
            p.setFont(QFont("Segoe UI Emoji", 28))
            p.drawText(card, Qt.AlignmentFlag.AlignCenter, "♫")

        p.end()


# ─────────────────────────────────────────────────────────────────────
#  Primary button
# ─────────────────────────────────────────────────────────────────────
def _accent_btn(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setFont(_font(13, True))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(42)
    btn.setMinimumWidth(160)
    btn.setStyleSheet(f"""
        QPushButton {{
            background: {_ACCENT};
            color: #fff;
            border: none;
            border-radius: 10px;
            padding: 0 28px;
            font-size: 14px;
        }}
        QPushButton:hover {{
            background: {_ACCENT_HOVER};
        }}
        QPushButton:pressed {{
            background: #7244d4;
        }}
    """)
    return btn


# ─────────────────────────────────────────────────────────────────────
#  Background model downloader
# ─────────────────────────────────────────────────────────────────────
class _ModelDownloadWorker(QObject):
    """Downloads the rembg depth model and the Whisper Base captions model in a background thread."""

    status = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, depth_model: str = "u2net", captions_model: str = "base", download_depth: bool = True) -> None:
        super().__init__()
        self._depth_model = depth_model
        self._captions_model = captions_model
        self._download_depth = download_depth

    def run(self) -> None:
        try:
            # 1. Depth Effect Model
            if self._download_depth:
                self.status.emit(f"Loading Depth Model ({self._depth_model}, ~170 MB)…")
                from rembg import new_session
                new_session(model_name=self._depth_model)

            # 2. Captions AI Model (Base)
            self.status.emit(f"Loading Whisper AI Captions Model ({self._captions_model}, ~140 MB)…")
            from faster_whisper import WhisperModel
            WhisperModel(self._captions_model, device="cpu", compute_type="int8")

            self.status.emit("AI Models ready!")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))




# ─────────────────────────────────────────────────────────────────────
#  Setup Wizard
# ─────────────────────────────────────────────────────────────────────
class SetupWizard(QDialog):
    """4-step first-run configuration wizard."""

    def __init__(self, cfg: ConfigManager, parent=None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._model_ready = False
        self._download_thread: QThread | None = None
        self.setWindowTitle("GMP — Setup")
        ico_file = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
        if ico_file.exists():
            self.setWindowIcon(QIcon(str(ico_file)))
        self.setFixedSize(520, 500)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(_SETUP_CSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.addWidget(self._page_welcome())
        self._stack.addWidget(self._page_customize())
        self._stack.addWidget(self._page_model_download())
        self._stack.addWidget(self._page_startup())
        root.addWidget(self._stack, 1)

        # Bottom bar (dots + button)
        bottom = QVBoxLayout()
        bottom.setContentsMargins(24, 0, 24, 20)
        bottom.setSpacing(12)

        self._dots = _StepDots(4)
        bottom.addWidget(self._dots, alignment=Qt.AlignmentFlag.AlignCenter)

        root.addLayout(bottom)

    def _go(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        self._dots.set_step(idx)

    # ── Page 1: Welcome ──────────────────────────────────────────────

    def _page_welcome(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 40, 40, 20)
        v.setSpacing(12)

        v.addStretch()

        art = _WelcomeArt()
        v.addWidget(art, alignment=Qt.AlignmentFlag.AlignCenter)

        v.addSpacing(12)

        title = QLabel("Welcome to GMP")
        title.setFont(_font(22, True))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        sub = QLabel(
            "Glass Media Player — a depth-layered desktop music overlay.\n"
            "Let's set things up in a few quick steps."
        )
        sub.setFont(_font(12))
        sub.setStyleSheet(f"color: {_DIM};")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        v.addWidget(sub)

        v.addSpacing(16)

        btn = _accent_btn("Get Started →")
        btn.clicked.connect(lambda: self._go(1))
        v.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        v.addStretch()
        return w

    # ── Page 2: Customize ────────────────────────────────────────────

    def _page_customize(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 30, 40, 20)
        v.setSpacing(14)

        title = QLabel("Customize Your Player")
        title.setFont(_font(18, True))
        v.addWidget(title)

        desc = QLabel("Adjust these settings to match your style. You can always change them later.")
        desc.setFont(_font(11))
        desc.setStyleSheet(f"color: {_DIM};")
        desc.setWordWrap(True)
        v.addWidget(desc)

        v.addSpacing(8)

        # Opacity
        v.addLayout(self._wiz_slider("Card Opacity", "alpha", 0, 255, self._cfg["alpha"]))

        # Glow
        v.addLayout(self._wiz_slider("Glow Intensity", "glow", 0, 255, self._cfg["glow"]))

        # Depth effect
        depth_cb = QCheckBox("Enable Depth Effect (AI foreground extraction)")
        depth_cb.setChecked(self._cfg["depth_enabled"])
        depth_cb.toggled.connect(lambda v: self._cfg.set("depth_enabled", v, save=False))
        v.addWidget(depth_cb)

        # Depth Model
        model_row = QHBoxLayout()
        model_lbl = QLabel("Depth Model")
        model_lbl.setFixedWidth(110)
        model_combo = QComboBox()
        models = ["u2net", "u2netp", "isnet-general-use"]
        model_combo.addItems(models)
        cur = self._cfg["model"]
        if cur in models:
            model_combo.setCurrentIndex(models.index(cur))
        model_combo.currentTextChanged.connect(
            lambda m: self._cfg.set("model", m, save=False)
        )
        model_row.addWidget(model_lbl)
        model_row.addWidget(model_combo)
        model_row.addStretch()
        v.addLayout(model_row)

        # Captions AI Model
        whisper_row = QHBoxLayout()
        whisper_lbl = QLabel("Captions Model")
        whisper_lbl.setFixedWidth(110)
        whisper_combo = QComboBox()
        whisper_combo.addItem("Base (~140 MB, recommended)", "base")
        whisper_combo.addItem("Small (~460 MB, higher accuracy)", "small")
        cur_w = self._cfg.get("captions_whisper_model", "base")
        if cur_w == "small":
            whisper_combo.setCurrentIndex(1)
        else:
            whisper_combo.setCurrentIndex(0)
            self._cfg.set("captions_whisper_model", "base", save=False)
        whisper_combo.currentIndexChanged.connect(
            lambda idx: self._cfg.set("captions_whisper_model", whisper_combo.itemData(idx), save=False)
        )
        whisper_row.addWidget(whisper_lbl)
        whisper_row.addWidget(whisper_combo)
        whisper_row.addStretch()
        v.addLayout(whisper_row)

        v.addStretch()

        # Nav buttons
        nav = QHBoxLayout()
        back = QPushButton("← Back")
        back.setStyleSheet(f"QPushButton {{ background: transparent; color: {_DIM}; border: none; }}"
                           f"QPushButton:hover {{ color: {_TEXT}; }}")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self._go(0))
        nav.addWidget(back)
        nav.addStretch()
        nxt = _accent_btn("Next →")
        nxt.clicked.connect(self._start_model_download)
        nav.addWidget(nxt)
        v.addLayout(nav)

        return w

    # ── Page 3: Model Download ───────────────────────────────────────

    def _page_model_download(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 40, 40, 20)
        v.setSpacing(16)

        v.addStretch()

        self._dl_title = QLabel("Preparing AI Models…")
        self._dl_title.setFont(_font(18, True))
        self._dl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._dl_title)

        self._dl_desc = QLabel(
            "Downloading the AI models for Depth Effect and Live Captions (Base).\n"
            "This only happens once on first launch. Please wait…"
        )
        self._dl_desc.setFont(_font(11))
        self._dl_desc.setStyleSheet(f"color: {_DIM};")
        self._dl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dl_desc.setWordWrap(True)
        v.addWidget(self._dl_desc)

        v.addSpacing(16)

        self._dl_progress = QProgressBar()
        self._dl_progress.setRange(0, 0)  # indeterminate
        self._dl_progress.setFixedHeight(8)
        self._dl_progress.setTextVisible(False)
        self._dl_progress.setStyleSheet(f"""
            QProgressBar {{
                background: rgba(255,255,255,0.08);
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: {_ACCENT};
                border-radius: 4px;
            }}
        """)
        v.addWidget(self._dl_progress)

        self._dl_status = QLabel("Initializing…")
        self._dl_status.setFont(_font(10))
        self._dl_status.setStyleSheet(f"color: {_DIM};")
        self._dl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._dl_status)

        v.addStretch()

        # Hidden "Continue" button (shown when download finishes)
        self._dl_continue = _accent_btn("Continue →")
        self._dl_continue.clicked.connect(lambda: self._go(3))
        self._dl_continue.hide()
        v.addWidget(self._dl_continue, alignment=Qt.AlignmentFlag.AlignCenter)

        v.addStretch()
        return w

    def _start_model_download(self) -> None:
        """Navigate to download page and start both depth and captions model downloads."""
        self._go(2)

        download_depth = self._cfg.get("depth_enabled", True)
        depth_model = self._cfg.get("model", "u2net")
        captions_model = self._cfg.get("captions_whisper_model", "base")
        if captions_model == "tiny":
            captions_model = "base"
            self._cfg.set("captions_whisper_model", "base")

        self._dl_status.setText("Initializing AI models…")

        self._download_thread = QThread()
        self._dl_worker = _ModelDownloadWorker(
            depth_model=depth_model,
            captions_model=captions_model,
            download_depth=download_depth,
        )
        self._dl_worker.moveToThread(self._download_thread)
        self._download_thread.started.connect(self._dl_worker.run)
        self._dl_worker.status.connect(self._on_dl_status)
        self._dl_worker.finished.connect(self._on_dl_finished)
        self._dl_worker.error.connect(self._on_dl_error)
        self._download_thread.start()

    def _on_dl_status(self, text: str) -> None:
        self._dl_status.setText(text)

    def _on_dl_finished(self) -> None:
        self._model_ready = True
        self._dl_title.setText("AI Models Ready ✓")
        self._dl_desc.setText("Depth effect and Whisper AI live captions (Base) are downloaded and ready to use.")
        self._dl_progress.setRange(0, 100)
        self._dl_progress.setValue(100)
        self._dl_status.setText("Complete!")
        self._dl_continue.show()
        # Clean up thread
        if self._download_thread:
            self._download_thread.quit()
            self._download_thread.wait(2000)

    def _on_dl_error(self, msg: str) -> None:
        self._model_ready = True  # allow continuing anyway
        self._dl_title.setText("Download Notice")
        self._dl_desc.setText(
            f"Note: {msg[:100]}\n\n"
            "You can still continue setup — any missing models will download\n"
            "automatically in the background when first required."
        )
        self._dl_progress.hide()
        self._dl_status.setText("Ready to proceed.")
        self._dl_continue.show()
        if self._download_thread:
            self._download_thread.quit()
            self._download_thread.wait(2000)

    # ── Page 4: Startup ──────────────────────────────────────────────

    def _page_startup(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 30, 40, 20)
        v.setSpacing(14)

        title = QLabel("Startup & Final Touches")
        title.setFont(_font(18, True))
        v.addWidget(title)

        v.addSpacing(8)

        auto_cb = QCheckBox("Run GMP at Windows Login")
        auto_cb.setChecked(False)
        auto_cb.toggled.connect(lambda v: self._cfg.set("autostart", v, save=False))
        v.addWidget(auto_cb)

        hint = QLabel("GMP will start silently in the system tray when you log in.")
        hint.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        v.addSpacing(12)

        tip_title = QLabel("💡 Quick Tips")
        tip_title.setFont(_font(13, True))
        v.addWidget(tip_title)

        tips = QLabel(
            "• Drag the player card anywhere on your desktop\n"
            "• Right-click the tray icon for quick actions\n"
            "• Open Settings anytime from the tray menu\n"
            "• Lock the layout when you've found the perfect spot"
        )
        tips.setStyleSheet(f"color: {_DIM}; font-size: 12px; line-height: 1.6;")
        tips.setWordWrap(True)
        v.addWidget(tips)

        v.addStretch()

        # Nav buttons
        nav = QHBoxLayout()
        back = QPushButton("← Back")
        back.setStyleSheet(f"QPushButton {{ background: transparent; color: {_DIM}; border: none; }}"
                           f"QPushButton:hover {{ color: {_TEXT}; }}")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self._go(2))
        nav.addWidget(back)
        nav.addStretch()
        finish = _accent_btn("Finish Setup ✓")
        finish.clicked.connect(self._finish)
        nav.addWidget(finish)
        v.addLayout(nav)

        return w

    # ── Helpers ───────────────────────────────────────────────────────

    def _wiz_slider(
        self, label: str, key: str,
        lo: int, hi: int, val: int,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        lbl = QLabel(label)
        lbl.setFixedWidth(120)
        row.addWidget(lbl)

        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(val)
        sl.setFixedHeight(20)
        row.addWidget(sl, 1)

        val_lbl = QLabel(str(val))
        val_lbl.setFixedWidth(36)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setStyleSheet(f"color: {_DIM}; font-size: 12px;")
        row.addWidget(val_lbl)

        def _on_change(v: int):
            val_lbl.setText(str(v))
            self._cfg.set(key, v, save=False)

        sl.valueChanged.connect(_on_change)
        return row

    def _finish(self) -> None:
        """Complete setup: save config, install system entries, enable autostart if selected, close."""
        self._cfg["first_run"] = False
        self._cfg.save_now()

        # Install Start Menu shortcut and Windows Control Panel registration
        try:
            from core.installer import install_system_entries
            install_system_entries()
        except Exception as exc:
            log.warning("Could not register system entries: %s", exc)

        if self._cfg["autostart"]:
            from core.autostart import enable
            enable()

        self.accept()
