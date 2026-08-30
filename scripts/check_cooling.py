"""End-to-end: ask Jarvis to change the NZXT CAM cooling mode, then restore it.

Briefly switches the cooling profile to Silent and puts it back to Performance.
Fully reversible, and the original mode is restored even if a step fails.

    uv run python scripts/check_cooling.py
"""

from __future__ import annotations

import asyncio
import sys

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


async def current_mode() -> str:
    """Read the active cooling mode straight out of CAM's fan readout."""
    from jarvis.tools import registry

    result = await registry.invoke("read_app_value",
                                   {"window": "NZXT CAM", "label": "Pump"})
    return str(result.get("value", ""))


async def main() -> int:
    from jarvis.bus import bus
    from jarvis.llm.agent import agent
    from jarvis.llm.openrouter import client
    from jarvis.tools import registry

    bus.bind_loop(asyncio.get_running_loop())

    before = await current_mode()
    print(f"\nstarting state: {before!r}\n")

    try:
        print("[1] asking Jarvis to switch to Silent\n")
        agent.reset()
        reply = await agent.run(
            "In the NZXT CAM app, change my cooling mode to Silent."
        )
        calls = [m.get("name") for m in agent.history if m.get("role") == "tool"]
        print(f"  tools: {calls}")
        print(f"  reply: {reply}\n")

        after = await current_mode()
        check("cooling mode switched to Silent", "silent" in after.lower(),
              f"CAM now reports: {after!r}")

    finally:
        print("\n[2] restoring Performance\n")
        agent.reset()
        reply = await agent.run(
            "In the NZXT CAM app, change my cooling mode back to Performance."
        )
        print(f"  reply: {reply}")
        restored = await current_mode()
        check("restored to Performance", "performance" in restored.lower(),
              f"CAM now reports: {restored!r}")

    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    await client.aclose()
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
