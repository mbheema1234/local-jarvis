"""Can the agent work out how to operate an app it was never taught about?

Read-only: it explores NZXT CAM's cooling page and reports what it finds, but
is told not to change any setting.

    uv run python scripts/check_app_control.py
"""

from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    from jarvis.bus import bus
    from jarvis.llm.agent import agent
    from jarvis.llm.openrouter import client

    bus.bind_loop(asyncio.get_running_loop())

    task = (
        "Look inside the NZXT CAM app, go to its Cooling page, and tell me what "
        "cooling modes or profiles are available there. Do not change any "
        "setting - just report what you find."
    )
    print(f"USER: {task}\n")

    reply = await agent.run(task)

    calls = [m.get("name") for m in agent.history if m.get("role") == "tool"]
    print(f"\nTOOLS USED ({len(calls)}): {calls}")
    print(f"\nJARVIS: {reply}\n")

    explored = sum(1 for c in calls if c in ("inspect_app", "click_element",
                                             "read_app_value", "wait_for_element"))
    ok = explored >= 2
    print("=" * 62)
    print(f"  {'PASS' if ok else 'FAIL'}  agent drove the app's UI "
          f"({explored} UI-automation calls)")

    await client.aclose()
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
