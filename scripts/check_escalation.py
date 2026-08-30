"""Verify the agent escalates to the fallback model when it stalls.

Forces a stall by shrinking the tool budget to one round, then checks the
handover actually happened and the turn still finished.

    uv run python scripts/check_escalation.py
"""

from __future__ import annotations

import asyncio
import sys

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


async def collect(coro):
    """Run a turn while capturing bus events."""
    from jarvis.bus import bus

    events: list[dict] = []
    queue = bus.subscribe()

    async def drain():
        try:
            while True:
                events.append(await queue.get())
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(drain())
    try:
        result = await coro
        await asyncio.sleep(0.25)
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        task.cancel()
        bus.unsubscribe(queue)
    return result, events


async def main() -> int:
    from jarvis import config
    from jarvis.bus import bus
    from jarvis.llm.agent import agent
    from jarvis.llm.openrouter import client

    bus.bind_loop(asyncio.get_running_loop())

    settings = config.load()
    print(f"\n  primary  : {settings.models.agent}")
    print(f"  fallback : {settings.models.fallback}\n")

    print("[1] normal turn stays on the primary model")
    agent.reset()
    reply, events = await collect(agent.run("What is my CPU usage right now?"))
    check("answered without escalating",
          not any(e["kind"] == "escalated" for e in events), reply[:110])

    print("\n[2] a stalled turn hands over to the fallback")
    # One round is not enough to look at the screen and then answer, so the
    # agent is guaranteed to still be mid-tool-call when the budget runs out.
    original = settings.models.max_tool_iterations
    config.update({"models": {"max_tool_iterations": 1}})
    try:
        agent.reset()
        reply, events = await collect(agent.run(
            "Look at my second monitor and tell me exactly which applications "
            "are open on it."))
        escalations = [e for e in events if e["kind"] == "escalated"]
        check("escalation fired", bool(escalations),
              str(escalations[0]) if escalations else "none")
        check("escalated to the configured fallback",
              bool(escalations) and escalations[0]["model"] == settings.models.fallback,
              escalations[0]["model"] if escalations else "")
        check("still produced a real answer",
              len(reply) > 30 and "stuck" not in reply.lower(), reply[:150])
    finally:
        config.update({"models": {"max_tool_iterations": original}})

    print("\n[3] escalation can be turned off")
    config.update({"models": {"max_tool_iterations": 1, "escalate_on_stuck": False}})
    try:
        agent.reset()
        reply, events = await collect(agent.run(
            "Look at my second monitor and list every application open on it."))
        check("no escalation when disabled",
              not any(e["kind"] == "escalated" for e in events), reply[:90])
    finally:
        config.update({"models": {"max_tool_iterations": original,
                                  "escalate_on_stuck": True}})

    await client.aclose()
    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
