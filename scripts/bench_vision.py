"""Compare vision models on a real screenshot of this machine.

Reads the same monitor with each candidate and reports accuracy, latency and
cost, so the choice is made on evidence rather than on model names.

    uv run python scripts/bench_vision.py [monitor]
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time

MONITOR = int(sys.argv[1]) if len(sys.argv) > 1 else 3

CANDIDATES = [
    ("google/gemini-2.5-flash", 0.30, 2.50),      # current default
    ("google/gemini-3.7-flash", 0.75, 3.75),
    ("google/gemini-2.5-pro", 1.25, 10.00),
    ("google/gemini-3.1-pro-preview", 2.00, 12.00),
    ("anthropic/claude-sonnet-5", 2.00, 10.00),
]

# Short and checkable: the point is accuracy on real detail, not an essay.
# Capping tokens without capping length just rewards whoever skips the preamble.
QUESTION = (
    "Answer in at most 3 short sentences, no markdown, no headings.\n"
    "1) Name every application window you can see.\n"
    "2) Quote the exact title text of the frontmost window.\n"
    "3) Name one specific piece of small text you can read anywhere on screen."
)


async def ask(model: str, encoded: str) -> tuple[str, float, dict]:
    from jarvis.llm.openrouter import OpenRouterError, client

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": QUESTION},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        ],
    }]
    started = time.time()
    try:
        result = await client.chat(messages, model=model, max_tokens=300,
                                   temperature=0.1)
    except OpenRouterError as exc:
        return f"ERROR: {exc}", time.time() - started, {}
    return ((result["message"].get("content") or "").strip(),
            time.time() - started, result.get("usage", {}))


async def main() -> int:
    from jarvis.bus import bus
    from jarvis.llm.openrouter import client
    from jarvis.tools.screen import _capture

    bus.bind_loop(asyncio.get_running_loop())

    png, size = _capture(MONITOR)
    encoded = base64.b64encode(png).decode("ascii")
    print(f"\nmonitor {MONITOR}: {size[0]}x{size[1]}, {len(png)/1024:.0f} KB\n")

    for model, in_price, out_price in CANDIDATES:
        answer, elapsed, usage = await ask(model, encoded)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost = (prompt_tokens * in_price + completion_tokens * out_price) / 1e6

        print("=" * 74)
        print(f"{model}")
        print(f"  {elapsed:.1f}s | {prompt_tokens} in + {completion_tokens} out "
              f"| ~${cost:.5f} per look")
        print("-" * 74)
        print(f"  {answer[:460]}")
        print()

    await client.aclose()
    return 0


sys.exit(asyncio.run(main()))
