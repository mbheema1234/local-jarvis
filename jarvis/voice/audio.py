"""Microphone capture with energy-based endpointing.

Recording starts the moment you activate Jarvis and stops on its own once you
stop talking, so there is nothing to hold down and nothing to release.
"""

from __future__ import annotations

import threading
from typing import Callable

import numpy as np
import sounddevice as sd

from ..bus import bus
from ..config import load
from ..log import get
from .devices import DeviceUnavailable, invalidate, list_inputs, resolve

log = get("jarvis.voice.audio")

BLOCK_MS = 30


class Recorder:
    def __init__(self) -> None:
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Abort an in-progress recording."""
        self._cancel.set()

    def list_devices(self) -> list[dict]:
        selected = ""
        try:
            selected = resolve().name
        except DeviceUnavailable:
            pass
        return [{**d, "selected": d["name"] == selected} for d in list_inputs()]

    def record(
        self,
        on_level: Callable[[float], None] | None = None,
        lead_in_s: float | None = None,
    ) -> np.ndarray | None:
        """Record one utterance. Returns mono float32 audio, or None if aborted.

        Stops after ``silence_duration_s`` of quiet once speech has been
        detected, or at ``max_utterance_s`` regardless.

        ``lead_in_s`` is how long to wait for speech to begin before giving up.
        Mid-conversation this is longer, so a pause to think does not end the
        conversation.
        """
        cfg = load().voice
        self._cancel.clear()

        rate = cfg.sample_rate
        block = int(rate * BLOCK_MS / 1000)
        silence_blocks = max(1, int(cfg.silence_duration_s * 1000 / BLOCK_MS))
        max_blocks = int(cfg.max_utterance_s * 1000 / BLOCK_MS)
        # Don't end the utterance on the natural pause before someone starts.
        wait = cfg.lead_in_s if lead_in_s is None else lead_in_s
        lead_in_blocks = int(wait * 1000 / BLOCK_MS)

        frames: list[np.ndarray] = []
        quiet_run = 0
        speech_started = False

        try:
            device = resolve(rate)
        except DeviceUnavailable as exc:
            log.error("%s", exc)
            bus.publish("error", text=str(exc))
            return None

        try:
            stream = sd.InputStream(
                samplerate=rate,
                channels=1,
                dtype="float32",
                blocksize=block,
                device=device.index,
            )
        except Exception as exc:
            # The device list may have shifted under us; force a re-resolve so
            # the next attempt doesn't reuse a stale index.
            invalidate()
            log.error("could not open %s: %s", device, exc)
            bus.publish("error", text=f"Could not open {device.name}: {exc}")
            return None

        with stream:
            for index in range(max_blocks):
                if self._cancel.is_set():
                    log.info("recording cancelled")
                    return None
                try:
                    data, overflowed = stream.read(block)
                except Exception as exc:
                    log.error("audio read failed: %s", exc)
                    break
                if overflowed:
                    log.debug("input overflow")

                mono = data[:, 0].copy()
                frames.append(mono)

                level = float(np.sqrt(np.mean(mono**2)))
                if on_level is not None:
                    on_level(level)

                if level >= cfg.silence_threshold:
                    speech_started = True
                    quiet_run = 0
                else:
                    quiet_run += 1

                if speech_started and quiet_run >= silence_blocks:
                    log.debug("endpoint after %d blocks", index)
                    break
                if not speech_started and index >= lead_in_blocks:
                    log.info("no speech detected")
                    return None

        if not frames:
            return None

        audio = np.concatenate(frames)
        duration = len(audio) / rate
        if duration < cfg.min_utterance_s or not speech_started:
            log.info("utterance too short (%.2fs)", duration)
            return None

        peak = float(np.max(np.abs(audio)))
        if peak > 0:
            # Normalise to a consistent level; Whisper is noticeably more
            # accurate on quiet mics after this.
            audio = audio * min(0.95 / peak, 8.0)
        return audio.astype(np.float32)


recorder = Recorder()
