"""Verify multi-monitor vision and web-page inspection.

Read-only: it looks at the screens and reads what the browser exposes, but
clicks nothing and sends nothing.

    uv run python scripts/check_vision.py
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
    from jarvis.llm.openrouter import client
    from jarvis.tools import registry

    bus.bind_loop(asyncio.get_running_loop())

    print("\n[1] monitor enumeration")
    result = await registry.invoke("list_monitors", {})
    monitors = result.get("monitors", [])
    check("found all three monitors", len(monitors) == 3, f"{len(monitors)} detected")
    for m in monitors:
        apps = ", ".join(m.get("apps", [])[:4]) or "nothing"
        print(f"        monitor {m['monitor']}: {m['width']}x{m['height']} "
              f"({m['orientation']}) — {apps}")
    check("identified the portrait monitor",
          any(m["orientation"] == "portrait" for m in monitors))
    check("knows which windows are on which monitor",
          any(m.get("window_count", 0) > 0 for m in monitors))

    print("\n[2] looking at a single monitor")
    result = await registry.invoke(
        "see_screen", {"question": "What applications are visible here?", "monitor": 2})
    answer = result.get("answer", "")
    check("described monitor 2", result.get("ok") and len(answer) > 30, answer[:150])
    # Monitor 2 holds NZXT CAM, the NVIDIA App and Discord.
    check("recognised what is actually on monitor 2",
          any(k in answer.lower() for k in ("nzxt", "cam", "discord", "nvidia")),
          "looked for nzxt/discord/nvidia in the description")

    print("\n[3] looking at all monitors at once")
    result = await registry.invoke(
        "see_screen", {"question": "Describe each screen briefly.", "monitor": 0})
    check("described the whole desktop", result.get("ok"), result.get("answer", "")[:150])

    print("\n[4] reading a web page through the browser")
    result = await registry.invoke(
        "inspect_app", {"window": "Firefox", "interactive_only": True, "max_elements": 60})
    if not result.get("ok"):
        check("browser exposed page elements", False, result.get("error", ""))
    else:
        elements = result["elements"]
        check("browser exposed page elements", len(elements) > 15,
              f"{len(elements)} interactive elements")
        kinds = {e["type"] for e in elements}
        check("includes clickable page controls",
              bool(kinds & {"Button", "Hyperlink", "Edit", "ComboBox"}),
              f"types: {sorted(kinds)}")

    print("\n[5] filtering a large page")
    result = await registry.invoke(
        "inspect_app", {"window": "Firefox", "filter": "search"})
    check("filter narrows a big tree",
          result.get("ok") and result.get("count", 0) <= result.get("scanned", 1e9),
          f"{result.get('count')} of {result.get('scanned')} matched 'search'"
          if result.get("ok") else result.get("error", ""))

    await client.aclose()
    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
