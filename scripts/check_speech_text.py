"""Verify text is rewritten for the ear before it reaches the speech engine.

    uv run python scripts/check_speech_text.py
"""

from __future__ import annotations

import sys

from jarvis.voice.speech_text import for_speech

# (input, must contain, must NOT contain)
CASES: list[tuple[str, list[str], list[str]]] = [
    # The reported bug.
    ("Turning on the **radio** now.", ["radio"], ["*"]),
    ("That's *really* important.", ["really"], ["*"]),
    ("Use ***both*** hands.", ["both"], ["*"]),
    ("The __config__ file is ready.", ["config"], ["_"]),
    ("Set _volume_ to 40.", ["volume"], ["_"]),
    ("That is ~~wrong~~ right.", ["wrong", "right"], ["~"]),

    # Headings, quotes, rules, lists.
    ("## System Status\nAll good.", ["System Status", "All good"], ["#"]),
    ("> He said it was fine.", ["He said it was fine"], [">"]),
    ("- first\n- second\n- third", ["first", "second", "third"], ["-"]),
    ("1. Open it\n2. Close it", ["Open it", "Close it"], ["1.", "2."]),
    ("Done.\n\n---\n\nNext.", ["Done", "Next"], ["---"]),

    # Code.
    ("Run `get_volume` to check.", ["get_volume"], ["`"]),
    ("Here:\n```python\nprint('hi')\n```\nThat's it.", ["That's it"], ["```", "print"]),

    # Links, emails, paths.
    ("See [the docs](https://example.com/a/b) for more.", ["the docs"],
     ["https", "example.com/a", "("]),
    ("Check https://openrouter.ai/docs now.", ["openrouter"], ["https", "://"]),
    ("Mail alex@example.com about it.", ["alex", "at", "example dot com"], ["@"]),
    (r"Saved to C:\Users\bheem\projects\jarvis\notes.txt", ["notes.txt"],
     ["C:\\", "Users", "\\"]),

    # Symbols and units.
    ("CPU is at 45% now.", ["45 percent"], ["%"]),
    ("Liquid is 38°C.", ["38 degrees"], ["°"]),
    ("You have 16GB free.", ["16 gigabytes"], ["16GB"]),
    ("Open Epic → then NVIDIA.", ["then"], ["→"]),
    ("Silent | Performance | Fixed", ["Silent", "Performance", "Fixed"], ["|"]),
    ("It's ~2600 RPM.", ["about 2600"], ["~"]),
    ("Try Chrome & Firefox.", ["and"], ["&"]),
    ("Resolution is 2560x1440.", ["2560 by 1440"], ["2560x"]),
    ("Use bold/italic there.", ["or"], ["bold/italic"]),
    ("Done ✅ and ready 🎉", ["Done", "ready"], ["✅", "🎉"]),
    ("Gmail's Send \u202a(Ctrl-Enter)\u202c button.", ["Send"], ["\u202a", "\u202c"]),

    # Things that must survive untouched.
    ("2 * 3 is 6", ["2 times 3 is 6"], ["*"]),
    ("The file is snake_case_name here", ["snake_case_name"], []),
    ("Your CPU is at 12 percent across 24 cores.",
     ["CPU", "12 percent", "24 cores"], []),
    ("I opened Epic Games Launcher and the NVIDIA App.",
     ["Epic Games Launcher", "NVIDIA App"], []),
]

REALISTIC = [
    "**Done!** I've set your cooling to *Silent* mode — pump is now at ~2400 RPM "
    "(down from 2607). Check `NZXT CAM` → Cooling for details.",
    "## Search results\n\n1. **Seahawks win Super Bowl LX** — 29-13 over the "
    "Patriots\n2. See https://espn.com/nfl/recap for the full story\n\nWant me to "
    "open it? 🏈",
]


def main() -> int:
    failures = 0

    print("\n--- transformations ---\n")
    for source, required, forbidden in CASES:
        result = for_speech(source)
        missing = [w for w in required if w.lower() not in result.lower()]
        leaked = [w for w in forbidden if w in result]
        ok = not missing and not leaked
        if not ok:
            failures += 1
            print(f"  FAIL  {source[:52]!r}")
            print(f"        got: {result!r}")
            if missing:
                print(f"        missing: {missing}")
            if leaked:
                print(f"        still present: {leaked}")
        else:
            print(f"  ok    {source[:46]!r}\n        -> {result[:64]!r}")

    print("\n--- realistic replies ---\n")
    for source in REALISTIC:
        result = for_speech(source)
        print(f"  before: {source[:100]!r}")
        print(f"  after : {result[:150]!r}\n")
        for bad in ("**", "```", "##", "http", "→"):
            if bad in result:
                print(f"  FAIL   {bad!r} survived")
                failures += 1

    print("=" * 62)
    print(f"  {len(CASES)} cases, {failures} failure(s)")
    return 1 if failures else 0


sys.exit(main())
