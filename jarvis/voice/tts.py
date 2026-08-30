"""Speech synthesis.

Two backends: Microsoft's neural voices via edge-tts (much better sounding,
needs a connection) and Windows SAPI via pyttsx3 (fully offline). Playback is
interruptible so you can talk over Jarvis mid-sentence.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time

import numpy as np
import sounddevice as sd

from ..bus import bus
from ..config import load
from ..log import get
from .speech_text import for_speech

log = get("jarvis.voice.tts")


class Speaker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.speaking = False

    def stop(self) -> None:
        """Interrupt whatever is currently being spoken."""
        self._stop.set()
        try:
            sd.stop()
        except Exception:
            pass

    async def speak(self, text: str) -> bool:
        """Speak ``text``. Returns False if it was interrupted or failed.

        The text is rewritten for the ear first: markdown, URLs, paths and
        symbols are read out literally by every engine otherwise. The original
        wording still reaches the dashboard untouched.
        """
        spoken = for_speech(text or "")
        if not spoken or not load().voice.tts_enabled:
            return False
        if spoken != (text or "").strip():
            log.debug("speaking cleaned text: %r", spoken[:100])
        text = spoken

        self._stop.clear()
        self.speaking = True
        try:
            if load().voice.tts_engine == "edge":
                ok = await self._speak_edge(text)
                if ok:
                    return True
                log.info("edge-tts unavailable; falling back to offline SAPI")
            return await asyncio.to_thread(self._speak_sapi, text)
        finally:
            self.speaking = False

    # -- backends ----------------------------------------------------------

    async def _speak_edge(self, text: str) -> bool:
        cfg = load().voice
        try:
            import edge_tts
            import soundfile as sf

            communicate = edge_tts.Communicate(text, cfg.tts_voice, rate=cfg.tts_rate)
            buffer = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buffer.write(chunk["data"])
                if self._stop.is_set():
                    return True  # interrupted, but not a backend failure

            if buffer.tell() == 0:
                return False
            buffer.seek(0)
            audio, rate = sf.read(buffer, dtype="float32")
        except Exception as exc:
            log.warning("edge-tts failed: %s", exc)
            return False

        await asyncio.to_thread(self._play, audio, rate)
        return True

    def _play(self, audio: np.ndarray, rate: int) -> None:
        """Play audio, publishing its loudness as it goes.

        The level drives the mouth on screen, so it opens on the actual sounds
        being spoken rather than flapping on a timer.
        """
        mono = audio if audio.ndim == 1 else audio.mean(axis=1)

        # Loudness envelope in 40 ms windows, computed once up front.
        window = max(1, int(rate * 0.04))
        frames = len(mono) // window
        envelope = [
            float(np.sqrt(np.mean(mono[i * window:(i + 1) * window] ** 2)))
            for i in range(frames)
        ]
        peak = max(envelope, default=0.0) or 1.0

        with self._lock:
            try:
                sd.play(audio, rate)
                started = time.monotonic()
                emitted = -1

                # Poll rather than sd.wait() so stop() takes effect promptly.
                while sd.get_stream().active:
                    if self._stop.is_set():
                        sd.stop()
                        break
                    index = int((time.monotonic() - started) / 0.04)
                    if index != emitted and 0 <= index < frames:
                        emitted = index
                        bus.publish("speak_level",
                                    level=round(envelope[index] / peak, 3))
                    threading.Event().wait(0.02)
            except Exception as exc:
                log.warning("playback failed: %s", exc)
            finally:
                bus.publish("speak_level", level=0.0)

    def _speak_sapi(self, text: str) -> bool:
        if self._stop.is_set():
            return False
        try:
            import pyttsx3

            # A fresh engine per utterance: pyttsx3's run loop is not reusable
            # across threads once it has been driven to completion.
            engine = pyttsx3.init()
            engine.setProperty("rate", 185)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
            return True
        except Exception as exc:
            log.error("SAPI speech failed: %s", exc)
            return False

    @staticmethod
    async def list_voices() -> list[dict]:
        """Available edge-tts English voices, for the settings panel."""
        try:
            import edge_tts

            voices = await edge_tts.list_voices()
        except Exception:
            return []
        return sorted(
            (
                {"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
                for v in voices
                if v["Locale"].startswith("en-")
            ),
            key=lambda v: v["name"],
        )


speaker = Speaker()
