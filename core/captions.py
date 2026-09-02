"""
core/captions.py — Real-time audio loopback capture and automatic speech-to-text captions.

When LrcLib does not have synced lyrics for the current media (e.g. indie tracks,
YouTube videos, podcasts, movies), this engine captures the system audio output
via WASAPI loopback and generates live captions using a local, quantized Whisper model.
The spoken or sung language is automatically detected (no manual language choice needed).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

log = logging.getLogger(__name__)

# Model name: 'tiny' is ~75 MB, runs ultra-fast on CPU with int8 quantization
_MODEL_NAME = "tiny"
_SAMPLE_RATE = 16000
_CHUNK_SECONDS = 2.5


class _CaptionWorker(QObject):
    """Background worker that records loopback audio and runs transcription."""

    caption_ready = pyqtSignal(str, str)  # (text, detected_language)
    status_changed = pyqtSignal(str)      # (status message)

    def __init__(self, cfg: Any = None) -> None:
        super().__init__()
        self._cfg = cfg
        self._running = False
        self._active = False
        self._model = None
        self._model_name = ""
        self._lock = threading.Lock()

    def start_captions(self) -> None:
        with self._lock:
            self._active = True

    def stop_captions(self) -> None:
        with self._lock:
            self._active = False

    def shutdown(self) -> None:
        self._running = False
        self._active = False

    def run(self) -> None:
        self._running = True
        log.info("AutoCaption worker started")

        while self._running:
            if not self._active:
                time.sleep(0.3)
                continue

            try:
                self._ensure_model()
                self._record_and_transcribe()
            except Exception as e:
                log.debug("AutoCaption iteration notice: %s", e)
                time.sleep(0.5)

        log.info("AutoCaption worker stopped")

    def _ensure_model(self) -> None:
        target_model = self._cfg.get("captions_whisper_model", "base") if self._cfg else "base"
        if self._model is None or self._model_name != target_model:
            self.status_changed.emit(f"Loading Whisper AI ({target_model})…")
            from faster_whisper import WhisperModel
            self._model = WhisperModel(target_model, device="cpu", compute_type="int8")
            self._model_name = target_model
            self.status_changed.emit("Speech AI ready")
            log.info("Whisper '%s' model loaded successfully", target_model)

    def _record_and_transcribe(self) -> None:
        try:
            import soundcard as sc
            speaker = sc.default_speaker()
            if not speaker:
                time.sleep(1.0)
                return
            loopback_mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            if not loopback_mic:
                mics = sc.all_microphones(include_loopback=True)
                loopbacks = [m for m in mics if getattr(m, 'isloopback', False)]
                if loopbacks:
                    loopback_mic = loopbacks[0]
                else:
                    time.sleep(1.0)
                    return
        except Exception as e:
            log.debug("Loopback mic acquisition error: %s", e)
            time.sleep(1.0)
            return

        num_frames = int(_SAMPLE_RATE * _CHUNK_SECONDS)

        with loopback_mic.recorder(samplerate=_SAMPLE_RATE, channels=1) as mic:
            while self._running and self._active:
                data = mic.record(numframes=num_frames)
                audio_1d = data.squeeze().astype(np.float32)

                # Check energy/RMS: skip silent frames
                rms = np.sqrt(np.mean(audio_1d**2))
                if rms < 0.005:
                    continue

                if not self._active or not self._running:
                    break

                # Determine language
                lang_mode = self._cfg.get("captions_lang_mode", "auto") if self._cfg else "auto"
                target_lang = None
                if lang_mode == "manual":
                    target_lang = self._cfg.get("captions_manual_lang", "en") if self._cfg else "en"

                # Transcribe
                segments, info = self._model.transcribe(
                    audio_1d,
                    language=target_lang,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False,
                )

                detected_lang = info.language.upper() if info and info.language else (target_lang.upper() if target_lang else "AUTO")
                collected_text = " ".join(s.text.strip() for s in segments if s.text.strip())

                if collected_text:
                    self.caption_ready.emit(collected_text, detected_lang)


class AutoCaptionEngine(QObject):
    """High-level controller managing the caption worker thread."""

    caption_ready = pyqtSignal(str, str)  # (text, detected_language)
    status_changed = pyqtSignal(str)

    def __init__(self, cfg: Any = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._thread = QThread()
        self._worker = _CaptionWorker(cfg)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.caption_ready.connect(self.caption_ready.emit)
        self._worker.status_changed.connect(self.status_changed.emit)

        self._thread.start()

    def start(self) -> None:
        self._worker.start_captions()

    def stop(self) -> None:
        self._worker.stop_captions()

    def shutdown(self) -> None:
        self._worker.shutdown()
        self._thread.quit()
        self._thread.wait(2000)
