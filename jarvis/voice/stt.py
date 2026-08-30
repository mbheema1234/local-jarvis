"""Speech recognition via faster-whisper, running entirely on this machine.

Nothing spoken to Jarvis is uploaded for transcription -- the model runs
locally, which is the whole point of a local assistant.
"""

from __future__ import annotations

import threading
import time

import numpy as np

from ..config import ROOT, load
from ..log import get

log = get("jarvis.voice.stt")

MODELS_DIR = ROOT / "models"
MODELS_DIR.mkdir(exist_ok=True)


class Transcriber:
    def __init__(self) -> None:
        self._model = None
        self._model_name: str | None = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Load the Whisper model, downloading it on first use."""
        cfg = load().voice
        with self._lock:
            if self._model is not None and self._model_name == cfg.stt_model:
                return

            from faster_whisper import WhisperModel

            device = cfg.stt_device
            compute = cfg.stt_compute_type
            if device == "auto":
                device, compute = self._pick_device(compute)

            log.info("loading whisper %r on %s (%s)", cfg.stt_model, device, compute)
            started = time.time()
            try:
                self._model = WhisperModel(
                    cfg.stt_model,
                    device=device,
                    compute_type=compute,
                    download_root=str(MODELS_DIR),
                )
            except Exception as exc:
                if device == "cuda":
                    log.warning("CUDA load failed (%s); falling back to CPU", exc)
                    self._model = WhisperModel(
                        cfg.stt_model,
                        device="cpu",
                        compute_type="int8",
                        download_root=str(MODELS_DIR),
                    )
                else:
                    raise
            self._model_name = cfg.stt_model
            log.info("whisper ready in %.1fs", time.time() - started)

    @staticmethod
    def _pick_device(preferred_compute: str) -> tuple[str, str]:
        """Use CUDA when cuDNN is actually present, otherwise CPU."""
        try:
            import ctranslate2

            if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
                return "cuda", "float16"
        except Exception:
            pass
        return "cpu", preferred_compute

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe mono 16 kHz float32 audio to text."""
        if self._model is None:
            self.load()
        assert self._model is not None

        started = time.time()
        segments, info = self._model.transcribe(
            audio,
            language="en",
            beam_size=1,           # greedy: much faster, negligible accuracy loss here
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,  # stops runaway repetition
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        log.info(
            "transcribed %.1fs of audio in %.2fs: %r",
            len(audio) / 16000, time.time() - started, text[:80],
        )
        return text


transcriber = Transcriber()
