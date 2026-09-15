"""Verify list_menu_items / click_menu_item against a live Jarvis server.

Drives the real HIGH-risk permission round-trip via the HTTP/WS API (same
pattern as check_integration.py) against a real Notepad window: opening the
View menu, listing its items, toggling Word wrap on and off, and confirming
error paths fail gracefully without leaving a menu hanging open.

Point it at a Notepad window you don't mind Jarvis clicking around in --
by default it targets the exact title "Untitled - Notepad" (a fresh,
unsaved window), never a window with an existing "*" unsaved-changes marker.

    uv run python scripts/check_menu_tools.py [port] [window-title]
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx
import websockets

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
WINDOW = sys.argv[2] if len(sys.argv) > 2 else "Untitled - Notepad"
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}/ws"

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))


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
        print(f"\n[1] target window: {WINDOW!r} on port {PORT}")

        print("\n[2] list_menu_items('View') -- should prompt (HIGH risk) and return items")
        result, event = await with_approval(client, "list_menu_items", {"window": WINDOW, "menu": "View"})
        check("permission prompt raised for list_menu_items", event.get("tool") == "list_menu_items")
        check("risk tier surfaced as high", event.get("risk") == "high", f"risk={event.get('risk')}")
        items = [e.get("name") for e in (result.get("items") or [])]
        check("list_menu_items returned items", result.get("ok", True) and len(items) > 0, f"items={items}")
        check("contains expected View items",
              any("word wrap" in i.casefold() for i in items) and any("status bar" in i.casefold() for i in items),
              f"items={items}")

        # Close whatever list_menu_items deliberately left open, so the next
        # step starts from a clean, closed-menu state.
        await call(client, "press_keys", {"keys": ["escape"]})
        await asyncio.sleep(0.3)

        print("\n[3] click_menu_item('View', 'Word wrap') -- toggle ON->OFF (or vice versa)")
        result, event = await with_approval(client, "click_menu_item",
                                             {"window": WINDOW, "menu": "View", "item": "Word wrap"})
        check("permission prompt raised for click_menu_item", event.get("tool") == "click_menu_item")
        check("click_menu_item reports ok", result.get("ok") is True, json.dumps(result)[:200])

        print("\n[4] click_menu_item again -- toggle back, proves repeated open/close cycles work")
        result2, event2 = await with_approval(client, "click_menu_item",
                                               {"window": WINDOW, "menu": "View", "item": "Word wrap"})
        check("second toggle reports ok", result2.get("ok") is True, json.dumps(result2)[:200])

        print("\n[5] error handling: nonexistent menu")
        result, event5 = await with_approval(client, "list_menu_items",
                                              {"window": WINDOW, "menu": "ThisMenuDoesNotExist"})
        check("bad menu name fails gracefully", result.get("ok") is False, json.dumps(result)[:200])

        print("\n[6] error handling: nonexistent item in a real menu")
        result3, event3 = await with_approval(client, "click_menu_item",
                                               {"window": WINDOW, "menu": "View", "item": "ThisItemDoesNotExist"})
        check("bad item name fails gracefully", result3.get("ok") is False, json.dumps(result3)[:200])

        print("\n[7] confirm no menu left hanging open after the failure")
        result4, event4 = await with_approval(client, "list_menu_items", {"window": WINDOW, "menu": "View"})
        items4 = [e.get("name") for e in (result4.get("items") or [])]
        check("View menu still opens cleanly and lists items", len(items4) > 0, f"items={items4}")
        await call(client, "press_keys", {"keys": ["escape"]})

    print(f"\n{'=' * 60}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
