"""Verify the 'hey jarvis' wake word model fires on the phrase and stays quiet otherwise.

Feeds synthesised speech through the detector in realtime-sized chunks, exactly
as the live listener does.

    uv run python scripts/check_wakeword.py
"""

from __future__ import annotations

import asyncio
import io
import sys

import numpy as np

CHUNK = 1280  # 80 ms at 16 kHz — what openWakeWord expects per predict() call

SHOULD_FIRE = [
    "Hey Jarvis, open Spotify.",
    "Hey Jarvis, what's my CPU usage?",
    "Hey Jarvis.",
]
SHOULD_NOT_FIRE = [
    "Open Spotify please.",
    "The weather today is quite nice.",
    "Hey there, can you help me with something?",
]


async def synth(text: str, voice: str) -> np.ndarray:
    """Render text to 16 kHz mono int16, the format openWakeWord wants."""
    import edge_tts
    import soundfile as sf

    communicate = edge_tts.Communicate(text, voice)
    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    buffer.seek(0)

    audio, rate = sf.read(buffer, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if rate != 16000:
        target = int(len(audio) * 16000 / rate)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, target), np.arange(len(audio)), audio
        )
    # Lead-in silence so the model has context before the phrase starts.
    audio = np.concatenate([np.zeros(8000), audio, np.zeros(4000)])
    return (audio * 32767).astype(np.int16)


def peak_score(model, audio: np.ndarray, label: str) -> float:
    """Stream audio through the detector and return the highest score seen."""
    model.reset()
    best = 0.0
    for start in range(0, len(audio) - CHUNK, CHUNK):
        scores = model.predict(audio[start:start + CHUNK])
        best = max(best, scores.get(label, 0.0))
    return best


async def main() -> int:
    import openwakeword
    from openwakeword.model import Model

    print("downloading wake word models (first run only)...")
    openwakeword.utils.download_models(model_names=["hey_jarvis_v0.1"])

    model = Model(wakeword_models=["hey_jarvis_v0.1"], inference_framework="onnx")
    label = list(model.models.keys())[0]
    print(f"loaded model: {label}\n")

    threshold = 0.5
    failures = 0

    # Two voices, so we're not tuning to one speaker's timbre.
    for voice in ("en-US-AriaNeural", "en-GB-RyanNeural"):
        print(f"--- voice: {voice} ---")
        for phrase in SHOULD_FIRE:
            score = peak_score(model, await synth(phrase, voice), label)
            ok = score >= threshold
            print(f"  {'PASS' if ok else 'FAIL'}  fires    {score:.3f}  {phrase!r}")
            failures += 0 if ok else 1

        for phrase in SHOULD_NOT_FIRE:
            score = peak_score(model, await synth(phrase, voice), label)
            ok = score < threshold
            print(f"  {'PASS' if ok else 'FAIL'}  quiet    {score:.3f}  {phrase!r}")
            failures += 0 if ok else 1
        print()

    print(f"{'=' * 58}\n  {failures} failure(s)")
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
