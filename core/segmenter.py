"""
core/segmenter.py — Fast foreground cutout generator using rembg.

Runs AI segmentation (U²-Net via ONNX Runtime) in a background QThread
to avoid blocking the UI. Results are cached to ``.cache/`` keyed by a
hash of the source wallpaper so unchanged wallpapers are never
re-processed.

The output is a full-resolution RGBA QPixmap whose alpha channel
isolates the foreground subject. This pixmap is later composited as the
top layer by the overlay window.
"""

from __future__ import annotations

import hashlib
import logging
import traceback
from io import BytesIO
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

log = logging.getLogger(__name__)

# ── Default cache location (project-local) ──────────────────────────
DEFAULT_CACHE = Path(__file__).resolve().parent.parent / ".cache"


# ─────────────────────────────────────────────────────────────────────
#  Background worker (runs inside QThread)
# ─────────────────────────────────────────────────────────────────────
class _Worker(QObject):
    """Performs the heavy rembg computation off the main thread."""

    done = pyqtSignal(QPixmap, str)    # (mask_pixmap, wallpaper_path)
    failed = pyqtSignal(str, str)      # (error_msg, wallpaper_path)
    status = pyqtSignal(str)           # human-readable progress text

    def __init__(
        self, wallpaper_path: str, cache_dir: Path, model: str,
        session=None,
    ) -> None:
        super().__init__()
        self.wallpaper_path = wallpaper_path
        self.cache_dir = cache_dir
        self.model = model
        self._session = session

    # ── entry point (called when thread starts) ─────────────────────

    def run(self) -> None:
        wp = self.wallpaper_path
        try:
            cache_file = self._cache_path()

            # 1. Try cache first
            if cache_file.exists():
                self.status.emit("Loading cached cutout…")
                px = QPixmap(str(cache_file))
                if not px.isNull():
                    log.info("Cache hit for %s", wp)
                    self.done.emit(px, wp)
                    return
                log.warning("Cached file corrupt, re-segmenting")

            # 2. Lazy-import rembg (heavy; deferred so startup is fast)
            self.status.emit("Loading AI model…")
            from rembg import new_session, remove  # noqa: F811

            session = self._session
            if session is None:
                session = new_session(self.model)

            # 3. Run segmentation
            self.status.emit("Segmenting foreground…")
            src = Image.open(wp).convert("RGB")
            mask: Image.Image = remove(
                src,
                session=session,
                post_process_mask=True,
            )

            # 4. Write to cache
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            mask.save(str(cache_file), "PNG")
            log.info("Segmentation cached → %s", cache_file)

            # 5. Convert to QPixmap
            self.status.emit("Preparing overlay…")
            px = self._pil_to_pixmap(mask)

            # 6. Free image memory (keep session for reuse)
            del src
            del mask
            import gc
            gc.collect()

            self.done.emit(px, wp)

        except Exception as exc:
            tb = traceback.format_exc()
            log.error("Segmentation failed: %s\n%s", exc, tb)
            self.failed.emit(str(exc), wp)

    # ── helpers ──────────────────────────────────────────────────────

    def _cache_path(self) -> Path:
        h = self._file_hash(self.wallpaper_path)
        return self.cache_dir / f"{h}_{self.model}.png"

    @staticmethod
    def _file_hash(path: str) -> str:
        """Fast pseudo-hash: first 64 KiB + file size."""
        md5 = hashlib.md5()
        with open(path, "rb") as f:
            md5.update(f.read(65_536))
            f.seek(0, 2)
            md5.update(str(f.tell()).encode())
        return md5.hexdigest()[:16]

    @staticmethod
    def _pil_to_pixmap(img: Image.Image) -> QPixmap:
        img = img.convert("RGBA")
        raw = img.tobytes("raw", "RGBA")
        qi = QImage(raw, img.width, img.height,
                    img.width * 4, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qi.copy())   # .copy() owns the buffer


# ─────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────
class Segmenter(QObject):
    """
    High-level foreground segmentation manager.

    Usage::

        seg = Segmenter()
        seg.ready.connect(lambda px, path: overlay.set_mask(px))
        seg.segment("/path/to/wallpaper.jpg")
    """

    ready = pyqtSignal(QPixmap, str)        # (mask, wallpaper_path)
    error = pyqtSignal(str, str)            # (message, wallpaper_path)
    progress = pyqtSignal(str)              # status text

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE,
        model: str = "u2net",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache = cache_dir
        self._model = model
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._rembg_session = None  # cached AI session

    # ── public ───────────────────────────────────────────────────────

    def segment(self, wallpaper_path: str) -> None:
        """Start (or restart) segmentation for the given wallpaper."""
        self._stop_current()

        if not wallpaper_path or not Path(wallpaper_path).is_file():
            self.error.emit("Wallpaper file not found", wallpaper_path or "")
            return

        self._thread = QThread()
        self._worker = _Worker(
            wallpaper_path, self._cache, self._model,
            session=self._rembg_session,
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.status.connect(self.progress.emit)

        self._thread.start()
        log.info("Segmentation started for %s", wallpaper_path)

    def try_cache(self, wallpaper_path: str) -> QPixmap | None:
        """Return the cached mask if available, else *None*."""
        if not wallpaper_path:
            return None
        h = _Worker._file_hash(wallpaper_path)
        p = self._cache / f"{h}_{self._model}.png"
        if p.exists():
            px = QPixmap(str(p))
            return px if not px.isNull() else None
        return None

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        if value != self._model:
            self._model = value
            self._rembg_session = None  # invalidate cached session

    # ── internal ─────────────────────────────────────────────────────

    def _on_done(self, px: QPixmap, path: str) -> None:
        self.ready.emit(px, path)
        self._stop_current()

    def _on_fail(self, msg: str, path: str) -> None:
        self.error.emit(msg, path)
        self._stop_current()

    def _stop_current(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
