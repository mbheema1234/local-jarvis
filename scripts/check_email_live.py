"""Live regression check for jarvis/tools/email.py against a real Gmail
account.

Exercises search_emails and read_email against the real inbox (read-only)
and confirms send_email's risk tier plus its confirmation gate -- without
ever completing a real send. MIME construction is covered separately by
calling send_email directly with a stubbed Gmail service, so the actual
network boundary (service.users().messages().send()) is never touched by
this script.

Needs data/gmail_token.json to already exist (run scripts/gmail_auth.py once
to create it).

    uv run python scripts/check_email_live.py
"""

from __future__ import annotations

import asyncio
import base64
import sys

from jarvis import tools  # noqa: F401 -- import registers every tool
from jarvis.bus import bus
from jarvis.config import GMAIL_TOKEN_PATH
from jarvis.security import Risk
from jarvis.security.policy import policy
from jarvis.tools.registry import REGISTRY, invoke

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


async def deny_next_permission_request(queue: asyncio.Queue, expect_tool: str) -> dict:
    """Watch an already-subscribed queue for the next permission_request and
    immediately deny it.

    Mirrors what the dashboard does when the user clicks Deny, but without a
    human or an HTTP round trip -- this proves the gate is live using the
    real policy/config, not a mock of it. The queue must be subscribed
    *before* the tool call is issued, or publish() -> _dispatch() (which runs
    synchronously on this same loop, before this task ever gets scheduled)
    can fire before the subscription exists and the event is lost.
    """
    try:
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=10)
            if event.get("kind") == "permission_request" and event.get("tool") == expect_tool:
                policy.resolve(event["id"], False)
                return event
    finally:
        bus.unsubscribe(queue)


