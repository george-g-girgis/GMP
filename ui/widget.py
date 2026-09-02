"""
ui/widget.py — Glassmorphic player card widget.

The main interactive card that displays track info, album art, playback
controls, and synced lyrics. Reads all visual settings from the
centralised ConfigManager and reacts to live changes.
"""

from __future__ import annotations

import bisect
from PyQt6.QtCore import (
    QEasingCurve,
    QLineF,
    QPoint,
    QRectF,
    QTimer,
    QVariantAnimation,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
    QGraphicsOpacityEffect,
)

import ctypes
from ctypes import wintypes
import logging

log = logging.getLogger(__name__)

dwmapi = ctypes.windll.dwmapi


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("rcDestination", wintypes.RECT),
        ("rcSource", wintypes.RECT),
        ("opacity", ctypes.c_ubyte),
        ("fVisible", wintypes.BOOL),
        ("fSourceClientAreaOnly", wintypes.BOOL),
    ]


# 64-bit ctypes prototypes for DWM functions
dwmapi.DwmRegisterThumbnail.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.POINTER(wintypes.HANDLE),
]
dwmapi.DwmRegisterThumbnail.restype = ctypes.c_long

dwmapi.DwmUpdateThumbnailProperties.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES),
]
dwmapi.DwmUpdateThumbnailProperties.restype = ctypes.c_long

dwmapi.DwmUnregisterThumbnail.argtypes = [wintypes.HANDLE]
dwmapi.DwmUnregisterThumbnail.restype = ctypes.c_long

DWM_TNP_RECTDESTINATION = 0x00000001
DWM_TNP_OPACITY = 0x00000004
DWM_TNP_VISIBLE = 0x00000008
DWM_TNP_SOURCECLIENTAREAONLY = 0x00000010

# ── Curated colour palette (defaults) ────────────────────────────────
_BORDER = QColor(255, 255, 255, 45)
_TEXT = QColor(235, 235, 245)
_TEXT_DIM = QColor(170, 170, 195)
_ACCENT = QColor(138, 92, 246)
_RADIUS = 20

# Windows 11 Segoe Fluent Icons
_ICON_PLAY = "\uE768"
_ICON_PAUSE = "\uE769"
_ICON_PREV = "\uE892"
_ICON_NEXT = "\uE893"
_ICON_SHUFFLE = "\uE8B1"
_ICON_REPEAT = "\uE8EE"
_ICON_REPEAT_ONE = "\uE8ED"


def _time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


# Cache the preferred font family at module load
_FONT_FAMILY: str | None = None

def _font(size: int, bold: bool = False, family: str | None = None) -> QFont:
    global _FONT_FAMILY
    if family is None:
        if _FONT_FAMILY is None:
            _FONT_FAMILY = "Inter" if "Inter" in QFontDatabase.families() else "Segoe UI"
        name = _FONT_FAMILY
    else:
        name = family
    f = QFont(name, size)
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


