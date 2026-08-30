"""Verify the conversation loop: one wake word, several exchanges.

Feeds synthesised speech through the real pipeline by standing in for the
microphone, so the loop, the timeout and the farewell handling are all
exercised for real.

    uv run python scripts/check_conversation.py
"""

from __future__ import annotations

import asyncio
import io
import sys

import numpy as np

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


async def synth(text: str) -> np.ndarray:
    """Render a phrase to 16 kHz mono float32, as the recorder would return."""
    import edge_tts
    import soundfile as sf

    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
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
        audio = np.interp(np.linspace(0, len(audio) - 1, target),
                          np.arange(len(audio)), audio)
    return audio.astype(np.float32)


async def run_conversation(lines: list[str]) -> tuple[list[str], list[dict]]:
    """Drive a full pipeline turn, feeding `lines` one per listen."""
    from jarvis.bus import bus
    # The pipeline holds references to these exact singleton instances, so
    # patching the instances is enough -- no module surgery needed.
    from jarvis.voice.audio import recorder
    from jarvis.voice.pipeline import pipeline
    from jarvis.voice.tts import speaker

    clips = [await synth(line) for line in lines]
    served = {"i": 0}

    def fake_record(on_level=None, lead_in_s=None):
        index = served["i"]
        served["i"] += 1
        if index >= len(clips):
            return None          # silence -> the conversation should end
        return clips[index]

    events: list[dict] = []
    queue = bus.subscribe()

    async def drain():
        try:
            while True:
                events.append(await queue.get())
        except asyncio.CancelledError:
            pass

    collector = asyncio.create_task(drain())

    original_record = recorder.record
    original_speak = speaker.speak
    spoken: list[str] = []

    async def fake_speak(text: str) -> bool:
        spoken.append(text)
        await asyncio.sleep(0.05)     # don't wait on real audio
        return True

    recorder.record = fake_record
    speaker.speak = fake_speak
    try:
        await pipeline._turn()
        # Let the collector pick up anything published on the way out;
        # cancelling immediately would drop the closing event.
        await asyncio.sleep(0.25)
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        recorder.record = original_record
        speaker.speak = original_speak
        collector.cancel()
        bus.unsubscribe(queue)

    return spoken, events


async def main() -> int:
    from jarvis import config
    from jarvis.bus import bus
    from jarvis.llm.agent import agent
    from jarvis.llm.openrouter import client
    from jarvis.voice.stt import transcriber

    bus.bind_loop(asyncio.get_running_loop())
    config.update({"voice": {"conversation_mode": True}})
    await asyncio.to_thread(transcriber.load)

    print("\n[1] several exchanges from one activation")
    agent.reset()
    spoken, events = await run_conversation([
        "What is my CPU usage right now?",
        "And how much memory am I using?",
        "How many monitors do I have?",
    ])
    check("answered every follow-up without a new wake word",
          len(spoken) == 3, f"{len(spoken)} replies: {[s[:44] for s in spoken]}")

    convo_events = [e for e in events if e["kind"] == "conversation"]
    check("signalled that the conversation was open",
          any(e.get("active") for e in convo_events), f"{len(convo_events)} events")
    check("closed the conversation on silence",
          convo_events and convo_events[-1].get("active") is False,
          str(convo_events[-1]) if convo_events else "none")

    print("\n[2] follow-ups keep their context")
    agent.reset()
    spoken, _ = await run_conversation([
        "How many monitors do I have?",
        "What is on the second one?",
    ])
    check("resolved 'the second one' from context",
          len(spoken) == 2 and any(w in spoken[1].lower() for w in
                                   ("discord", "nzxt", "nvidia", "portrait", "cam")),
          spoken[1][:130] if len(spoken) > 1 else "no second reply")

    print("\n[3] a farewell ends it")
    agent.reset()
    spoken, events = await run_conversation([
        "What time is it?",
        "Thanks Jarvis.",
        "This should never be reached.",
    ])
    check("stopped at the farewell", len(spoken) == 2,
          f"{len(spoken)} replies: {[s[:40] for s in spoken]}")

    print("\n[4] conversation mode off means a single exchange")
    config.update({"voice": {"conversation_mode": False}})
    agent.reset()
    spoken, _ = await run_conversation([
        "What time is it?",
        "And what is my CPU usage?",
    ])
    check("only one exchange when disabled", len(spoken) == 1,
          f"{len(spoken)} replies")
    config.update({"voice": {"conversation_mode": True}})

    await client.aclose()
    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
