"""
core/video.py — Real-time window video frame streaming for GMP.

Captures hardware-accelerated video frames directly from target windows
(VLC media player, YouTube in Edge/Chrome/Brave, Netflix, etc.) via
Windows PrintWindow (PW_RENDERFULLCONTENT) and streams them to the UI thread
as QPixmaps at 24-30 FPS.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import time

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _VideoWorker(QObject):
    """Background worker continuously capturing and emitting video frames."""

    frame_ready = pyqtSignal(QPixmap)

    def __init__(self, fps: int = 24) -> None:
        super().__init__()
        self._fps = fps
        self._interval = 1.0 / max(1, fps)
        self._running = False
        self._active = False
        self._hwnd: int | None = None
        self._target_w = 480
        self._target_h = 270

    def set_target(self, hwnd: int | None, w: int = 480, h: int = 270) -> None:
        self._hwnd = hwnd
        self._target_w = max(100, w)
        self._target_h = max(100, h)
        self._active = bool(hwnd)

    def stop_capture(self) -> None:
        self._active = False
        self._hwnd = None

    def shutdown(self) -> None:
        self._running = False
        self._active = False

    def run(self) -> None:
        self._running = True
        log.info("Video mirror engine thread started")

        try:
            hdesk = user32.OpenInputDesktop(0, False, 0x0100 | 0x0040 | 0x0001)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

        while self._running:
            if not self._active or not self._hwnd:
                time.sleep(0.1)
                continue

            t0 = time.perf_counter()
            pm = self._capture_frame()
            if pm and not pm.isNull():
                self.frame_ready.emit(pm)

            elapsed = time.perf_counter() - t0
            sleep_time = max(0.005, self._interval - elapsed)
            time.sleep(sleep_time)

        log.info("Video mirror engine thread stopped")

    def _capture_frame(self) -> QPixmap | None:
        hwnd = self._hwnd
        if not hwnd or not user32.IsWindow(wintypes.HWND(hwnd)):
            return None

        # If minimized (iconic), do not force un-minimize onto screen
        if user32.IsIconic(wintypes.HWND(hwnd)):
            return None

        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None

        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 10 or h <= 10:
            return None

        hdc_screen = user32.GetDC(0)
        if not hdc_screen:
            return None

        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        old_bm = gdi32.SelectObject(hdc_mem, hbm)

        PW_RENDERFULLCONTENT = 2
        success = user32.PrintWindow(wintypes.HWND(hwnd), hdc_mem, PW_RENDERFULLCONTENT)
        pixmap = None

        if success:
            tw, th = self._target_w, self._target_h
            hdc_small = gdi32.CreateCompatibleDC(hdc_screen)
            hbm_small = gdi32.CreateCompatibleBitmap(hdc_screen, tw, th)
            old_small = gdi32.SelectObject(hdc_small, hbm_small)

            gdi32.SetStretchBltMode(hdc_small, 3)  # COLORONCOLOR
            gdi32.StretchBlt(hdc_small, 0, 0, tw, th, hdc_mem, 0, 0, w, h, 0x00CC0020)

            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = tw
            bmi.bmiHeader.biHeight = -th
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0

            buf = ctypes.create_string_buffer(tw * th * 4)
            gdi32.GetDIBits(hdc_small, hbm_small, 0, th, buf, ctypes.byref(bmi), 0)

            raw_bytes = bytes(buf)
            qi = QImage(raw_bytes, tw, th, tw * 4, QImage.Format.Format_ARGB32_Premultiplied)
            pixmap = QPixmap.fromImage(qi)

            gdi32.SelectObject(hdc_small, old_small)
            gdi32.DeleteObject(hbm_small)
            gdi32.DeleteDC(hdc_small)

        gdi32.SelectObject(hdc_mem, old_bm)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

        return pixmap


class VideoMirrorEngine(QObject):
    """Real-time video mirror controller."""

    frame_ready = pyqtSignal(QPixmap)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _VideoWorker(fps=24)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.frame_ready.connect(self.frame_ready.emit)
        self._thread.start()

    def start_stream(self, hwnd: int, w: int = 480, h: int = 270) -> None:
        self._worker.set_target(hwnd, w, h)

    def stop_stream(self) -> None:
        self._worker.stop_capture()

    def shutdown(self) -> None:
        self._worker.shutdown()
        self._thread.quit()
        self._thread.wait(1500)
