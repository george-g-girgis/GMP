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

user32 = ctypes.windll.user32
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL

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
    """Locate the top-level window HWND for the active media source."""
    if not app_id and not title:
        return None
    try:
        import re
        hdesk = user32.OpenInputDesktop(0, False, 0x0100)
        if not hdesk:
            return None

        app_token = os.path.splitext(os.path.basename(app_id))[0].lower() if app_id else ""
        title_lower = title.lower() if title else ""
        keywords = [k for k in re.findall(r"[\w\d]+", title_lower) if len(k) >= 3]

        best_hwnd = None
        fallback_hwnd = None
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            nonlocal best_hwnd, fallback_hwnd
            if not user32.IsWindow(hwnd):
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

            # Match title
            title_match = False
            if title_lower and (title_lower in w_title or w_title in title_lower):
                title_match = True
            elif keywords:
                matches = sum(1 for kw in keywords if kw in w_title)
                if matches >= 2 or (len(keywords) == 1 and matches == 1):
                    title_match = True

            # Match app
            app_match = False
            if app_token and (app_token in pname or app_token in w_title):
                app_match = True
            if "vlc" in app_token or "vlc" in pname or "vlc media player" in w_title:
                app_match = True

            if title_match and app_match:
                best_hwnd = hwnd
                return 0
            if title_match and not best_hwnd:
                best_hwnd = hwnd
            if app_match and not fallback_hwnd:
                fallback_hwnd = hwnd
            return 1

        user32.EnumDesktopWindows(hdesk, WNDENUMPROC(cb), 0)
        user32.CloseDesktop(hdesk)

        target = best_hwnd or fallback_hwnd
        if target:
            # If minimized, restore without activating to allow DWM to capture its buffer
            if user32.IsIconic(target):
                SW_SHOWNOACTIVATE = 4
                user32.ShowWindow(target, SW_SHOWNOACTIVATE)
                HWND_BOTTOM = 1
                user32.SetWindowPos(target, HWND_BOTTOM, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

        return target
    except Exception as exc:
        log.debug("find_media_window failed: %s", exc)
        return None

def scan_standalone_players() -> list[dict]:
    """Inspect desktop windows for active players that do not hook into Windows GSMTC (e.g. VLC)."""
    results = []

    def cb(hwnd, _):
        if not user32.IsWindow(hwnd):
            return 1
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        txt = buf.value.strip()
        if not txt:
            return 1

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pname = get_process_name_for_pid(pid.value)

        if "vlc.exe" in pname or "vlc" in pname:
            title = txt.replace(" - VLC media player", "").strip()
            cls_buf = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(hwnd, cls_buf, 512)
            if cls_buf.value == "Qt5QWindowIcon":
                is_playing = bool(title and title != "VLC media player")
                results.append({
                    "app_id": "vlc.exe",
                    "name": "VLC Media Player",
                    "title": title if is_playing else "VLC Media Player",
                    "artist": "VLC Media Player",
                    "hwnd": hwnd,
                    "is_video": True,
                    "is_playing": is_playing,
                    "playback_type": 2,
                })
        elif any(p in pname for p in ("mpc-hc", "potplayer", "wmplayer")):
            results.append({
                "app_id": pname,
                "name": pname.replace(".exe", "").capitalize(),
                "title": txt,
                "artist": pname.replace(".exe", "").capitalize(),
                "hwnd": hwnd,
                "is_video": True,
                "is_playing": True,
                "playback_type": 2,
            })
        return 1

    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x0100 | 0x0040 | 0x0001)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
            proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb)
            user32.EnumDesktopWindows(hdesk, proc, 0)
            user32.CloseDesktop(hdesk)
    except Exception as e:
        log.debug("scan_standalone_players error: %s", e)
    return results


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

            # Dynamic adaptive sleep
            if self._last_playing:
                sleep_s = self._poll_ms / 1000.0
            else:
                sleep_s = max(self._poll_ms * 4, 800) / 1000.0
            await asyncio.sleep(sleep_s)

    def _get_target_session(self):
        """Intelligently select media session across GSMTC and standalone players (VLC, etc.)."""
        source_mode = self._cfg.get("media_source_mode", "auto") if self._cfg else "auto"
        selected_app = (self._cfg.get("selected_media_source", "") if self._cfg else "").strip().lower()

        standalone = scan_standalone_players()

        # 1. Manual mode: check standalone first
        if source_mode == "manual" and selected_app:
            for p in standalone:
                if selected_app in p["app_id"] or p["app_id"] in selected_app:
                    return p

        # 2. Auto mode: if a standalone video player is playing, prioritize it!
        if source_mode == "auto":
            for p in standalone:
                if p.get("is_playing"):
                    return p

        # Check GSMTC sessions
        if not self._mgr:
            return standalone[0] if standalone else None
        try:
            sessions = list(self._mgr.get_sessions())
        except Exception:
            sessions = []

        if source_mode == "manual" and selected_app:
            for s in sessions:
                app_id = (s.source_app_user_model_id or "").lower()
                if selected_app in app_id or app_id in selected_app:
                    return s

        if source_mode == "auto":
            for s in sessions:
                try:
                    pb = s.get_playback_info()
                    if pb and pb.playback_status and pb.playback_status.value == _PLAYING:
                        return s
                except Exception:
                    pass

        try:
            curr = self._mgr.get_current_session()
            if curr:
                return curr
        except Exception:
            pass

        if sessions:
            return sessions[0]
        if standalone:
            return standalone[0]
        return None

    async def _poll_sessions_list(self) -> None:
        """Enumerate all active media sessions (GSMTC + VLC/standalone) and emit sessions_updated."""
        result = []
        source_mode = self._cfg.get("media_source_mode", "auto") if self._cfg else "auto"
        selected_app = (self._cfg.get("selected_media_source", "") if self._cfg else "").strip().lower()

        active_app = ""
        if isinstance(self._active_session, dict):
            active_app = self._active_session.get("app_id", "").lower()
        elif self._active_session and hasattr(self._active_session, "source_app_user_model_id"):
            active_app = (self._active_session.source_app_user_model_id or "").lower()

        # 1. Standalone players (VLC, etc.)
        for p in scan_standalone_players():
            app_id = p["app_id"]
            if source_mode == "manual":
                is_selected = bool(selected_app and (selected_app in app_id.lower() or app_id.lower() in selected_app))
            else:
                is_selected = (active_app == app_id.lower())

            result.append({
                "app_id": app_id,
                "title": p["title"],
                "artist": p["artist"],
                "is_playing": p["is_playing"],
                "playback_type": p["playback_type"],
                "is_selected": is_selected,
                "hwnd": p["hwnd"],
            })

        # 2. GSMTC sessions
        if self._mgr:
            try:
                sessions = list(self._mgr.get_sessions())
            except Exception:
                sessions = []

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
            session = self._get_target_session()
            self._active_session = session

            if session is None:
                if self._has_session:
                    self._has_session = False
                    self._last_key = None
                    self.session_lost.emit()
                return

            self._has_session = True

            # Standalone player handling
            if isinstance(session, dict):
                is_playing = session.get("is_playing", False)
                if is_playing != self._last_playing:
                    self._last_playing = is_playing
                    self.playback_changed.emit(is_playing)
                return

            # GSMTC player handling
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
                except Exception:
                    pass
        except Exception:
            log.error("Fast poll error: %s", traceback.format_exc())

    async def _poll_slow(self) -> None:
        try:
            session = self._get_target_session()
            self._active_session = session
            if not session:
                return

            await self._poll_sessions_list()

            # Standalone player handling (VLC, etc.)
            if isinstance(session, dict):
                app_id = session.get("app_id", "vlc.exe")
                title = session.get("title", "Video")
                artist = session.get("artist", "VLC Media Player")
                key = f"{app_id}|{title}|{artist}"
                if key == self._last_key:
                    return
                self._last_key = key

                info = {
                    "title": title,
                    "artist": artist,
                    "album": "",
                    "art": None,
                    "shuffle": False,
                    "repeat": 0,
                    "is_video": True,
                    "source_hwnd": session.get("hwnd"),
                    "app_id": app_id,
                }
                self.track_changed.emit(info)
                self.playback_changed.emit(session.get("is_playing", True))
                log.info("Track (Standalone) → %s — %s (app=%s, hwnd=%s)", artist, title, app_id, hex(session.get("hwnd", 0)))
                return
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
        if not s:
            return
        if isinstance(s, dict):
            hwnd = s.get("hwnd")
            if hwnd:
                user32.PostMessageW(wintypes.HWND(hwnd), 0x0100, 0x20, 0)  # WM_KEYDOWN VK_SPACE
                user32.PostMessageW(wintypes.HWND(hwnd), 0x0101, 0x20, 0)  # WM_KEYUP VK_SPACE
        else:
            await s.try_toggle_play_pause_async()

    async def _ctrl_next(self) -> None:
        s = self._get_current_session()
        if not s:
            return
        if isinstance(s, dict):
            hwnd = s.get("hwnd")
            if hwnd:
                user32.PostMessageW(wintypes.HWND(hwnd), 0x0100, 0x4E, 0)  # 'N' next in VLC
                user32.PostMessageW(wintypes.HWND(hwnd), 0x0101, 0x4E, 0)
        else:
            await s.try_skip_next_async()

    async def _ctrl_prev(self) -> None:
        s = self._get_current_session()
        if not s:
            return
        if isinstance(s, dict):
            hwnd = s.get("hwnd")
            if hwnd:
                user32.PostMessageW(wintypes.HWND(hwnd), 0x0100, 0x50, 0)  # 'P' prev in VLC
                user32.PostMessageW(wintypes.HWND(hwnd), 0x0101, 0x50, 0)
        else:
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
        self._caption_engine = AutoCaptionEngine(self._cfg, self)
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

    def _update_caption_state(self) -> None:
        if not self._is_playing:
            self._caption_engine.stop()
            return

        mode = self._cfg.get("captions_mode", "auto") if self._cfg else "auto"
        if mode == "disabled":
            self._caption_engine.stop()
        elif mode == "speech_only":
            self._caption_engine.start()
        elif mode == "lrclib_only":
            self._caption_engine.stop()
        else:  # "auto"
            if self._has_synced_lyrics:
                self._caption_engine.stop()
            else:
                self._caption_engine.start()

    def _on_playback_state_changed(self, is_playing: bool) -> None:
        self._is_playing = is_playing
        self.playback_changed.emit(is_playing)
        self._update_caption_state()

    def _on_lyrics_ready(self, lyrics: list) -> None:
        self.lyrics_changed.emit(lyrics)
        self._has_synced_lyrics = bool(lyrics)
        self._update_caption_state()

    def _on_session_lost(self) -> None:
        self._is_playing = False
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
