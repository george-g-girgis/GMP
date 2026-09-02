"""
core/native_player.py — Native hardware-accelerated video playback engine for GMP.

Plays local video files (MP4, MKV, AVI, MOV, WebM) directly inside GMP using
PyQt6.QtMultimedia (QMediaPlayer + QAudioOutput + QVideoSink).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

log = logging.getLogger(__name__)


class NativeVideoPlayer(QObject):
    """Encapsulates QMediaPlayer and QVideoSink to stream video frames to PlayerWidget."""

    frame_ready = pyqtSignal(QPixmap)
    position_changed = pyqtSignal(float, float)  # (current_seconds, total_seconds)
    playback_changed = pyqtSignal(bool)         # is_playing
    media_loaded = pyqtSignal(str, str)          # (title, file_path)
    media_ended = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._sink = QVideoSink(self)

        self._player.setAudioOutput(self._audio)
        self._player.setVideoSink(self._sink)

        self._current_file: str | None = None
        self._is_active = False

        self._sink.videoFrameChanged.connect(self._on_video_frame)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def current_file(self) -> str | None:
        return self._current_file

    def play_file(self, file_path: str, start_pos_ms: int = 0) -> bool:
        if not os.path.exists(file_path):
            log.warning("Video file does not exist: %s", file_path)
            return False

        self._current_file = file_path
        self._is_active = True
        url = QUrl.fromLocalFile(file_path)
        self._player.setSource(url)

        if start_pos_ms > 0:
            self._player.setPosition(start_pos_ms)

        self._player.play()
        title = Path(file_path).stem
        self.media_loaded.emit(title, file_path)
        log.info("NativeVideoPlayer playing: %s", file_path)
        return True

    def toggle_play_pause(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def play(self) -> None:
        self._player.play()

    def stop(self) -> None:
        self._player.stop()
        self._is_active = False
        self._current_file = None

    def seek_percent(self, pct: float) -> None:
        dur = self._player.duration()
        if dur > 0:
            target = int(dur * max(0.0, min(1.0, pct)))
            self._player.setPosition(target)

    def set_volume(self, vol: float) -> None:
        self._audio.setVolume(max(0.0, min(1.0, vol)))

    def _on_video_frame(self, frame) -> None:
        if not self._is_active or not frame.isValid():
            return
        img = frame.toImage()
        if not img.isNull():
            pm = QPixmap.fromImage(img)
            self.frame_ready.emit(pm)

    def _on_position_changed(self, pos_ms: int) -> None:
        dur_ms = self._player.duration()
        cur_s = pos_ms / 1000.0
        dur_s = max(0.0, dur_ms / 1000.0)
        self.position_changed.emit(cur_s, dur_s)

    def _on_duration_changed(self, dur_ms: int) -> None:
        cur_s = self._player.position() / 1000.0
        dur_s = max(0.0, dur_ms / 1000.0)
        self.position_changed.emit(cur_s, dur_s)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        is_playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        self.playback_changed.emit(is_playing)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.media_ended.emit()