class _ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._cached_txt = ""
        self._cached_w = 0
        self._cached_elided = ""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(self.palette().windowText().color())
        txt = self.text()
        w = self.width()
        if txt != self._cached_txt or w != self._cached_w:
            self._cached_txt = txt
            self._cached_w = w
            metrics = QFontMetrics(self.font())
            self._cached_elided = metrics.elidedText(txt, Qt.TextElideMode.ElideRight, w)
        p.drawText(self.rect(), self.alignment(), self._cached_elided)
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  Resize Handle
# ─────────────────────────────────────────────────────────────────────
class _ResizeHandle(QLabel):
    """Custom resize grip for child widgets."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._dragging = False
        self._drag_start = None
        
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start = e.globalPosition()
            
    def mouseMoveEvent(self, e):
        if self._dragging and self.parent():
            delta = e.globalPosition() - self._drag_start
            w = max(320, int(self.parent().width() + delta.x()))
            h = max(190, int(self.parent().height() + delta.y()))
            self.parent().resize(w, h)
            self._drag_start = e.globalPosition()
            
    def mouseReleaseEvent(self, e):
        self._dragging = False
        if hasattr(self.parent(), 'resized'):
            self.parent().resized.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 100), 2.0))
        p.drawLine(self.width() - 8, self.height() - 2, self.width() - 2, self.height() - 8)
        p.drawLine(self.width() - 14, self.height() - 2, self.width() - 2, self.height() - 14)
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  Jump Slider
# ─────────────────────────────────────────────────────────────────────
class _JumpSlider(QSlider):
    """A smooth seek slider with drag tracking and real-time scrub events."""

    scrubbing_started = pyqtSignal(float)
    scrubbing_moved = pyqtSignal(float)
    scrubbing_finished = pyqtSignal(float)

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._dragging = False

    def _pos_to_pct(self, pos_x: float) -> float:
        w = max(1, self.width())
        return max(0.0, min(1.0, float(pos_x) / float(w)))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            pct = self._pos_to_pct(e.position().x())
            self.setValue(int(pct * 1000))
            self.scrubbing_started.emit(pct)
            self.scrubbing_moved.emit(pct)
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging:
            pct = self._pos_to_pct(e.position().x())
            self.setValue(int(pct * 1000))
            self.scrubbing_moved.emit(pct)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            pct = self._pos_to_pct(e.position().x())
            self.setValue(int(pct * 1000))
            self.scrubbing_finished.emit(pct)
            e.accept()
        else:
            super().mouseReleaseEvent(e)


# ─────────────────────────────────────────────────────────────────────
#  Icon Button
# ─────────────────────────────────────────────────────────────────────
class _IconButton(QPushButton):
    """A button with smooth hover animation and tactile press feedback."""

    def __init__(self, icon: str, sz: int = 34, font_sz: int = 14, parent=None) -> None:
        super().__init__(parent)
        self._icon = icon
        self._sz = sz
        self._font_sz = font_sz
        self._active = False
        self._pressed = False
        self._hover_alpha = 0.0

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_val)

        self.setFixedSize(sz, sz)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background:transparent;border:none;")

    def _on_anim_val(self, val: object) -> None:
        self._hover_alpha = float(val)
        self.update()

    def set_icon_text(self, txt: str) -> None:
        if self._icon != txt:
            self._icon = txt
            self.update()

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.update()

    def enterEvent(self, e):
        super().enterEvent(e)
        self._anim.stop()
        self._anim.setStartValue(self._hover_alpha)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._pressed = False
        self._anim.stop()
        self._anim.setStartValue(self._hover_alpha)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pressed:
            self._pressed = False
            self.update()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = self.rect()
        rad = max(4.0, self._sz / 3.2)

        if self._pressed:
            p.setBrush(QBrush(QColor(255, 255, 255, 45)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(r).adjusted(1.0, 1.0, -1.0, -1.0), rad, rad)
        elif self._active:
            p.setBrush(QBrush(QColor(138, 92, 246, 60)))
            p.setPen(QPen(QColor(138, 92, 246, 120), 1.2))
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        elif self._hover_alpha > 0.0:
            bg_alpha = int(25 * self._hover_alpha)
            p.setBrush(QBrush(QColor(255, 255, 255, bg_alpha)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(r), rad, rad)

        if self._active:
            p.setPen(_ACCENT)
        elif self._pressed:
            p.setPen(QColor(255, 255, 255))
        elif self._hover_alpha > 0.0:
            tr = int(_TEXT.red() + (255 - _TEXT.red()) * self._hover_alpha)
            tg = int(_TEXT.green() + (255 - _TEXT.green()) * self._hover_alpha)
            tb = int(_TEXT.blue() + (255 - _TEXT.blue()) * self._hover_alpha)
            p.setPen(QColor(tr, tg, tb))
        else:
            p.setPen(_TEXT)

        draw_rect = r.adjusted(0, 1, 0, 1) if self._pressed else r
        p.setFont(QFont("Segoe Fluent Icons", self._font_sz))
        p.drawText(draw_rect, Qt.AlignmentFlag.AlignCenter, self._icon)
        p.end()


# ─────────────────────────────────────────────────────────────────────
#  Player Widget
# ─────────────────────────────────────────────────────────────────────
class PlayerWidget(QWidget):
    """The main Glassmorphic card."""

    dragged = pyqtSignal(int, int)
    resized = pyqtSignal()
    open_settings = pyqtSignal()    # right-click → open Settings

    play_pause = pyqtSignal()
    next_track = pyqtSignal()
    prev_track = pyqtSignal()
    shuffle = pyqtSignal()
    repeat = pyqtSignal()
    seek = pyqtSignal(float)

    def __init__(self, cfg, w: int = 430, h: int = 240, parent=None) -> None:
        super().__init__(parent)
        self._cfg = cfg  # ConfigManager instance
        self.setMinimumSize(370, 240)
        
        self._playing = False
        self._blur_bg: QPixmap | None = None
        self._glow_cache: QPixmap | None = None
        self._drag_pos: QPoint | None = None
        self._raw_art: QPixmap | None = None  # original unscaled album art
        self._lyrics: list[tuple[float, str]] = []
        self._lyrics_times: list[float] = []
        self._is_scrubbing = False
        self._total_duration = 0.0
        self._current_duration = 0.0

        self._seek_cooldown = QTimer(self)
        self._seek_cooldown.setSingleShot(True)
        self._seek_cooldown.setInterval(400)

        self._is_hovered: bool = False

        # Live top-right date and time ticker
        self._datetime_timer = QTimer(self)
        self._datetime_timer.setInterval(1000)
        self._datetime_timer.timeout.connect(self._update_datetime)

        self._build()
        self._apply_cfg()
        self._apply_colors()
        self._cache_paint_params()  # pre-cache paint-time values
        self.resize(w, h)

        # React to live config changes
        self._cfg.changed.connect(self._on_cfg_changed)

    def _on_cfg_changed(self, key: str, value) -> None:
        """React to live config changes from the Settings window."""
        if key == "alpha":
            self._cache_paint_params()
            self.update()
        elif key == "glow" or key == "glow_color":
            self._glow_cache = None
            self._cache_paint_params()
            self.update()
        elif key == "locked":
            self._grip.setVisible(not value)
        elif key == "player_w":
            self.resize(value, self.height())
        elif key == "player_h":
            self.resize(self.width(), value)
        elif key == "lyrics_enabled":
            self._lyrics_on = bool(value)
            if not value:
                self._lyrics_lbl.hide()
            elif self._lyrics:
                self._lyrics_lbl.show()
        elif key in ("lyrics_color", "bg_color"):
            self._apply_colors()
            self._cache_paint_params()
            self.update()

    def _apply_colors(self) -> None:
        """Apply configurable colors to labels."""
        lc = self._cfg.get("lyrics_color", "#aaaac3")
        self._lyrics_lbl.setStyleSheet(f"color:{lc};background:transparent;")

    def _cache_paint_params(self) -> None:
        """Pre-cache frequently-used paint values to avoid per-frame allocations."""
        self._lyrics_on = bool(self._cfg.get("lyrics_enabled", True))
        # glow
        self._p_glow_val = int(self._cfg.get("glow", 140))
        gh = self._cfg.get("glow_color", "#ffffff")
        self._p_glow_c = QColor(gh) if gh else QColor(255, 255, 255)
        # card background
        a = int(self._cfg.get("alpha", 150))
        bh = self._cfg.get("bg_color", "#16162c")
        bg = QColor(bh) if bh else QColor(22, 22, 44)
        self._p_top_c = QColor(bg.red(), bg.green(), bg.blue(), a)
        self._p_bot_c = QColor(max(0, bg.red() - 12), max(0, bg.green() - 12),
                               max(0, bg.blue() - 18), min(255, a + 25))

    def _build(self) -> None:
        self.setMouseTracking(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(25, 25, 25, 25)
        root.setSpacing(0)

        # ── interactive UI container (supports video-mode hover opacity animation) ──
        self._ui_container = QWidget(self)
        self._ui_container.setMouseTracking(True)
        self._ui_opacity = QGraphicsOpacityEffect(self._ui_container)
        self._ui_container.setGraphicsEffect(self._ui_opacity)

        self._ui_anim = QVariantAnimation(self)
        self._ui_anim.setDuration(220)
        self._ui_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._ui_anim.valueChanged.connect(self._ui_opacity.setOpacity)

        ui_layout = QVBoxLayout(self._ui_container)
        ui_layout.setContentsMargins(20, 20, 20, 20)
        ui_layout.setSpacing(12)
        root.addWidget(self._ui_container)

        # ── top row (art + text + top-right date/time) ──
        top = QHBoxLayout()
        top.setSpacing(16)

        self._art = QLabel(self._ui_container)
        self._art.setFixedSize(68, 68)
        self._art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._art.setStyleSheet("border-radius:12px;background:rgba(255,255,255,0.05);")
        self._draw_default_art()
        top.addWidget(self._art)

        txt = QVBoxLayout()
        txt.setSpacing(2)
        txt.setContentsMargins(0, 4, 0, 4)

        self._title = _ElidedLabel("No Media Playing")
        self._title.setFont(_font(14, True))
        self._title.setStyleSheet(f"color:{_TEXT.name()};background:transparent;")
        self._title.setMinimumWidth(130)

        self._artist = _ElidedLabel("Play something to get started")
        self._artist.setFont(_font(11))
        self._artist.setStyleSheet(f"color:{_TEXT_DIM.name()};background:transparent;")

        self._album = _ElidedLabel("")
        self._album.setFont(_font(10))
        self._album.setStyleSheet(
            f"color:rgba({_TEXT_DIM.red()},{_TEXT_DIM.green()},{_TEXT_DIM.blue()},0.7);"
            "background:transparent;"
        )

        self._lyrics_lbl = _ElidedLabel("")
        self._lyrics_lbl.setFont(_font(11, True))
        self._lyrics_lbl.setStyleSheet(f"color:{_TEXT_DIM.name()};background:transparent;")
        self._lyrics_lbl.hide()

        txt.addWidget(self._title)
        txt.addWidget(self._artist)
        txt.addWidget(self._album)
        txt.addWidget(self._lyrics_lbl)
        txt.addStretch()

        top.addLayout(txt)
        top.addStretch()

        # ── top right clock & date ──
        datetime_box = QVBoxLayout()
        datetime_box.setSpacing(1)
        datetime_box.setContentsMargins(0, 2, 2, 0)
        datetime_box.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        self._clock_lbl = QLabel()
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._clock_lbl.setFont(_font(12, True))
        self._clock_lbl.setStyleSheet("color:#ffffff;background:transparent;")

        self._date_lbl = QLabel()
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._date_lbl.setFont(_font(10))
        self._date_lbl.setStyleSheet(f"color:rgba({_TEXT_DIM.red()},{_TEXT_DIM.green()},{_TEXT_DIM.blue()},0.85);background:transparent;")

        datetime_box.addWidget(self._clock_lbl)
        datetime_box.addWidget(self._date_lbl)
        top.addLayout(datetime_box)

        ui_layout.addLayout(top)

        # ── seek bar ──
        self._slider = _JumpSlider(Qt.Orientation.Horizontal, self._ui_container)
        self._slider.setRange(0, 1000)
        self._slider.setFixedHeight(16)
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_slider_style()
        self._slider.scrubbing_started.connect(self._on_scrub_start)
        self._slider.scrubbing_moved.connect(self._on_scrub_move)
        self._slider.scrubbing_finished.connect(self._on_scrub_finish)
        
        self._time = QLabel("0:00 / 0:00", self._ui_container)
        self._time.setFont(_font(10))
        self._time.setStyleSheet(f"color:{_TEXT_DIM.name()};background:transparent;")
        
        prg = QHBoxLayout()
        prg.addWidget(self._slider)
        prg.addWidget(self._time)
        ui_layout.addLayout(prg)

        # ── controls ──
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        ctrl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._btn_shuffle = _IconButton("\uE8B1", 24, 12, self._ui_container)
        self._btn_prev = _IconButton("\uE892", 34, 14, self._ui_container)
        self._btn_play = _IconButton(_ICON_PLAY, 42, 18, self._ui_container)
        self._btn_next = _IconButton("\uE893", 34, 14, self._ui_container)
        self._btn_repeat = _IconButton("\uE8EE", 24, 12, self._ui_container)

        self._btn_next.clicked.connect(self.next_track.emit)
        self._btn_play.clicked.connect(self._on_play_clicked)
        self._btn_prev.clicked.connect(self.prev_track.emit)
        self._btn_shuffle.clicked.connect(self._on_shuffle_clicked)
        self._btn_repeat.clicked.connect(self.repeat.emit)

        ctrl.addWidget(self._btn_shuffle)
        ctrl.addWidget(self._btn_prev)
        ctrl.addWidget(self._btn_play)
        ctrl.addWidget(self._btn_next)
        ctrl.addWidget(self._btn_repeat)
        
        ui_layout.addLayout(ctrl)
        
        self._grip = _ResizeHandle(self)

        # Set initial datetime and start timer
        self._update_datetime()
        self._datetime_timer.start()

        # Enable right-click context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_right_click)

    def _on_right_click(self, pos: QPoint) -> None:
        """Right-click on player → emit open_settings signal."""
        self.open_settings.emit()

    def _apply_slider_style(self) -> None:
        ac = _ACCENT.name()
        self._slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: rgba(255, 255, 255, 30);
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 14px; height: 14px; margin: -5px 0;
                background: #ffffff;
                border-radius: 7px;
                border: 2px solid {ac};
            }}
            QSlider::handle:horizontal:hover {{
                background: #f0f0f0;
                border: 3px solid #b57bff;
            }}
            QSlider::sub-page:horizontal {{
                background: qlineargradient(
                    x1:0,y1:0,x2:1,y2:0,
                    stop:0 {ac}, stop:1 #b57bff
                );
                border-radius: 2px;
            }}
        """)

    def _draw_default_art(self) -> None:
        sz = self._art.size()
        side = max(sz.width(), sz.height(), 1)
        px = QPixmap(side, side)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(_TEXT_DIM))
        font_sz = max(12, side // 3)
        p.setFont(QFont("Segoe Fluent Icons", font_sz))
        p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "\uE8D6")
        p.end()
        self._art.setPixmap(px)

    def _set_art(self, px: QPixmap) -> None:
        """Scale album art to fit the square container (no cropping)."""
        self._raw_art = px  # cache for re-apply on resize
        self._apply_art_to_label(px)

    def _apply_art_to_label(self, px: QPixmap) -> None:
        """Render a pixmap into the art label at the label's current size."""
        sz = self._art.size()
        if sz.width() < 1 or sz.height() < 1:
            return
        canvas = QPixmap(sz)
        canvas.fill(Qt.GlobalColor.transparent)
        scaled = px.scaled(
            sz,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        p = QPainter(canvas)
        x = (sz.width() - scaled.width()) // 2
        y = (sz.height() - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)
        p.end()
        self._art.setPixmap(canvas)

    def _apply_cfg(self):
        locked = self._cfg.get("locked", False)
        self._grip.setVisible(not locked)

    def _update_datetime(self) -> None:
        """Update top-right time and date according to the user's exact specification."""
        from datetime import datetime
        now = datetime.now()
        ampm = "P.M." if now.hour >= 12 else "A.M."
        h12 = now.hour % 12
        if h12 == 0:
            h12 = 12
        time_str = f"{h12:02d}:{now.minute:02d} {ampm}"
        month_str = now.strftime("%b")
        year_str = now.strftime("%y")
        date_str = f"{now.day}-{month_str}-{year_str}"

        if hasattr(self, "_clock_lbl") and self._clock_lbl.text() != time_str:
            self._clock_lbl.setText(time_str)
        if hasattr(self, "_date_lbl") and self._date_lbl.text() != date_str:
            self._date_lbl.setText(date_str)

    # ── window & mouse events ────────────────────────────────────────
    def enterEvent(self, e):
        super().enterEvent(e)
        self._is_hovered = True
        if self._is_video_mode:
            self._animate_ui_opacity(1.0)
            if self._playing:
                self._hover_timer.start(3000)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._is_hovered = False
        if self._is_video_mode and self._playing and not self._is_scrubbing:
            self._animate_ui_opacity(0.0)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._cfg["locked"]:
            self._drag_pos = e.globalPosition()

    def mouseMoveEvent(self, e):
        if self._drag_pos and not self._cfg["locked"]:
            delta = e.globalPosition() - self._drag_pos
            self.dragged.emit(int(delta.x()), int(delta.y()))
            self._drag_pos = e.globalPosition()
            if self._is_video_mode:
                self._update_thumbnail_rect()

        if self._is_video_mode:
            if self._ui_opacity.opacity() < 0.95:
                self._animate_ui_opacity(1.0)
            if self._playing:
                self._hover_timer.start(3000)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = None

    def moveEvent(self, e):
        super().moveEvent(e)
        self.update()

    def _animate_ui_opacity(self, target: float) -> None:
        if hasattr(self, "_ui_anim"):
            if self._ui_anim.state() == QVariantAnimation.State.Running:
                self._ui_anim.stop()
            self._ui_anim.setStartValue(self._ui_opacity.opacity())
            self._ui_anim.setEndValue(target)
            self._ui_anim.start()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if hasattr(self, '_grip'):
            self._grip.raise_()
            self._grip.move(self.width() - self._grip.width(), self.height() - self._grip.height())
            
        # Only invalidate glow cache if size actually changed
        old_size = getattr(self, '_last_size', None)
        new_size = self.size()
        if old_size != new_size:
            self._last_size = new_size
            self._glow_cache = None
            
        s = max(1.0, (self.height() - 50) / 190.0)
        self._title.setFont(_font(int(14 * s), True))
        self._artist.setFont(_font(int(11 * s)))
        self._album.setFont(_font(int(10 * s)))
        self._lyrics_lbl.setFont(_font(int(11 * s), True))
        self._time.setFont(_font(int(10 * s)))

        if hasattr(self, "_clock_lbl"):
            self._clock_lbl.setFont(_font(int(12 * s), True))
        if hasattr(self, "_date_lbl"):
            self._date_lbl.setFont(_font(int(10 * s)))

        new_art_sz = int(68 * s)
        old_art_sz = self._art.width()
        self._art.setFixedSize(new_art_sz, new_art_sz)
        # Re-apply album art at new size
        if new_art_sz != old_art_sz:
            if self._raw_art and not self._raw_art.isNull():
                self._apply_art_to_label(self._raw_art)
            else:
                self._draw_default_art()
        
        for btn, sz, fsz in [
            (self._btn_shuffle, 24, 12),
            (self._btn_prev, 34, 14),
            (self._btn_play, 42, 18),
            (self._btn_next, 34, 14),
            (self._btn_repeat, 24, 12)
        ]:
            new_sz = int(sz * s)
            btn.setFixedSize(new_sz, new_sz)
            btn._sz = new_sz
            btn._font_sz = int(fsz * s)
            
        self.resized.emit()

    # ── api ──────────────────────────────────────────────────────────
    def set_blur_bg(self, px: QPixmap) -> None:
        self._blur_bg = px
        self.update()

    def set_track(self, info: dict) -> None:
        self._title.setText(info.get("title", "Unknown"))
        self._artist.setText(info.get("artist", "Unknown Artist"))
        self._album.setText(info.get("album", ""))

        art: QPixmap | None = info.get("art")
        if art and not art.isNull():
            self._set_art(art)
        else:
            self._draw_default_art()
            
        self.set_shuffle(info.get("shuffle", False))
        self.set_repeat(info.get("repeat", 0))

    def set_caption(self, text: str, lang: str = "AUTO") -> None:
        """Display live speech-to-text auto-generated captions."""
        if not getattr(self, "_lyrics_on", True):
            return
        mode = self._cfg.get("captions_mode", "auto")
        if mode == "disabled":
            return
        if self._lyrics and mode != "speech_only":
            # Synced lyrics from LrcLib take precedence unless forced
            return
        if text:
            show_badge = self._cfg.get("captions_show_badge", True)
            badge = f"[{lang}] " if (show_badge and lang and lang != "AUTO") else ""
            self._lyrics_lbl.show()
            self._lyrics_lbl.setText(f"{badge}{text}")

    def set_shuffle(self, active: bool) -> None:
        self._btn_shuffle.set_active(active)

    def set_repeat(self, rep: int) -> None:
        self._btn_repeat.set_active(rep in (1, 2))
        self._btn_repeat.set_icon_text(_ICON_REPEAT_ONE if rep == 1 else _ICON_REPEAT)

    def set_idle(self) -> None:
        self._title.setText("No Media Playing")
        self._artist.setText("Play something to get started")
        self._album.setText("")
        self._raw_art = None
        self._draw_default_art()
        self.set_playing(False)
        self.set_position(0.0, 0.0)
        self.set_lyrics([])
        self._disable_video_mode()
        
    def set_auth_failed(self) -> None:
        self._title.setText("Spotify API Keys Missing")
        self._artist.setText("Check .env.template file")
        self._album.setText("")
        self._raw_art = None
        self._draw_default_art()
        self.set_playing(False)
        self.set_position(0.0, 0.0)
        self.set_lyrics([])
        self._disable_video_mode()

    def set_playing(self, playing: bool) -> None:
        playing = bool(playing)
        if self._playing != playing:
            self._playing = playing
            self._btn_play.set_icon_text(_ICON_PAUSE if playing else _ICON_PLAY)

    def _on_play_clicked(self) -> None:
        """Optimistic instant feedback when play/pause is clicked."""
        self.set_playing(not self._playing)
        self.play_pause.emit()

    def _on_shuffle_clicked(self) -> None:
        """Optimistic instant feedback when shuffle is clicked."""
        self._btn_shuffle.set_active(not self._btn_shuffle._active)
        self.shuffle.emit()

    def _on_scrub_start(self, pct: float) -> None:
        self._is_scrubbing = True
        if self._total_duration > 0:
            cur = pct * self._total_duration
            self._time.setText(f"{_time(cur)} / {_time(self._total_duration)}")
            self._sync_lyrics(cur)

    def _on_scrub_move(self, pct: float) -> None:
        if self._total_duration > 0:
            cur = pct * self._total_duration
            self._time.setText(f"{_time(cur)} / {_time(self._total_duration)}")
            self._sync_lyrics(cur)

    def _on_scrub_finish(self, pct: float) -> None:
        self._is_scrubbing = False
        self._seek_cooldown.start()
        self.seek.emit(pct)

    def set_position(self, cur: float, total: float) -> None:
        self._current_duration = cur
        self._total_duration = total
        if total > 0:
            if not self._is_scrubbing and not self._seek_cooldown.isActive():
                self._slider.blockSignals(True)
                self._slider.setValue(int((cur / total) * 1000))
                self._slider.blockSignals(False)
                new_time = f"{_time(cur)} / {_time(total)}"
                if self._time.text() != new_time:
                    self._time.setText(new_time)
                self._sync_lyrics(cur)

    def set_lyrics(self, lyrics: list[tuple[float, str]]) -> None:
        self._lyrics = lyrics
        self._lyrics_times = [t for t, _ in lyrics] if lyrics else []
        lyrics_enabled = self._cfg.get("lyrics_enabled", True)
        if lyrics and lyrics_enabled:
            self._lyrics_lbl.show()
            self._lyrics_lbl.setText("Lyrics synced!")
        elif not lyrics_enabled:
            self._lyrics_lbl.hide()
        else:
            self._lyrics_lbl.show()
            self._lyrics_lbl.setText("No synced lyrics found.")

    def _sync_lyrics(self, cur: float) -> None:
        if not self._lyrics or not getattr(self, "_lyrics_on", True):
            return
        idx = bisect.bisect_right(self._lyrics_times, cur) - 1
        if idx >= 0:
            line_text = self._lyrics[idx][1]
            if self._lyrics_lbl.text() != line_text:
                self._lyrics_lbl.setText(line_text)

    # ── painting ─────────────────────────────────────────────────────
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = 25
        r = self.rect().adjusted(margin, margin, -margin, -margin)
        rf = QRectF(r)
        rad = float(_RADIUS)

        # ── glow (cached color) ──
        glow_val = self._p_glow_val
        if glow_val > 0:
            if self._glow_cache is None or self._glow_cache.size() != self.size():
                gc = self._p_glow_c
                self._glow_cache = QPixmap(self.size())
                self._glow_cache.fill(Qt.GlobalColor.transparent)
                gp = QPainter(self._glow_cache)
                gp.setRenderHint(QPainter.RenderHint.Antialiasing)
                gp.setBrush(Qt.BrushStyle.NoBrush)
                grf = QRectF(self._glow_cache.rect()).adjusted(margin, margin, -margin, -margin)
                for i in range(1, 15):
                    alpha = int(glow_val * (1.0 - (i / 15.0)) ** 2)
                    gp.setPen(QPen(QColor(gc.red(), gc.green(), gc.blue(), alpha), 2.0))
                    gp.drawRoundedRect(grf.adjusted(-float(i), -float(i), float(i), float(i)), rad + float(i)/2.0, rad + float(i)/2.0)
                gp.end()
            p.drawPixmap(0, 0, self._glow_cache)

        # ── blur background ──
        if self._blur_bg and not self._blur_bg.isNull():
            path = QPainterPath()
            path.addRoundedRect(rf, rad, rad)
            p.setClipPath(path)
            p.drawPixmap(self.rect(), self._blur_bg)
            p.setClipping(False)

        # ── card background (cached gradient) ──
        g = QLinearGradient(0.0, 0.0, 0.0, float(r.height()))
        g.setColorAt(0.0, self._p_top_c)
        g.setColorAt(1.0, self._p_bot_c)
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rf, rad, rad)

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_BORDER, 1.2))
        p.drawRoundedRect(rf.adjusted(1.0, 1.0, -1.0, -1.0), rad - 1.0, rad - 1.0)

        p.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        p.drawLine(QLineF(float(r.x()) + rad, float(r.y()) + 1.0, float(r.right()) - rad, float(r.y()) + 1.0))
        
        p.end()
