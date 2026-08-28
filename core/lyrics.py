import json
import logging
import re
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

log = logging.getLogger(__name__)

# Max in-memory LRU cache size
_CACHE_MAX_SIZE = 128


def _clean_title(title: str) -> str:
    """Strip extraneous release tags for improved LrcLib hit rate."""
    t = title.strip()
    # Remove bracketed/parenthetical metadata like [Remastered], (feat. X), (Live at Y)
    t = re.sub(r"\s*[\(\[](?:feat\.|featuring|remaster(?:ed)?|live|radio edit|deluxe|bonus|version|explicit)[\s\S]*?[\)\]]", "", t, flags=re.IGNORECASE)
    # Remove trailing ' - Remastered 2021' style suffixes
    t = re.sub(r"\s+-\s+.*(?:remaster|live|edit|version).*", "", t, flags=re.IGNORECASE)
    return t.strip() or title.strip()


class _LyricsWorker(QObject):
    """Worker object to run blocking urllib requests with LRU caching."""

    ready = pyqtSignal(list)

    def __init__(self) -> None:
        super().__init__()
        self._cache: OrderedDict[tuple[str, str], list] = OrderedDict()

    def fetch(self, track: str, artist: str) -> None:
        if not track or not artist:
            self.ready.emit([])
            return

        cache_key = (track.lower().strip(), artist.lower().strip())
        if cache_key in self._cache:
            # Move to end (most recently used)
            self._cache.move_to_end(cache_key)
            log.info("Lyrics LRU cache hit for '%s' — '%s'", artist, track)
            self.ready.emit(self._cache[cache_key])
            return

        cleaned_track = _clean_title(track)
        parsed = self._do_fetch(cleaned_track, artist)

        # If clean search didn't yield synced lyrics and clean differs from original, try original as fallback
        if not parsed and cleaned_track != track:
            parsed = self._do_fetch(track, artist)

        # Store in LRU cache
        if len(self._cache) >= _CACHE_MAX_SIZE:
            self._cache.popitem(last=False)  # evict oldest
        self._cache[cache_key] = parsed

        self.ready.emit(parsed)

    def _do_fetch(self, track: str, artist: str) -> list[tuple[float, str]]:
        try:
            query = urllib.parse.urlencode({"track_name": track, "artist_name": artist})
            url = f"https://lrclib.net/api/get?{query}"
            req = urllib.request.Request(url, headers={"User-Agent": "GMP/1.1 (https://github.com/george-g-girgis/GMP)"})

            with urllib.request.urlopen(req, timeout=3.5) as res:
                data = json.loads(res.read().decode("utf-8"))

            synced = data.get("syncedLyrics")
            if not synced:
                return []

            # Parse LRC format [mm:ss.xx]
            parsed = []
            for line in synced.split("\n"):
                line = line.strip()
                if not line.startswith("["):
                    continue
                end_idx = line.find("]")
                if end_idx == -1:
                    continue

                time_str = line[1:end_idx]
                text = line[end_idx + 1 :].strip()

                try:
                    m, s = time_str.split(":")
                    sec = int(m) * 60 + float(s)
                    parsed.append((sec, text))
                except Exception:
                    continue

            log.info("Fetched %d lines of synced lyrics for '%s'", len(parsed), track)
            return parsed

        except urllib.error.URLError as e:
            log.debug("Network error fetching lyrics for '%s': %s", track, e)
            return []
        except Exception as e:
            log.debug("Lyrics fetch/parse failed for '%s': %s", track, e)
            return []


class LyricsFetcher(QObject):
    """Async wrapper for fetching LRC lyrics."""
    
    lyrics_ready = pyqtSignal(list)
    
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _LyricsWorker()
        self._worker.moveToThread(self._thread)
        
        # Use a helper QObject to safely emit cross-thread signals
        self._trigger = _LyricsTrigger(self)
        self._trigger.fetch.connect(self._worker.fetch)
        
        self._worker.ready.connect(self.lyrics_ready.emit)
        self._thread.start()
        
    def fetch(self, track: str, artist: str) -> None:
        self._trigger.fetch.emit(track, artist)

    def stop(self) -> None:
        self._thread.quit()
        self._thread.wait(1000)


class _LyricsTrigger(QObject):
    fetch = pyqtSignal(str, str)
