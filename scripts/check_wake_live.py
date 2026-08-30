"""Acoustic end-to-end test: play "Hey Jarvis" aloud and see if the mic catches it.

This is the only check that exercises the real signal path -- speaker, room,
microphone, detector. A failure here can be acoustic (mic gain, speaker volume,
echo cancellation) rather than a code fault, so read it as a tuning signal.

    uv run python scripts/check_wake_live.py [port]
"""

from __future__ import annotations

import asyncio
import io
import json
import sys

import httpx
import websockets

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
BASE = f"http://127.0.0.1:{PORT}"


async def say_aloud(text: str) -> None:
    """Speak a phrase through the default output device."""
    import edge_tts
    import sounddevice as sd
    import soundfile as sf

    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    buffer.seek(0)
    audio, rate = sf.read(buffer, dtype="float32")

    sd.play(audio, rate)
    await asyncio.to_thread(sd.wait)


async def main() -> int:
    async with httpx.AsyncClient(timeout=20) as client:
        state = (await client.get(f"{BASE}/api/wake")).json()
        if not state.get("listening"):
            print("  wake listener is not armed; nothing to test.")
            return 1
        print(f"  listener armed ({state['model']})")

        volume = (await client.post(f"{BASE}/api/tool",
                                    json={"name": "get_volume", "args": {}})).json()
        print(f"  output volume: {volume.get('level')}%"
              f"{'  (muted!)' if volume.get('muted') else ''}\n")

        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws") as socket:
            await socket.recv()  # replay frame

            print('  speaking "Hey Jarvis" out loud...')
            await say_aloud("Hey Jarvis.")

            detected = False
            try:
                async with asyncio.timeout(12):
                    while True:
                        event = json.loads(await socket.recv())
                        if event.get("kind") == "wake_detected":
                            detected = True
                            break
                        if event.get("kind") == "state" and event.get("state") == "listening":
                            detected = True
                            break
            except (asyncio.TimeoutError, TimeoutError):
                pass

        # Don't leave it mid-turn waiting on a voice that isn't coming.
        await client.post(f"{BASE}/api/cancel", json={})

        if detected:
            print("\n  PASS  the microphone heard the wake word and Jarvis activated")
            return 0
        print("\n  NOT DETECTED — the phrase never reached the mic loudly enough.")
        print("  This is usually acoustic, not a code fault. Try:")
        print("    - raise output volume, or say it yourself into the mic")
        print("    - lower voice.wake_threshold (currently 0.5) in Settings")
        print("    - check the input device under Settings")
        return 1


sys.exit(asyncio.run(main()))
