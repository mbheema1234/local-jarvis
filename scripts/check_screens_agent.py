"""Does Jarvis behave sensibly about three monitors in conversation?

Checks two different situations: a general "what can you see" should just look
and answer, while an ambiguous instruction about "my monitor" should ask which
one, naming what is on each.

    uv run python scripts/check_screens_agent.py
"""

from __future__ import annotations

import asyncio
import sys

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


async def ask(prompt: str) -> tuple[str, list[str]]:
    from jarvis.llm.agent import agent

    agent.reset()
    reply = await agent.run(prompt)
    tools = [m.get("name") for m in agent.history if m.get("role") == "tool"]
    print(f"\n  USER   : {prompt}")
    print(f"  TOOLS  : {tools}")
    print(f"  JARVIS : {reply}\n")
    return reply, tools


async def main() -> int:
    from jarvis.bus import bus
    from jarvis.llm.openrouter import client

    bus.bind_loop(asyncio.get_running_loop())

    print("\n[1] general 'what can you see' — should just look, not interrogate")
    reply, tools = await ask("Hey Jarvis, can you see what is on my monitor screens right now?")
    check("looked at the screen", any(t in ("see_screen", "list_monitors", "describe_screen")
                                      for t in tools), str(tools))
    check("described real content rather than asking which monitor",
          len(reply) > 60 and "?" not in reply[-2:],
          "reply ends without deferring back to the user")

    print("\n[2] ambiguous instruction — should ask which monitor")
    reply, tools = await ask(
        "Jarvis, close the window that's open on my monitor.")
    asked = "?" in reply
    named = any(w in reply.lower() for w in
                ("discord", "nzxt", "firefox", "code", "nvidia", "portrait",
                 "main", "primary", "left", "right", "middle"))
    check("asked the user which monitor", asked, reply[:120])
    check("named what is on the monitors to disambiguate", named,
          "looked for app names or positions in the question")

    print("\n[3] specific monitor request")
    reply, tools = await ask("What's on my third monitor?")
    check("looked at a specific monitor",
          any(t in ("see_screen", "list_monitors") for t in tools), str(tools))
    check("mentioned what is actually there",
          any(w in reply.lower() for w in ("code", "visual studio", "terminal",
                                           "explorer", "command", "editor")),
          reply[:140])

    await client.aclose()
    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
