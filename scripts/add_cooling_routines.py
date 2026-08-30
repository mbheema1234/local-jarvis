"""Save the discovered NZXT CAM cooling sequences as one-command routines.

The steps here are exactly what Jarvis worked out by exploring the app; saving
them means it replays a known-good sequence instead of rediscovering it, which
is both faster and cheaper.

    uv run python scripts/add_cooling_routines.py
"""

from __future__ import annotations

import asyncio
import sys

# Every mode that might currently be showing, since the dropdown is opened by
# clicking whichever one is active.
MODES = "Performance, Silent, Fixed, Balanced, Quiet, Default"


def steps_for(mode: str) -> list[dict]:
    others = ", ".join(m for m in MODES.split(", ") if m != mode)
    return [
        {"tool": "launch_app", "args": {"name": "NZXT CAM"}},
        {"tool": "click_element", "args": {"window": "NZXT CAM", "name": "Cooling"}},
        {"tool": "select_option",
         "args": {"window": "NZXT CAM", "option": mode, "opener": others}},
    ]


ROUTINES = [
    ("silent cooling", "Set NZXT CAM's cooling profile to Silent.", steps_for("Silent")),
    ("performance cooling", "Set NZXT CAM's cooling profile to Performance.",
     steps_for("Performance")),
]


async def main() -> int:
    from jarvis.bus import bus
    from jarvis.tools import registry

    bus.bind_loop(asyncio.get_running_loop())

    for name, description, steps in ROUTINES:
        result = await registry.invoke("save_routine", {
            "name": name, "description": description, "steps": steps,
        })
        status = "saved" if result.get("ok") else f"FAILED: {result.get('error')}"
        print(f"  {name:<22} {status} ({len(steps)} steps)")

    listing = await registry.invoke("list_routines", {})
    print("\nroutines now available:")
    for routine in listing.get("routines", []):
        print(f"  - {routine['name']}: {routine['description']}")
    return 0


sys.exit(asyncio.run(main()))
