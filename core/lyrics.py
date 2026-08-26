import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

log = logging.getLogger(__name__)


class _LyricsWorker(QObject):
    """Worker object to run blocking urllib requests."""

    ready = pyqtSignal(list)
    
    def fetch(self, track: str, artist: str) -> None:
        if not track or not artist:
            self.ready.emit([])
            return
            
        # Basic cleanup for better search hits
        track = track.split(" (")[0].split(" - ")[0]
        
        try:
            query = urllib.parse.urlencode({"track_name": track, "artist_name": artist})
            url = f"https://lrclib.net/api/get?{query}"
            req = urllib.request.Request(url, headers={'User-Agent': 'GMP/1.0'})
            
            with urllib.request.urlopen(req, timeout=3) as res:
                data = json.loads(res.read().decode())
                
            synced = data.get("syncedLyrics")
            if not synced:
                self.ready.emit([])
                return
                
            # Parse LRC [mm:ss.xx]
            parsed = []
            for line in synced.split('\n'):
                line = line.strip()
                if not line.startswith('['):
                    continue
                end_idx = line.find(']')
                if end_idx == -1:
                    continue
                    
                time_str = line[1:end_idx]
                text = line[end_idx+1:].strip()
                
                # convert mm:ss.xx to seconds
                try:
                    m, s = time_str.split(':')
                    sec = int(m) * 60 + float(s)
                    parsed.append((sec, text))
                except Exception:
                    continue
                    
            self.ready.emit(parsed)
            log.info("Fetched %d lines of lyrics for %s", len(parsed), track)
            
        except urllib.error.URLError as e:
            log.debug("Network error fetching lyrics: %s", e)
            self.ready.emit([])
        except Exception as e:
            log.error("Failed to parse lyrics: %s", e)
            self.ready.emit([])


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
