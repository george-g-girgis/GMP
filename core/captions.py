"""
core/captions.py — Real-time audio loopback capture and automatic speech-to-text captions.

When LrcLib does not have synced lyrics for the current media (e.g. indie tracks,
YouTube videos, podcasts, movies), this engine captures the system audio output
via WASAPI loopback and generates live captions using a local, quantized Whisper model.
The spoken or sung language is automatically detected (no manual language choice needed).
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time
from typing import Any

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

log = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_WINDOW_SECONDS = 4.0        # 4-second audio context window
_CHUNK_SECONDS = 0.25        # 250ms capture slices for zero-drop loopback
_WINDOW_SAMPLES = int(_SAMPLE_RATE * _WINDOW_SECONDS)
_CHUNK_SAMPLES = int(_SAMPLE_RATE * _CHUNK_SECONDS)


class _AudioCaptureWorker:
    """Dedicated thread for non-blocking loopback audio acquisition."""

    def __init__(self, buffer: collections.deque, lock: threading.Lock) -> None:
        self._buffer = buffer
        self._lock = lock
        self._running = False
        self._active = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._active = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="GMPAudioCapture")
        self._thread.start()

    def set_active(self, active: bool) -> None:
        self._active = active

    def stop(self) -> None:
        self._running = False
        self._active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        import warnings
        try:
            import soundcard as sc
        except ModuleNotFoundError:
            log.warning("soundcard not installed — AI captions disabled. Run: pip install soundcard")
            return

        # Suppress WASAPI loopback buffer overflow warnings — our ring buffer
        # design tolerates occasional frame drops without quality impact.
        try:
            warnings.filterwarnings("ignore", message="data discontinuity", category=sc.mediafoundation.SoundcardRuntimeWarning)
        except Exception:
            pass  # Not all platforms expose SoundcardRuntimeWarning

        mic = None
        while self._running:
            if not self._active:
                time.sleep(0.3)
                continue

            # Obtain loopback microphone
            if mic is None:
                try:
                    speaker = sc.default_speaker()
                    if speaker:
                        mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
                    if not mic:
                        mics = sc.all_microphones(include_loopback=True)
                        loopbacks = [m for m in mics if getattr(m, 'isloopback', False)]
                        if loopbacks:
                            mic = loopbacks[0]
                except Exception as e:
                    log.debug("Loopback mic acquisition retry: %s", e)
                    time.sleep(1.0)
                    continue

            if not mic:
                time.sleep(1.0)
                continue

            try:
                with mic.recorder(samplerate=_SAMPLE_RATE, channels=1) as recorder:
                    while self._running and self._active:
                        data = recorder.record(numframes=_CHUNK_SAMPLES)
                        samples = data.squeeze().astype(np.float32)
                        with self._lock:
                            self._buffer.extend(samples)
            except Exception as exc:
                log.debug("Loopback capture notice: %s", exc)
                mic = None
                time.sleep(0.5)


class _CaptionWorker(QObject):
    """Background worker that analyzes buffered audio and runs VAD-filtered Whisper transcription."""

    caption_ready = pyqtSignal(str, str)  # (text, detected_language)
    status_changed = pyqtSignal(str)      # (status message)

    def __init__(self, cfg: Any = None) -> None:
        super().__init__()
        self._cfg = cfg
        self._running = False
        self._active = False
        self._model = None
        self._model_name = ""
        self._last_caption = ""

        self._lock = threading.Lock()
        self._audio_buffer: collections.deque = collections.deque(maxlen=_WINDOW_SAMPLES)
        self._capture = _AudioCaptureWorker(self._audio_buffer, self._lock)

    def start_captions(self) -> None:
        self._active = True
        self._capture.set_active(True)

    def stop_captions(self) -> None:
        self._active = False
        self._capture.set_active(False)
        with self._lock:
            self._audio_buffer.clear()
        self._last_caption = ""

    def shutdown(self) -> None:
        self._running = False
        self._active = False
        self._capture.stop()

    def run(self) -> None:
        self._running = True
        self._capture.start()
        log.info("AutoCaption worker started with dedicated capture thread")

        while self._running:
            if not self._active:
                time.sleep(0.3)
                continue

            try:
                self._ensure_model()
                self._transcribe_cycle()
            except Exception as e:
                log.debug("AutoCaption cycle notice: %s", e)
                time.sleep(0.5)

        self._capture.stop()
        log.info("AutoCaption worker stopped")

    def _ensure_model(self) -> None:
        target_model = self._cfg.get("captions_whisper_model", "tiny") if self._cfg else "tiny"
        if self._model is None or self._model_name != target_model:
            self.status_changed.emit(f"Loading Whisper AI ({target_model})…")
            from faster_whisper import WhisperModel

            threads = max(2, min(os.cpu_count() or 4, 8))
            self._model = WhisperModel(
                target_model,
                device="cpu",
                compute_type="int8",
                cpu_threads=threads,
            )
            self._model_name = target_model
            self.status_changed.emit("Speech AI ready")
            log.info("Whisper '%s' model loaded successfully (%d threads)", target_model, threads)

    def _transcribe_cycle(self) -> None:
        # Wait until buffer has enough audio context
        min_samples = int(_SAMPLE_RATE * 2.0)
        with self._lock:
            if len(self._audio_buffer) < min_samples:
                time.sleep(0.3)
                return
            audio = np.array(self._audio_buffer, dtype=np.float32)

        # Check RMS energy to skip silence or near-silence
        rms = np.sqrt(np.mean(audio**2))
        if rms < 0.005:
            time.sleep(0.4)
            return

        # Determine target language
        lang_mode = self._cfg.get("captions_lang_mode", "auto") if self._cfg else "auto"
        target_lang = None
        if lang_mode == "manual":
            target_lang = self._cfg.get("captions_manual_lang", "en") if self._cfg else "en"

        # Transcribe with Silero VAD to reject background music and isolate singing/speech
        segments, info = self._model.transcribe(
            audio,
            language=target_lang,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=400,
                speech_pad_ms=200,
            ),
            beam_size=2,
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )

        detected_lang = info.language.upper() if info and info.language else (target_lang.upper() if target_lang else "AUTO")
        collected_text = " ".join(s.text.strip() for s in segments if s.text.strip())

        if collected_text and collected_text != self._last_caption:
            self._last_caption = collected_text
            self.caption_ready.emit(collected_text, detected_lang)

        # Pace the transcription loop (1.5s cadence — balances responsiveness vs CPU)
        time.sleep(1.5)


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
