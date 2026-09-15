"""Verify list_context_menu_items / click_context_menu_item against a live Jarvis server.

Drives the real HIGH-risk permission round-trip via the HTTP/WS API (same
pattern as check_menu_tools.py) against a real Notepad window: right-clicking
a paragraph of text, listing what the context menu reveals, clicking "Select
all" and "Copy" and verifying the result against the real Windows clipboard
(not just the tool's own "ok" field), and confirming error paths fail
gracefully without leaving a menu hanging open.

Unlike check_menu_tools.py, this script does not assume the target window
already exists -- launch a fresh Notepad window yourself, type a paragraph of
placeholder text into it, and leave that window focused before running this.

    uv run python scripts/check_context_menu.py [port] [window-title] [target-name] [expected-text]
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys

import httpx
import websockets

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
WINDOW = sys.argv[2] if len(sys.argv) > 2 else "Notepad"
TARGET = sys.argv[3] if len(sys.argv) > 3 else "Text editor"
# Only used to strengthen the clipboard check in [4] into an exact match
# rather than a bare non-empty check; pass a 4th arg if the target window's
# text differs from this script's own default setup instructions.
EXPECT_TEXT = (
    sys.argv[4] if len(sys.argv) > 4 else
    "The quick brown fox jumps over the lazy dog. This is placeholder "
    "paragraph text for testing right-click context menus in Notepad."
)
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}/ws"

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))


def get_clipboard() -> str:
    """Read the real Windows clipboard, independent of anything Jarvis reports."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


async def auto_approve(socket, approve: bool = True) -> asyncio.Task:
    async def watcher() -> dict:
        while True:
            event = json.loads(await socket.recv())
            if event.get("kind") == "permission_request":
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{BASE}/api/permission",
                        json={"id": event["id"], "approved": approve},
                    )
                return event
    return asyncio.create_task(watcher())


async def call(client, name, args):
    return (await client.post(f"{BASE}/api/tool", json={"name": name, "args": args})).json()


async def with_approval(client, name, args):
    # Risk.HIGH tools always raise a permission prompt before their body
    # runs, even for calls that will ultimately fail on bad args -- so every
    # invocation here goes through the approval round-trip, or it would sit
    # for the full confirm_timeout_s and come back denied instead of
    # exercising the tool's own error handling.
    async with websockets.connect(WS) as socket:
        await socket.recv()  # replay frame
        task = await auto_approve(socket, approve=True)
        result = await call(client, name, args)
        event = await asyncio.wait_for(task, timeout=20)
        return result, event


