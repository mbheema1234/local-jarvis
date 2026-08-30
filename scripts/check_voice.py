"""Exercise the voice path without a human.

Synthesises speech with edge-tts, resamples it to what Whisper expects, and
runs it through the real transcriber -- so STT accuracy and the
speech-to-tool-call path are both verified end to end.

    uv run python scripts/check_voice.py
"""

from __future__ import annotations

import asyncio
import io
import sys
import time

import numpy as np

PHRASES = [
    "What is my CPU usage right now?",
    "Hey Jarvis, open the Epic Games Launcher and my NVIDIA app, I want to game.",
    "Set the volume to forty percent.",
    "What time is it?",
]


async def synth(text: str, voice: str = "en-US-AriaNeural") -> np.ndarray:
    """Render text to 16 kHz mono float32, the format Whisper wants."""
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
        # Linear resample is plenty for a synthetic test signal.
        target_len = int(len(audio) * 16000 / rate)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, target_len),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)
    return audio


# Whisper normalises spoken numbers to digits, which is correct output but
# would otherwise read as a mismatch against the written phrase.
_NUMBER_WORDS = {
    "zero": "0", "ten": "10", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100", "percent": "%",
}


def similarity(said: str, heard: str) -> float:
    """Word overlap, ignoring case, punctuation, and number formatting."""

    def words(text: str) -> set[str]:
        out = set()
        for word in text.lower().split():
            word = word.strip(".,?!")
            if word.endswith("%") and len(word) > 1:
                out.update({word[:-1], "%"})
                continue
            out.add(_NUMBER_WORDS.get(word, word))
        return out

    spoken = words(said)
    return len(spoken & words(heard)) / max(1, len(spoken))


async def main() -> int:
    from jarvis.bus import bus
    from jarvis.llm.agent import agent
    from jarvis.llm.openrouter import client
    from jarvis.voice.stt import transcriber

    bus.bind_loop(asyncio.get_running_loop())

    print("loading whisper...")
    started = time.time()
    await asyncio.to_thread(transcriber.load)
    print(f"loaded in {time.time() - started:.1f}s\n")

    failures = 0
    for phrase in PHRASES:
        audio = await synth(phrase)

        started = time.time()
        heard = await asyncio.to_thread(transcriber.transcribe, audio)
        stt_time = time.time() - started

        overlap = similarity(phrase, heard)

        print(f"  said  : {phrase}")
        print(f"  heard : {heard}")
        print(f"  match : {overlap:.0%}   ({len(audio)/16000:.1f}s audio, "
              f"{stt_time:.2f}s transcribe, {len(audio)/16000/stt_time:.1f}x realtime)")

        if overlap < 0.7:
            print("  ^^ POOR TRANSCRIPTION")
            failures += 1
        print()

    # Now run the gaming request through the agent, but with launching stubbed
    # out so the test doesn't actually open a game launcher.
    print("=" * 62)
    print("agent routing (app launches stubbed):\n")

    from jarvis.tools import registry

    launched: list[str] = []
    original = registry.REGISTRY["launch_apps"].func
    original_single = registry.REGISTRY["launch_app"].func

    def fake_many(names: list[str]) -> dict:
        launched.extend(names)
        return {"ok": True, "launched": names}

    def fake_one(name: str) -> dict:
        launched.append(name)
        return {"ok": True, "launched": name}

    registry.REGISTRY["launch_apps"].func = fake_many
    registry.REGISTRY["launch_app"].func = fake_one
    try:
        reply = await agent.run(
            "Hey Jarvis, open the Epic Games Launcher and my NVIDIA app, I want to game."
        )
        print(f"  tools asked to launch : {launched}")
        print(f"  reply                 : {reply}")
        ok = any("epic" in a.lower() for a in launched) and \
             any("nvidia" in a.lower() for a in launched)
        print(f"\n  {'PASS' if ok else 'FAIL'}  routed both apps from one spoken sentence")
        if not ok:
            failures += 1
    finally:
        registry.REGISTRY["launch_apps"].func = original
        registry.REGISTRY["launch_app"].func = original_single
        await client.aclose()

    print(f"\n{'=' * 62}\n  {failures} failure(s)")
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
