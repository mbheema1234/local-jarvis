"""Always-on "Hey Jarvis" detection.

openWakeWord ships a pretrained model for this exact phrase. It runs locally on
CPU against a small sliding window of audio -- no audio is recorded, buffered to
disk, or sent anywhere. Only once the phrase fires does the recorder start, and
even then transcription happens on this machine.

The listener owns the microphone while armed, so it releases it (closes the
stream entirely) whenever a turn is in progress. That avoids fighting the
recorder for the device and stops Jarvis from hearing its own replies.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from ..bus import bus
from ..config import ROOT, load
from ..log import get
from .devices import DeviceUnavailable, invalidate, resolve

log = get("jarvis.voice.wake")

# openWakeWord expects 80 ms of 16 kHz mono int16 per prediction.
SAMPLE_RATE = 16000
CHUNK = 1280

MODELS_DIR = ROOT / "models" / "wakeword"


class WakeListener:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._armed = threading.Event()
        self._model = None
        self._label = ""
        self._last_fire = 0.0
        # Log the "mic is missing" message once per outage, not every retry.
        self._warned_missing = False
        self.available = False
        self.last_score = 0.0

    # -- lifecycle ---------------------------------------------------------

    def _load_model(self) -> bool:
        if self._model is not None:
            return True
        cfg = load().voice
        try:
            import openwakeword
            from openwakeword.model import Model

            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            # Idempotent: skips anything already downloaded.
            openwakeword.utils.download_models(model_names=[cfg.wake_model])

            self._model = Model(
                wakeword_models=[cfg.wake_model],
                inference_framework="onnx",
            )
            self._label = next(iter(self._model.models))
            self.available = True
            log.info("wake word model loaded: %s", self._label)
            return True
        except Exception as exc:
            log.error("could not load wake word model: %s", exc)
            self.available = False
            return False

    def start(self, on_wake: Callable[[], None]) -> bool:
        """Begin listening for the wake phrase in a background thread.

        Idempotent: if the thread is already running but paused (muted, or
        mid-turn), this re-arms it rather than returning a listener that never
        actually listens again.
        """
        if self._thread is not None and self._thread.is_alive():
            self.resume()
            return True
        if not self._load_model():
            return False

        self._running.set()
        self._armed.set()
        self._thread = threading.Thread(
            target=self._run, args=(on_wake,), daemon=True, name="wakeword"
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running.clear()
        self._armed.clear()

    def pause(self) -> None:
        """Release the microphone (during a turn, or when muted)."""
        self._armed.clear()

    def resume(self) -> None:
        if self._running.is_set():
            self._armed.set()

    @property
    def listening(self) -> bool:
        return (
            self._running.is_set()
            and self._armed.is_set()
            and self._thread is not None
            and self._thread.is_alive()
        )

    # -- the loop ----------------------------------------------------------

    def _run(self, on_wake: Callable[[], None]) -> None:
        import sounddevice as sd

        while self._running.is_set():
            if not self._armed.is_set():
                time.sleep(0.12)
                continue

            cfg = load().voice
            try:
                device = resolve(SAMPLE_RATE)
            except DeviceUnavailable as exc:
                # Never fall back to a different microphone: if the pinned mic
                # is gone, wait for it to come back instead.
                if not self._warned_missing:
                    log.error("%s", exc)
                    bus.publish("error", text=str(exc))
                    bus.publish("wake_state", listening=False, available=self.available)
                    self._warned_missing = True
                time.sleep(5.0)
                continue

            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="int16",
                    blocksize=CHUNK,
                    device=device.index,
                )
            except Exception as exc:
                invalidate()
                log.error("wake listener could not open %s: %s", device, exc)
                time.sleep(3.0)
                continue

            if self._warned_missing:
                log.info("microphone is back")
                bus.publish("wake_state", listening=True, available=self.available)
                self._warned_missing = False

            log.info("listening for '%s' on %s", cfg.wake_model, device)
            try:
                with stream:
                    if self._model is not None:
                        self._model.reset()
                    while self._running.is_set() and self._armed.is_set():
                        try:
                            data, _ = stream.read(CHUNK)
                        except Exception as exc:
                            log.warning("wake audio read failed: %s", exc)
                            break

                        scores = self._model.predict(np.asarray(data[:, 0]))
                        score = float(scores.get(self._label, 0.0))
                        self.last_score = score

                        if score < cfg.wake_threshold:
                            continue
                        if time.time() - self._last_fire < cfg.wake_cooldown_s:
                            continue

                        self._last_fire = time.time()
                        log.info("wake word detected (%.3f)", score)
                        # Release the mic before the recorder wants it.
                        self._armed.clear()
                        try:
                            on_wake()
                        except Exception:
                            log.exception("wake callback failed")
                        break
            except Exception as exc:
                log.error("wake listener stopped: %s", exc)
                time.sleep(1.0)

        log.info("wake listener exited")


wake = WakeListener()