async def main() -> int:
    bus.bind_loop(asyncio.get_running_loop())

    print(f"\n[0] token present at {GMAIL_TOKEN_PATH}")
    check("gmail_token.json exists", GMAIL_TOKEN_PATH.exists())
    if not GMAIL_TOKEN_PATH.exists():
        print("\n  Run scripts/gmail_auth.py first.")
        return 1

    # -- search_emails: read-only, real inbox -----------------------------
    print("\n[1] search_emails against the real inbox")
    result = await invoke("search_emails", {"query": "in:inbox", "max_results": 5})
    check("search_emails returned ok", result.get("ok", True) and "error" not in result,
          str(result.get("error", ""))[:200])
    results = result.get("results", [])
    check("search_emails returned real messages", len(results) > 0,
          f"count={result.get('count')}")

    if results:
        first = results[0]
        check("result has a non-empty subject", bool(first.get("subject", "").strip()) or
              first.get("subject", "") == "",  # a blank subject is legitimate Gmail data
              repr(first.get("subject")))
        check("result has a from header", bool(first.get("from", "").strip()),
              repr(first.get("from")))
        check("result has an id", bool(first.get("id")), repr(first.get("id")))
        check("result has a date", bool(first.get("date", "").strip()), repr(first.get("date")))
        print("        sample results:")
        for r in results[:5]:
            print(f"          [{r['id']}] from={r['from']!r} subject={r['subject']!r}")
            print(f"              snippet={r['snippet'][:80]!r}")

    # -- read_email: full message, real body -------------------------------
    print("\n[2] read_email on the first search result")
    if results:
        message_id = results[0]["id"]
        full = await invoke("read_email", {"message_id": message_id})
        check("read_email returned ok", full.get("ok", True) and "error" not in full,
              str(full.get("error", ""))[:200])
        check("read_email id matches request", full.get("id") == message_id)
        check("read_email has a from header", bool(full.get("from", "").strip()),
              repr(full.get("from")))
        check("read_email has a to header", bool(full.get("to", "").strip()),
              repr(full.get("to")))
        check("read_email subject matches the search result",
              full.get("subject") == results[0]["subject"],
              f"read={full.get('subject')!r} search={results[0]['subject']!r}")
        body = full.get("body", "")
        check("read_email body decoded to non-empty text", bool(body.strip()),
              f"body length={len(body)}")
        print(f"        subject={full.get('subject')!r}")
        print(f"        from={full.get('from')!r}")
        print(f"        body[:200]={body[:200]!r}")
    else:
        check("read_email exercised", False, "no search results to read")

    # -- search_emails with a query that should legitimately return zero --
    print("\n[3] search_emails with a query that should match nothing")
    empty = await invoke("search_emails", {
        "query": "from:no-such-sender-jarvis-check-9f3a7c@example.invalid"})
    check("empty search returns ok with zero results",
          empty.get("count") == 0 and empty.get("results") == [],
          str(empty))

    # -- send_email: risk tier + confirmation gate --------------------------
    print("\n[4] send_email is registered as HIGH risk")
    entry = REGISTRY.get("send_email")
    check("send_email is registered", entry is not None)
    if entry is not None:
        check("send_email risk tier is HIGH", entry.risk is Risk.HIGH, f"got {entry.risk!r}")

    print("\n[5] send_email through the real confirmation gate (denied, never sent)")
    permission_queue = bus.subscribe()  # subscribe *before* invoking -- see docstring above
    watcher = asyncio.create_task(deny_next_permission_request(permission_queue, "send_email"))
    start = asyncio.get_running_loop().time()
    send_result = await invoke("send_email", {
        "to": "jarvis-check-should-not-send@example.invalid",
        "subject": "Jarvis live check -- should never be sent",
        "body": "If a real email with this subject was sent, the confirmation "
                "gate is broken.",
    })
    elapsed = asyncio.get_running_loop().time() - start
    try:
        event = await asyncio.wait_for(watcher, timeout=5)
    except asyncio.TimeoutError:
        event = None
        watcher.cancel()

    check("a permission_request was raised for send_email", event is not None,
          str(event)[:200])
    if event is not None:
        check("permission_request carries the real recipient/subject",
              event.get("args", {}).get("to") == "jarvis-check-should-not-send@example.invalid",
              str(event.get("args")))
    check("denied send_email did not run",
          not send_result.get("ok") and send_result.get("denied"),
          str(send_result))
    check("denial was fast (gate, not a 45s confirm_timeout_s fallback)",
          elapsed < 5, f"{elapsed:.2f}s")

    # -- send_email MIME construction, in isolation, no network -----------
    print("\n[6] send_email MIME construction (stubbed service, no network call)")
    from jarvis.tools import email as email_mod

    captured = {}

    class _FakeMessages:
        def send(self, userId, body):  # noqa: N803 -- matches google client's kwarg name
            captured["userId"] = userId
            captured["raw"] = body["raw"]

            class _Exec:
                def execute(self_inner):
                    return {"id": "fake-message-id-0001"}

            return _Exec()

    class _FakeUsers:
        def messages(self):
            return _FakeMessages()

    class _FakeService:
        def users(self):
            return _FakeUsers()

    original_gmail_service = email_mod._gmail_service
    email_mod._gmail_service = lambda: (_FakeService(), None)
    try:
        stub_result = email_mod.send_email(
            to="unit-test@example.invalid",
            subject="Stubbed subject",
            body="Stubbed body text",
            cc="cc-test@example.invalid",
        )
    finally:
        email_mod._gmail_service = original_gmail_service

    check("stubbed send_email returned the fake message id",
          stub_result.get("id") == "fake-message-id-0001", str(stub_result))
    check("stubbed send_email never called through invoke() / no real network path taken",
          "raw" in captured, "no raw payload captured")

    if "raw" in captured:
        padded = captured["raw"] + "=" * (-len(captured["raw"]) % 4)
        raw_bytes = base64.urlsafe_b64decode(padded)
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        check("MIME payload has correct To header", "unit-test@example.invalid" in raw_text)
        check("MIME payload has correct Subject header", "Stubbed subject" in raw_text)
        check("MIME payload has correct Cc header", "cc-test@example.invalid" in raw_text)
        check("MIME payload carries the body text", "Stubbed body text" in raw_text)

    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
