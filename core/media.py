"""
core/media.py — Asynchronous Windows GSMTC media listener.

Uses the modern ``winrt`` packages (winrt-Windows.Media.Control) to
monitor the *current* system media session (Spotify, browser, VLC —
anything that plugs into Windows' Global System Media Transport Controls).

Runs in a dedicated background QThread to avoid freezing the PyQt main
event loop, as WinRT async calls can block if asyncio is running directly
on the UI thread.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from PIL import Image
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

log = logging.getLogger(__name__)

_PLAYING = 4


class _MediaWorker(QObject):
    """Background worker that runs the asyncio event loop for media polling."""

    track_changed = pyqtSignal(dict)
    playback_changed = pyqtSignal(bool)
    position_changed = pyqtSignal(float, float)
    session_lost = pyqtSignal()
    shuffle_changed = pyqtSignal(bool)
    repeat_changed = pyqtSignal(int)
    auth_failed = pyqtSignal() # Kept for compatibility if main.py is listening

    def __init__(self, poll_ms: int = 200) -> None:
        super().__init__()
        self._poll_ms = poll_ms
        self._running = False
        self._last_key: str | None = None
        self._last_playing: bool | None = None
        self._last_shuffle: bool | None = None
        self._last_repeat: int | None = None
        self._has_session = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._slow_tick = 0
        self._mgr = None
        
        # We queue actions to the asyncio loop
        self._action_queue: asyncio.Queue | None = None

    def run(self) -> None:
        self._running = True
        try:
            asyncio.run(self._main_loop())
        except Exception as e:
            log.error("Media worker crashed: %s", e)

    def stop(self) -> None:
        self._running = False

    def trigger_play_pause(self) -> None:
        if self._loop and self._action_queue:
            asyncio.run_coroutine_threadsafe(self._action_queue.put("play_pause"), self._loop)

    def trigger_next(self) -> None:
        if self._loop and self._action_queue:
            asyncio.run_coroutine_threadsafe(self._action_queue.put("next"), self._loop)

    def trigger_prev(self) -> None:
        if self._loop and self._action_queue:
            asyncio.run_coroutine_threadsafe(self._action_queue.put("prev"), self._loop)

    def trigger_shuffle(self) -> None:
        if self._loop and self._action_queue:
            asyncio.run_coroutine_threadsafe(self._action_queue.put("shuffle"), self._loop)

    def trigger_repeat(self) -> None:
        if self._loop and self._action_queue:
            asyncio.run_coroutine_threadsafe(self._action_queue.put("repeat"), self._loop)

    def trigger_seek(self, pct: float) -> None:
        if self._loop and self._action_queue:
            asyncio.run_coroutine_threadsafe(self._action_queue.put(("seek", pct)), self._loop)

    async def _main_loop(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._action_queue = asyncio.Queue()

        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Mgr,
            )
            self._mgr = await Mgr.request_async()
        except ImportError:
            log.error("winrt packages missing!")
            self.session_lost.emit()
            return

        while self._running:
            # Check for queued actions
            while not self._action_queue.empty():
                action = await self._action_queue.get()
                if action == "play_pause":
                    await self._ctrl_play_pause()
                elif action == "next":
                    await self._ctrl_next()
                elif action == "prev":
                    await self._ctrl_prev()
                elif action == "shuffle":
                    await self._ctrl_shuffle()
                elif action == "repeat":
                    await self._ctrl_repeat()
                elif isinstance(action, tuple) and action[0] == "seek":
                    await self._ctrl_seek(action[1])
                self._action_queue.task_done()

            # Poll state
            await self._poll_fast()
            self._slow_tick += 1
            if self._slow_tick >= 5:
                if not getattr(self, '_slow_task', None) or self._slow_task.done():
                    self._slow_task = asyncio.create_task(self._poll_slow())
                self._slow_tick = 0

            # Dynamic adaptive sleep: poll fast when actively playing, slow down when idle
            if self._last_playing:
                sleep_s = self._poll_ms / 1000.0
            else:
                sleep_s = max(self._poll_ms * 4, 800) / 1000.0
            await asyncio.sleep(sleep_s)

    async def _poll_fast(self) -> None:
        try:
            if not self._mgr:
                return
            session = self._mgr.get_current_session()

            if session is None:
                if self._has_session:
                    self._has_session = False
                    self._last_key = None
                    self.session_lost.emit()
                return

            self._has_session = True

            pb = session.get_playback_info()
            is_playing = False
            if pb is not None:
                is_playing = (
                    pb.playback_status is not None
                    and pb.playback_status.value == _PLAYING
                )
                if is_playing != self._last_playing:
                    self._last_playing = is_playing
                    self.playback_changed.emit(is_playing)
                    
                # Check shuffle and repeat in fast loop for realtime sync
                shuff = False
                rep = 0
                try:
                    if hasattr(pb, 'is_shuffle_active') and pb.is_shuffle_active is not None:
                        shuff = pb.is_shuffle_active
                    if hasattr(pb, 'auto_repeat_mode') and pb.auto_repeat_mode is not None:
                        rep = pb.auto_repeat_mode.value
                except Exception:
                    pass
                    
                if shuff != self._last_shuffle:
                    self._last_shuffle = shuff
                    self.shuffle_changed.emit(shuff)
                if rep != self._last_repeat:
                    self._last_repeat = rep
                    self.repeat_changed.emit(rep)
                    
            else:
                if self._last_playing is not False:
                    self._last_playing = False
                    self.playback_changed.emit(False)

            tl = session.get_timeline_properties()
            if tl:
                try:
                    cur = tl.position.total_seconds()
                    end = tl.end_time.total_seconds()
                    
                    if is_playing:
                        now = datetime.now(timezone.utc)
                        elapsed = (now - tl.last_updated_time).total_seconds()
                        cur = min(cur + elapsed, end)
                        
                    self.position_changed.emit(cur, end)
                except Exception as e:
                    pass
                    
        except Exception:
            log.error("Fast poll error: %s", traceback.format_exc())

    async def _poll_slow(self) -> None:
        try:
            if not self._mgr:
                return
            session = self._mgr.get_current_session()
            if not session:
                return

            # Track metadata
            props = await session.try_get_media_properties_async()
            if not props:
                return

            key = f"{props.title}|{props.artist}|{props.album_title}"
            if key == self._last_key:
                return
            self._last_key = key

            pb = session.get_playback_info()
            shuff = False
            rep = 0
            if pb:
                try:
                    if hasattr(pb, 'is_shuffle_active') and pb.is_shuffle_active is not None:
                        shuff = pb.is_shuffle_active
                    if hasattr(pb, 'auto_repeat_mode') and pb.auto_repeat_mode is not None:
                        rep = pb.auto_repeat_mode.value
                except Exception as e:
                    log.error("Failed to read shuffle/repeat: %s", e)

            # 1. EMIT INSTANTLY (without album art) to prevent UI freezing
            info = {
                "title": props.title or "Unknown",
                "artist": props.artist or "Unknown Artist",
                "album": props.album_title or "",
                "art": None,
                "shuffle": shuff,
                "repeat": rep,
            }
            self.track_changed.emit(info)
            log.info("Track → %s — %s", props.artist, props.title)

            # 2. FETCH THUMBNAIL CONCURRENTLY (Does not block subsequent _poll_slow calls)
            async def _fetch_and_emit():
                try:
                    art_dict = await self._fetch_thumbnail(props)
                    if art_dict:
                        info_copy = info.copy()
                        info_copy["art"] = art_dict
                        self.track_changed.emit(info_copy)
                except Exception:
                    log.error("Thumbnail task failed:\n%s", traceback.format_exc())

            asyncio.create_task(_fetch_and_emit())

        except ImportError as exc:
            log.error("winrt packages not available: %s", exc)
            self.session_lost.emit()
        except Exception:
            log.error("Slow poll error:\n%s", traceback.format_exc())

    @staticmethod
    async def _fetch_thumbnail(props) -> dict[str, Any] | None:
        """Read the album-art into raw bytes (QPixmap must be created on main thread)."""
        try:
            ref = props.thumbnail
            if ref is None:
                return None

            from winrt.windows.storage.streams import Buffer, InputStreamOptions

            stream = await ref.open_read_async()
            size = stream.size
            buf = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)

            raw = bytes(bytearray(buf))
            pil = Image.open(BytesIO(raw)).convert("RGBA")
            
            return {
                "data": pil.tobytes("raw", "RGBA"),
                "width": pil.width,
                "height": pil.height,
            }

        except Exception:
            log.error("Thumbnail fetch failed:\n%s", traceback.format_exc())
            return None

    def _get_current_session(self):
        """Reuse the cached manager instead of re-requesting it."""
        if not self._mgr:
            return None
        return self._mgr.get_current_session()

    async def _ctrl_play_pause(self) -> None:
        s = self._get_current_session()
        if s:
            await s.try_toggle_play_pause_async()

    async def _ctrl_next(self) -> None:
        s = self._get_current_session()
        if s:
            await s.try_skip_next_async()

    async def _ctrl_prev(self) -> None:
        s = self._get_current_session()
        if s:
            await s.try_skip_previous_async()

    async def _ctrl_shuffle(self) -> None:
        s = self._get_current_session()
        if s:
            pb = s.get_playback_info()
            if pb:
                await s.try_change_shuffle_active_async(not pb.is_shuffle_active)

    async def _ctrl_repeat(self) -> None:
        s = self._get_current_session()
        if s:
            pb = s.get_playback_info()
            if pb and pb.auto_repeat_mode:
                from winrt.windows.media import MediaPlaybackAutoRepeatMode
                # 0 = None, 1 = Track, 2 = List
                # Toggle: None -> List -> Track -> None
                cur = pb.auto_repeat_mode.value
                nxt = 2 if cur == 0 else (1 if cur == 2 else 0)
                await s.try_change_auto_repeat_mode_async(MediaPlaybackAutoRepeatMode(nxt))

    async def _ctrl_seek(self, pct: float) -> None:
        s = self._get_current_session()
        if s:
            tl = s.get_timeline_properties()
            if tl:
                dur = tl.end_time.total_seconds()
                ticks = int(dur * pct * 10_000_000)
                await s.try_change_playback_position_async(ticks)

class MediaController(QObject):
    """Real-time bridge between Windows media sessions and Qt signals."""

    track_changed = pyqtSignal(dict)
    lyrics_changed = pyqtSignal(list)
    playback_changed = pyqtSignal(bool)
    position_changed = pyqtSignal(float, float)
    session_lost = pyqtSignal()
    shuffle_changed = pyqtSignal(bool)
    repeat_changed = pyqtSignal(int)
    auth_failed = pyqtSignal()

    def __init__(self, poll_ms: int = 1000, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _MediaWorker(poll_ms)
        self._worker.moveToThread(self._thread)
        
        from core.lyrics import LyricsFetcher
        self._lyrics_fetcher = LyricsFetcher(self)
        self._lyrics_fetcher.lyrics_ready.connect(self.lyrics_changed.emit)

        self._thread.started.connect(self._worker.run)
        
        # Connect signals
        self._worker.track_changed.connect(self._on_track_changed)
        self._worker.playback_changed.connect(self.playback_changed.emit)
        self._worker.position_changed.connect(self.position_changed.emit)
        self._worker.session_lost.connect(self.session_lost.emit)
        self._worker.shuffle_changed.connect(self.shuffle_changed.emit)
        self._worker.repeat_changed.connect(self.repeat_changed.emit)
        self._worker.auth_failed.connect(self.auth_failed.emit)

    def start(self) -> None:
        self._thread.start()
        log.info("MediaController started (WinRT Universal)")

    def stop(self) -> None:
        self._worker.stop()
        self._lyrics_fetcher.stop()
        self._thread.quit()
        self._thread.wait(2000)
        log.info("MediaController stopped")

    def play_pause(self) -> None:
        self._worker.trigger_play_pause()

    def next_track(self) -> None:
        self._worker.trigger_next()

    def previous_track(self) -> None:
        self._worker.trigger_prev()
        
    def trigger_shuffle(self) -> None:
        self._worker.trigger_shuffle()

    def trigger_repeat(self) -> None:
        self._worker.trigger_repeat()

    def trigger_seek(self, pct: float) -> None:
        self._worker.trigger_seek(pct)
        
    def _on_track_changed(self, info: dict) -> None:
        """Convert raw image bytes to QPixmap on the main thread (GUI thread)."""
        art_dict = info.pop("art")
        if art_dict:
            try:
                qi = QImage(
                    art_dict["data"], art_dict["width"], art_dict["height"],
                    art_dict["width"] * 4, QImage.Format.Format_RGBA8888,
                )
                info["art"] = QPixmap.fromImage(qi.copy())
            except Exception as e:
                log.error("Failed to create QPixmap: %s", e)
        else:
            info["art"] = None
            
        # Trigger lyrics fetch
        title = info.get("title", "")
        artist = info.get("artist", "")
        track_key = (title, artist)
        if getattr(self, "_last_fetched_key", None) != track_key:
            self._last_fetched_key = track_key
            if title and artist and title != "Unknown":
                self.lyrics_changed.emit([])  # Clear lyrics immediately
                self._lyrics_fetcher.fetch(title, artist)
            else:
                self.lyrics_changed.emit([])

        self.track_changed.emit(info)