async def main() -> int:
    async with httpx.AsyncClient(timeout=90.0) as client:
        print(f"\n[1] target window: {WINDOW!r}, element {TARGET!r} on port {PORT}")

        # Start from a known-closed state -- a leftover menu from a previous
        # run or manual poking would otherwise contaminate the first check.
        await call(client, "press_keys", {"keys": ["escape"]})
        await asyncio.sleep(0.4)

        print(f"\n[2] list_context_menu_items({TARGET!r}) -- should prompt (HIGH risk) and return items")
        result, event = await with_approval(client, "list_context_menu_items",
                                             {"window": WINDOW, "target": TARGET})
        check("permission prompt raised for list_context_menu_items", event.get("tool") == "list_context_menu_items")
        check("risk tier surfaced as high", event.get("risk") == "high", f"risk={event.get('risk')}")
        items = [e.get("name") for e in (result.get("items") or []) if e.get("clickable")]
        check("list_context_menu_items returned items", result.get("ok", True) and len(items) > 0, f"items={items}")
        folded = [i.casefold() for i in items]
        check("contains expected Cut/Copy/Paste/Select all items",
              all(any(word in i for i in folded) for word in ("cut", "copy", "paste", "select all")),
              f"items={items}")

        # list_context_menu_items no longer promises the menu stays open --
        # its docstring now says a follow-up call reuses it "if it's still
        # open, or reopen[s] it fresh if not" (modern Notepad's AI-enhanced
        # menu self-dismisses in well under a second). Exercise exactly that
        # documented either-way path here, rather than closing it ourselves
        # first: whichever branch _open_context_menu takes, the click should
        # still land.
        print("\n[3] click_context_menu_item('Select all') immediately after, without closing the menu first")
        result, event = await with_approval(client, "click_context_menu_item",
                                             {"window": WINDOW, "target": TARGET, "item": "Select all"})
        check("click succeeds whether it reused the still-open menu or opened a fresh one (documented either-way behavior)",
              result.get("ok") is True, json.dumps(result)[:300])

        # Whether or not [3] worked, get to a clean, closed-menu state and
        # verify the basic click path standalone, with an independent,
        # non-Jarvis check: Select all then Copy should put the exact typed
        # text on the real Windows clipboard.
        await call(client, "press_keys", {"keys": ["escape"]})
        await asyncio.sleep(0.4)

        print(f"\n[4] click_context_menu_item('Select all') then ('Copy'), fresh each time -- verify via real clipboard")
        result, event = await with_approval(client, "click_context_menu_item",
                                             {"window": WINDOW, "target": TARGET, "item": "Select all"})
        check("permission prompt raised for click_context_menu_item", event.get("tool") == "click_context_menu_item")
        check("Select all reports ok", result.get("ok") is True, json.dumps(result)[:300])

        result2, event2 = await with_approval(client, "click_context_menu_item",
                                               {"window": WINDOW, "target": TARGET, "item": "Copy"})
        check("Copy reports ok", result2.get("ok") is True, json.dumps(result2)[:300])
        await asyncio.sleep(0.3)
        clipboard = get_clipboard()
        expect = EXPECT_TEXT.strip()
        check("clipboard exactly matches the target's full text (verified outside Jarvis)",
              bool(expect) and clipboard == expect,
              f"clipboard={clipboard[:120]!r} expect={expect[:120]!r}" if not expect or clipboard != expect
              else f"clipboard={clipboard[:60]!r}...")

        # Isolated spot check, deliberately run on its own (not chained onto
        # [2]/[3] above) so a failure here doesn't corrupt the state the
        # original list-then-click test depends on: does calling
        # list_context_menu_items a second time in a row, with nothing else
        # in between, still succeed with the same item set -- whether that
        # second call reuses a menu still open from the first, or (as is
        # likely on this app, given its <1s self-dismiss lifetime) has to
        # right-click open a fresh one -- rather than the contaminated-
        # baseline bug that used to return an empty list either way?
        print(f"\n[4b] list_context_menu_items({TARGET!r}) called twice in a row -- second call should succeed with the same items, reused or freshly reopened")
        result5a, event5a = await with_approval(client, "list_context_menu_items",
                                                 {"window": WINDOW, "target": TARGET})
        items5a = [e.get("name") for e in (result5a.get("items") or []) if e.get("clickable")]
        check("first of the pair succeeds with items", result5a.get("ok", True) and len(items5a) > 0,
              f"items={items5a}")

        result5b, event5b = await with_approval(client, "list_context_menu_items",
                                                 {"window": WINDOW, "target": TARGET})
        items5b = [e.get("name") for e in (result5b.get("items") or []) if e.get("clickable")]
        check("second call in a row still succeeds with items", result5b.get("ok", True) and len(items5b) > 0,
              f"ok={result5b.get('ok', True)} error={result5b.get('error')} items={items5b}")
        check("second call returns the same item set as the first (same menu content, whether reused or freshly reopened)",
              bool(items5a) and set(items5b) == set(items5a), f"first={items5a} second={items5b}")
        await call(client, "press_keys", {"keys": ["escape"]})
        await asyncio.sleep(0.3)

        print("\n[5] error handling: nonexistent target")
        result3, event3 = await with_approval(client, "list_context_menu_items",
                                               {"window": WINDOW, "target": "ThisElementDoesNotExist"})
        check("bad target name fails gracefully", result3.get("ok") is False, json.dumps(result3)[:200])

        print("\n[6] error handling: nonexistent item in a real target (fresh open)")
        result4, event4 = await with_approval(client, "click_context_menu_item",
                                               {"window": WINDOW, "target": TARGET, "item": "ThisItemDoesNotExist"})
        check("bad item name fails gracefully", result4.get("ok") is False, json.dumps(result4)[:200])

        print("\n[7] confirm no menu left hanging open after the failure")
        result5, event5 = await with_approval(client, "list_context_menu_items",
                                               {"window": WINDOW, "target": TARGET})
        items5 = [e.get("name") for e in (result5.get("items") or []) if e.get("clickable")]
        check("context menu still opens cleanly and lists items", len(items5) > 0, f"items={items5}")
        await call(client, "press_keys", {"keys": ["escape"]})
        await asyncio.sleep(0.3)

    print(f"\n{'=' * 60}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
