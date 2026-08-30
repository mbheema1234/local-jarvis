"""Verify Jarvis actually searches the internet rather than answering from memory.

The questions are chosen to be unanswerable from a stale model: they concern
events after training, or facts that change. If the agent answers them
correctly, it searched.

    uv run python scripts/check_search.py
"""

from __future__ import annotations

import asyncio
import sys

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


async def main() -> int:
    from jarvis.bus import bus
    from jarvis.llm.agent import agent
    from jarvis.llm.openrouter import client
    from jarvis.tools import registry

    bus.bind_loop(asyncio.get_running_loop())

    print("\n[1] raw search tools\n")

    result = await registry.invoke("search_web", {"query": "Super Bowl LX winner", "max_results": 3})
    check("search_web returns results",
          result.get("ok") and result.get("count", 0) > 0,
          (result.get("results") or [{}])[0].get("title", "")[:70])

    result = await registry.invoke("search_news", {"query": "nvidia", "max_results": 3})
    check("search_news returns dated articles",
          result.get("ok") and result.get("count", 0) > 0,
          f"{(result.get('results') or [{}])[0].get('date','')} "
          f"{(result.get('results') or [{}])[0].get('title','')[:60]}")

    result = await registry.invoke("fetch_url", {"url": "https://example.com"})
    check("fetch_url reads a page",
          result.get("ok") and "Example Domain" in (result.get("content") or ""),
          result.get("title", ""))

    print("\n[2] the agent searches instead of guessing\n")

    # Both of these fall after the assistant model's training cutoff, so a
    # correct answer can only come from a live search.
    probes = [
        ("Who won Super Bowl LX and what was the score?",
         lambda r: "seahawks" in r.lower() and "29" in r),
        ("What is the current weather in West Lafayette, Indiana?",
         lambda r: any(w in r.lower() for w in
                       ("degree", "°", "cloud", "rain", "sun", "clear", "fahrenheit", "humid"))),
    ]

    for question, verify in probes:
        agent.reset()
        reply = await agent.run(question)
        used = [m.get("name") for m in agent.history if m.get("role") == "tool"]
        searched = any(t in ("search_web", "search_news", "research", "fetch_url") for t in used)
        check(f"searched for: {question[:44]}...", searched, f"tools: {used}")
        check("  and answered correctly", verify(reply), reply[:150])

    print("\n[3] research tool (Perplexity Sonar, paid)\n")
    result = await registry.invoke(
        "research", {"question": "What are the main differences between the RTX 5090 and RTX 4090?"}
    )
    answer = result.get("answer") or ""
    check("research returns a sourced answer",
          result.get("ok") and len(answer) > 40,
          f"{answer[:130]}\n        sources: {len(result.get('sources') or [])}")

    await client.aclose()
    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
