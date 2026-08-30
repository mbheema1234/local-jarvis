"""Can Jarvis navigate Gmail by reading the page?

Goes as far as a filled-in draft, then discards it. Deliberately never clicks
Send: that is irreversible and belongs to the user.

    uv run python scripts/check_gmail.py
"""

from __future__ import annotations

import asyncio
import sys

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


async def discard_draft(registry) -> None:
    """Throw the draft away properly.

    Escape only minimises Gmail's compose window and the draft survives, so a
    later run reopens it with the old contents still in place. Ctrl+Shift+D is
    the actual discard.
    """
    await registry.invoke("press_keys", {"keys": ["ctrl", "shift", "d"]})
    await asyncio.sleep(1.5)


async def main() -> int:
    from jarvis.bus import bus
    from jarvis.llm.openrouter import client
    from jarvis.tools import registry

    bus.bind_loop(asyncio.get_running_loop())

    print("\n[0] clear any draft left over from a previous run")
    result = await registry.invoke(
        "inspect_app", {"window": "Firefox", "filter": "Message Body"})
    if result.get("ok") and result.get("elements"):
        await discard_draft(registry)
        print("        discarded a leftover compose window")
    else:
        print("        nothing left open")

    print("\n[1] find the Gmail tab")
    result = await registry.invoke(
        "inspect_app", {"window": "Firefox", "filter": "gmail", "max_elements": 40})
    tabs = result.get("elements", []) if result.get("ok") else []
    check("located a Gmail tab", bool(tabs),
          ", ".join(t["name"][:50] for t in tabs[:3]) or result.get("error", ""))
    if not tabs:
        print("\n  Open Gmail in Firefox and re-run.")
        return 1

    print("\n[2] switch to it")
    result = await registry.invoke(
        "click_element", {"window": "Firefox", "name": tabs[0]["name"]})
    check("clicked the Gmail tab", result.get("ok"), str(result.get("at")))
    await asyncio.sleep(2.5)

    print("\n[3] find the Compose button")
    result = await registry.invoke(
        "inspect_app", {"window": "Firefox", "filter": "compose", "max_elements": 30})
    compose = [e for e in result.get("elements", [])] if result.get("ok") else []
    check("found Compose", bool(compose),
          ", ".join(f"[{e['type']}] {e['name']}" for e in compose[:3])
          or result.get("error", ""))
    if not compose:
        return 1

    print("\n[4] open a compose window")
    result = await registry.invoke(
        "click_element", {"window": "Firefox", "name": compose[0]["name"]})
    check("clicked Compose", result.get("ok"))
    await asyncio.sleep(3)

    print("\n[5] find the message fields")
    result = await registry.invoke(
        "inspect_app", {"window": "Firefox", "interactive_only": True, "max_elements": 260})
    elements = result.get("elements", [])
    editable = {e["name"] for e in elements if e["type"] in ("Edit", "ComboBox")}

    check("found the To field", "To recipients" in editable)
    check("found the Subject field", "Subject" in editable)
    check("found the Message Body field",
          any("message body" in n.casefold() for n in
              {e["name"] for e in elements if e["type"] in ("Edit", "Document")}))
    send = [e["name"] for e in elements
            if e["type"] == "Button" and e["name"].casefold().startswith("send")]
    check("found the Send button", bool(send), repr(send[:1]))

    print("\n[6] fill in the draft (never sent)")
    for field, text, label in [
        ("To recipients", "jarvis-test@example.com", "recipient"),
        ("Subject", "Jarvis test please ignore", "subject"),
        ("Message Body", "This draft was written by Jarvis and was not sent.", "body"),
    ]:
        result = await registry.invoke("set_element_text", {
            "window": "Firefox", "field": field, "text": text})
        check(f"typed the {label}", result.get("ok"),
              f"into {result.get('field', '?')!r} (verified={result.get('verified')})"
              if result.get("ok") else str(result.get("error") or result.get("note"))[:90])
        await asyncio.sleep(0.5)

    print("\n[7] confirm visually — each field independently")
    shot = await registry.invoke("see_screen", {
        "question": "A Gmail compose window is open. Report exactly three things, "
                    "each on its own line: TO= followed by the contents of the To "
                    "box, SUBJECT= followed by the Subject box, BODY= followed by "
                    "the message body. Write 'empty' where a box has nothing.",
        "monitor": 1})
    answer = (shot.get("answer") or "")
    print(f"        {answer[:260]}")

    lowered = answer.lower()

    def section(tag: str, nxt: str | None) -> str:
        start = lowered.find(tag)
        if start < 0:
            return ""
        end = lowered.find(nxt, start) if nxt else len(lowered)
        return lowered[start:end if end > 0 else len(lowered)]

    to_part = section("to=", "subject=")
    subject_part = section("subject=", "body=")
    body_part = section("body=", None)

    check("recipient landed in the To box", "example.com" in to_part, to_part[:70])
    check("subject landed in the Subject box (and not To)",
          "jarvis test" in subject_part and "jarvis test" not in to_part,
          f"subject={subject_part[:60]!r}")
    check("body landed in the message body", "draft" in body_part, body_part[:70])

    await discard_draft(registry)
    print("        (draft discarded — no email was sent)")

    await client.aclose()
    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
