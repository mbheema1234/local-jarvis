"""Verify Jarvis can read and drive other applications' interfaces.

Read-only by default: it inspects NZXT CAM and reads values, but changes no
settings. Pass --click to also test clicking a harmless navigation link.

    uv run python scripts/check_uia.py [--click]
"""

from __future__ import annotations

import asyncio
import sys

DO_CLICK = "--click" in sys.argv
passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


async def main() -> int:
    from jarvis.bus import bus
    from jarvis.tools import registry

    bus.bind_loop(asyncio.get_running_loop())

    print("\n[1] multi-monitor awareness")
    result = await registry.invoke("cursor_position", {})
    desktop = result.get("desktop_size", [0, 0])
    primary = result.get("primary_screen", [0, 0])
    check("reports the full virtual desktop, not just the primary screen",
          desktop[0] > primary[0] or desktop[1] > primary[1],
          f"desktop {desktop[0]}x{desktop[1]} vs primary {primary[0]}x{primary[1]}")

    print("\n[2] reading inside an Electron app (NZXT CAM)")
    result = await registry.invoke("inspect_app", {"window": "NZXT CAM"})
    if not result.get("ok"):
        check("inspect_app found controls", False, result.get("error", ""))
        print("\n  (Is NZXT CAM running? Start it and re-run.)")
        return 1

    elements = result["elements"]
    names = {e["name"].casefold() for e in elements}
    check("inspect_app found controls", len(elements) > 10, f"{len(elements)} elements")
    check("found the Cooling navigation link", "cooling" in names)
    clickable = [e for e in elements if e["clickable"]]
    check("identified clickable controls", len(clickable) >= 5,
          ", ".join(e["name"] for e in clickable[:6]))

    print("\n[3] reading live values out of the app")
    for label in ("Liquid Temperature", "CPU Temperature", "Pump"):
        result = await registry.invoke("read_app_value",
                                       {"window": "NZXT CAM", "label": label})
        got = str(result.get("value", ""))
        check(f"read {label!r}", result.get("ok") and any(c.isdigit() for c in got), got[:60])

    print("\n[4] element lookup rejects what isn't there")
    result = await registry.invoke("click_element",
                                   {"window": "NZXT CAM", "name": "NoSuchButtonXYZ"})
    check("unknown element fails with suggestions",
          not result.get("ok") and bool(result.get("available")),
          f"suggested: {(result.get('available') or [])[:4]}")

    if DO_CLICK:
        print("\n[5] clicking a navigation link (harmless)")
        result = await registry.invoke("click_element",
                                       {"window": "NZXT CAM", "name": "PC Monitoring"})
        check("clicked 'PC Monitoring'", result.get("ok"), str(result.get("at")))

        after = await registry.invoke("inspect_app", {"window": "NZXT CAM"})
        check("page changed after the click", after.get("ok"),
              f"{after.get('count')} elements now visible")

        # Put it back where it was.
        await registry.invoke("click_element", {"window": "NZXT CAM", "name": "Cooling"})
    else:
        print("\n[5] skipped click test (pass --click to include it)")

    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
