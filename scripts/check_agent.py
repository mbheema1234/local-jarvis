"""End-to-end: real OpenRouter call driving real tools (read-only ones)."""
import asyncio, sys
from jarvis.bus import bus
from jarvis.llm.agent import agent
from jarvis.llm.openrouter import client

def watch(kind, **kw):
    pass

async def main():
    bus.bind_loop(asyncio.get_running_loop())
    info = await client.check_key()
    print(f"key ok | limit=${info.get('limit')} used=${info.get('usage')}\n")

    for prompt in [
        "What's my CPU and memory usage right now?",
        "Is the Epic Games Launcher installed on this PC? Don't open it, just check.",
    ]:
        print(f"USER: {prompt}")
        reply = await agent.run(prompt)
        calls = [m.get("name") for m in agent.history if m.get("role") == "tool"]
        print(f"TOOLS: {calls}")
        print(f"JARVIS: {reply}\n")
    await client.aclose()

asyncio.run(main())
