"""Confirm the cleaned text is what actually reaches the speech engine.

Intercepts edge-tts to capture the exact string handed to it, then optionally
speaks a before/after pair aloud so the difference is audible.

    uv run python scripts/check_speech_audio.py [--listen]
"""

from __future__ import annotations

import asyncio
import sys

LISTEN = "--listen" in sys.argv

SAMPLE = ("**Done!** I've set your cooling to *Silent* mode — pump is now at "
          "~2400 RPM. See `NZXT CAM` → Cooling or https://nzxt.com/cam 🎉")


async def main() -> int:
    import edge_tts

    from jarvis.voice.speech_text import for_speech
    from jarvis.voice.tts import speaker

    captured: list[str] = []
    original = edge_tts.Communicate

    class Spy(original):  # type: ignore[misc, valid-type]
        def __init__(self, text, *args, **kwargs):
            captured.append(text)
            super().__init__(text, *args, **kwargs)

    edge_tts.Communicate = Spy
    try:
        await speaker.speak(SAMPLE)
    finally:
        edge_tts.Communicate = original

    if not captured:
        print("  FAIL  the speech engine was never called")
        return 1

    handed_over = captured[0]
    print(f"\n  model wrote : {SAMPLE!r}")
    print(f"\n  engine spoke: {handed_over!r}\n")

    problems = [ch for ch in ("**", "*", "`", "→", "~", "🎉", "http", "##")
                if ch in handed_over]
    if problems:
        print(f"  FAIL  these reached the speech engine: {problems}")
        return 1
    print("  PASS  no markdown, symbols, URLs or emoji reached the speech engine")

    if handed_over != for_speech(SAMPLE):
        print("  FAIL  speaker did not use the cleaned text")
        return 1
    print("  PASS  speaker used the cleaned text")

    if LISTEN:
        print("\n  speaking the raw version, then the cleaned one...")
        comm = original(SAMPLE, "en-GB-RyanNeural")
        import io

        import sounddevice as sd
        import soundfile as sf

        buf = io.BytesIO()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        buf.seek(0)
        data, rate = sf.read(buf, dtype="float32")
        sd.play(data, rate)
        await asyncio.to_thread(sd.wait)

        await asyncio.sleep(0.6)
        await speaker.speak(SAMPLE)

    return 0


sys.exit(asyncio.run(main()))
