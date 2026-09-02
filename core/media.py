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

import ctypes
from ctypes import wintypes
import os

kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def get_process_name_for_pid(pid: int) -> str:
    h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h_proc:
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(1024)
    res = ""
    if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
        res = os.path.basename(buf.value).lower()
    kernel32.CloseHandle(h_proc)
    return res


def find_media_window(app_id: str, title: str = "") -> int | None:
    """Locate the top-level visible window HWND for the active media source."""
    if not app_id and not title:
        return None
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x0100)
        if not hdesk:
            return None

        app_token = os.path.splitext(os.path.basename(app_id))[0].lower() if app_id else ""
        title_lower = title.lower() if title else ""

        best_hwnd = None
        fallback_hwnd = None
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            nonlocal best_hwnd, fallback_hwnd
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return 1
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            w_title = buf.value.lower()
            if not w_title:
                return 1

            # Check process name
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pname = get_process_name_for_pid(pid.value)

            # Match exact track/video title in window title
            if title_lower and title_lower in w_title:
                best_hwnd = hwnd
                return 0

            # Match app token in process name or window title
            if app_token and (app_token in pname or app_token in w_title):
                if not fallback_hwnd:
                    fallback_hwnd = hwnd
            return 1

        user32.EnumDesktopWindows(hdesk, WNDENUMPROC(cb), 0)
        user32.CloseDesktop(hdesk)
        return best_hwnd or fallback_hwnd
    except Exception as exc:
        log.debug("find_media_window failed: %s", exc)
        return None

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
    auth_failed = pyqtSignal()
    sessions_updated = pyqtSignal(list)

    def __init__(self, poll_ms: int = 200, cfg: Any = None) -> None:
        super().__init__()
        self._poll_ms = poll_ms
        self._cfg = cfg
        self._running = False
        self._last_key: str | None = None
        self._last_playing: bool | None = None
        self._last_shuffle: bool | None = None
        self._last_repeat: int | None = None
        self._has_session = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._slow_tick = 0
        self._mgr = None
        self._active_session = None
        
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

    def refresh_sessions(self) -> None:
        if self._loop and self._action_queue:
            asyncio.run_coroutine_threadsafe(self._action_queue.put("refresh_sessions"), self._loop)

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
                elif action == "refresh_sessions":
                    await self._poll_sessions_list()
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

    def _get_target_session(self):
        """Intelligently select media session based on mode and playback state."""
        if not self._mgr:
            return None
        try:
            sessions = list(self._mgr.get_sessions())
        except Exception:
            sessions = []

        if not sessions:
            return None

        source_mode = self._cfg.get("media_source_mode", "auto") if self._cfg else "auto"
        selected_app = (self._cfg.get("selected_media_source", "") if self._cfg else "").strip().lower()

        # 1. Manual mode: look for user's specifically chosen app
        if source_mode == "manual" and selected_app:
            for s in sessions:
                app_id = (s.source_app_user_model_id or "").lower()
                if selected_app in app_id or app_id in selected_app:
                    return s

        # 2. Auto mode: prioritize whichever session is actively playing (_PLAYING == 4)
        for s in sessions:
            try:
                pb = s.get_playback_info()
                if pb and pb.playback_status and pb.playback_status.value == _PLAYING:
                    return s
            except Exception:
                pass

        # 3. Fallback to Windows default current session
        try:
            curr = self._mgr.get_current_session()
            if curr:
                return curr
        except Exception:
            pass

        return sessions[0]

    async def _poll_sessions_list(self) -> None:
        """Enumerate all active media sessions and emit sessions_updated signal."""
        if not self._mgr:
            return
        try:
            sessions = list(self._mgr.get_sessions())
        except Exception:
            sessions = []

        result = []
        source_mode = self._cfg.get("media_source_mode", "auto") if self._cfg else "auto"
        selected_app = (self._cfg.get("selected_media_source", "") if self._cfg else "").strip().lower()
        active_app = (self._active_session.source_app_user_model_id or "").lower() if self._active_session else ""

        for s in sessions:
            app_id = s.source_app_user_model_id or "Unknown"
            is_playing = False
            try:
                pb = s.get_playback_info()
                is_playing = (pb.playback_status and pb.playback_status.value == _PLAYING) if pb else False
            except Exception:
                pass

            title = "Unknown"
            artist = ""
            ptype = 0
            try:
                props = await s.try_get_media_properties_async()
                if props:
                    title = props.title or "Unknown"
                    artist = props.artist or ""
                    ptype = int(props.playback_type) if props.playback_type is not None else 0
            except Exception:
                pass

            if source_mode == "manual":
                is_selected = bool(selected_app and (selected_app in app_id.lower() or app_id.lower() in selected_app))
            else:
                is_selected = (app_id.lower() == active_app)

            result.append({
                "app_id": app_id,
                "title": title,
                "artist": artist,
                "is_playing": is_playing,
                "playback_type": ptype,
                "is_selected": is_selected,
            })
        self.sessions_updated.emit(result)

    async def _poll_fast(self) -> None:
        try:
            if not self._mgr:
                return
            session = self._get_target_session()
            self._active_session = session

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
            session = self._get_target_session()
            self._active_session = session
            if not session:
                return

            # Also refresh sessions list periodically
            await self._poll_sessions_list()

            # Track metadata
            props = await session.try_get_media_properties_async()
            if not props:
                return

            key = f"{session.source_app_user_model_id}|{props.title}|{props.artist}|{props.album_title}"
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

            # Check if playing video
            is_video = False
            source_hwnd = None
            try:
                video_mirror_enabled = self._cfg.get("video_mirror_enabled", True) if self._cfg else True
                treat_browser = self._cfg.get("treat_browser_as_video", True) if self._cfg else True
                app_id_lower = (session.source_app_user_model_id or "").lower()
                ptype = int(props.playback_type) if props.playback_type is not None else 0

                if video_mirror_enabled:
                    if ptype == 2:
                        is_video = True
                    elif treat_browser:
                        browser_and_video_tokens = (
                            "edge", "chrome", "firefox", "brave", "opera", "vlc",
                            "mpc", "potplayer", "netflix", "video", "twitch", "youtube"
                        )
                        music_tokens = ("spotify", "itunes", "applemusic", "tidal", "deezer")
                        if any(t in app_id_lower for t in browser_and_video_tokens) and not any(m in app_id_lower for m in music_tokens):
                            is_video = True
                        elif any(t in (props.title or "").lower() for t in ("youtube", "twitch", "netflix", "video")):
                            is_video = True

                if is_video:
                    source_hwnd = find_media_window(session.source_app_user_model_id, props.title or "")
            except Exception as e:
                log.debug("Video check error: %s", e)

            # 1. EMIT INSTANTLY (without album art) to prevent UI freezing
            info = {
                "title": props.title or "Unknown",
                "artist": props.artist or "Unknown Artist",
                "album": props.album_title or "",
                "art": None,
                "shuffle": shuff,
                "repeat": rep,
                "is_video": is_video,
                "source_hwnd": source_hwnd,
                "app_id": session.source_app_user_model_id or "",
            }
            self.track_changed.emit(info)
            log.info("Track → %s — %s (app=%s, video=%s, hwnd=%s)", props.artist, props.title, session.source_app_user_model_id, is_video, hex(source_hwnd) if source_hwnd else "None")

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
        """Reuse the target session."""
        return self._get_target_session()

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
    caption_ready = pyqtSignal(str, str)  # (text, detected_language)
    playback_changed = pyqtSignal(bool)
    position_changed = pyqtSignal(float, float)
    session_lost = pyqtSignal()
    shuffle_changed = pyqtSignal(bool)
    repeat_changed = pyqtSignal(int)
    auth_failed = pyqtSignal()
    sessions_updated = pyqtSignal(list)

    def __init__(self, poll_ms: int = 1000, cfg: Any = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._thread = QThread()
        self._worker = _MediaWorker(poll_ms, cfg)
        self._worker.moveToThread(self._thread)

        from core.lyrics import LyricsFetcher
        self._lyrics_fetcher = LyricsFetcher(self)
        self._lyrics_fetcher.lyrics_ready.connect(self._on_lyrics_ready)

        from core.captions import AutoCaptionEngine
        self._caption_engine = AutoCaptionEngine(self)
        self._caption_engine.caption_ready.connect(self.caption_ready.emit)

        self._is_playing = False
        self._has_synced_lyrics = False

        self._thread.started.connect(self._worker.run)

        # Connect signals
        self._worker.track_changed.connect(self._on_track_changed)
        self._worker.playback_changed.connect(self._on_playback_state_changed)
        self._worker.position_changed.connect(self.position_changed.emit)
        self._worker.session_lost.connect(self._on_session_lost)
        self._worker.shuffle_changed.connect(self.shuffle_changed.emit)
        self._worker.repeat_changed.connect(self.repeat_changed.emit)
        self._worker.auth_failed.connect(self.auth_failed.emit)
        self._worker.sessions_updated.connect(self.sessions_updated.emit)

    def refresh_sessions(self) -> None:
        self._worker.refresh_sessions()

    def start(self) -> None:
        self._thread.start()
        log.info("MediaController started (WinRT Universal)")

    def stop(self) -> None:
        self._worker.stop()
        self._lyrics_fetcher.stop()
        self._caption_engine.shutdown()
        self._thread.quit()
        self._thread.wait(2000)
        log.info("MediaController stopped")

    def _on_playback_state_changed(self, is_playing: bool) -> None:
        self._is_playing = is_playing
        self.playback_changed.emit(is_playing)
        if not is_playing:
            self._caption_engine.stop()
        elif not self._has_synced_lyrics:
            self._caption_engine.start()

    def _on_lyrics_ready(self, lyrics: list) -> None:
        self.lyrics_changed.emit(lyrics)
        if lyrics:
            self._has_synced_lyrics = True
            self._caption_engine.stop()
        else:
            self._has_synced_lyrics = False
            if self._is_playing:
                self._caption_engine.start()

    def _on_session_lost(self) -> None:
        self._caption_engine.stop()
        self.session_lost.emit()

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
