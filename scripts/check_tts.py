"""Verify edge-tts synthesis and MP3 decoding via soundfile."""
import asyncio, io, time
import edge_tts, soundfile as sf

async def main():
    t0 = time.time()
    comm = edge_tts.Communicate("Systems online. All tools are responding, sir.",
                                "en-GB-RyanNeural", rate="+8%")
    buf = io.BytesIO()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    synth = time.time() - t0
    buf.seek(0)
    data, sr = sf.read(buf, dtype="float32")
    print(f"synth {synth:.2f}s | mp3 {buf.getbuffer().nbytes} bytes")
    print(f"decoded: {data.shape} @ {sr} Hz = {len(data)/sr:.2f}s audio")
    print("MP3 decode via soundfile: OK")

asyncio.run(main())
