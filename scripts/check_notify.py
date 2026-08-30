"""Regression test for the notify() PowerShell-injection fix.

`notify()` builds a PowerShell script that embeds the caller's title/message
and runs it via `run_powershell()` -> a real `powershell.exe` subprocess.
It is Risk.SAFE, so nothing prompts for confirmation before it runs -- if
escaping is wrong, arbitrary text (voice input, a fetched web page, ...)
becomes arbitrary PowerShell.

This drives the real path end to end: jarvis.tools.registry.invoke("notify",
...) -> notify() -> run_powershell() -> powershell.exe -> the OS toast, then
reads the toast back out of the real Windows notification history and checks
a process list, so it can actually catch a broken fix rather than just
asserting on the string the escaping helper produces.

    uv run python scripts/check_notify.py
"""

from __future__ import annotations

import asyncio
import sys

import psutil

from jarvis import tools  # noqa: F401 -- import registers every tool
from jarvis.tools.registry import invoke
from jarvis.winutil import run_powershell

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def calc_pids() -> set[int]:
    """PIDs of anything calculator-shaped -- what $(calc.exe) would spawn."""
    pids = set()
    for p in psutil.process_iter(["name", "pid"]):
        try:
            if "calc" in (p.info.get("name") or "").lower():
                pids.add(p.info["pid"])
        except psutil.Error:
            continue
    return pids


def latest_toast() -> tuple[str, str] | None:
    """Read the most recent "Jarvis" toast's literal title/body back from
    the real Windows notification history (Action Center), independent of
    however notify() built the script."""
    # GetHistory() returns a WinRT IVectorView, which PowerShell doesn't
    # recognize as a real array -- indexing it directly (.Item(0)) dispatches
    # the call per-element instead of into the collection. Force it into a
    # true PS array with @(...) first.
    script = """
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
    $history = @([Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory("Jarvis"))
    if ($history.Count -gt 0) {
        $t = $history[0].Content.GetElementsByTagName("text")
        Write-Output ("<<TITLE>>" + $t.Item(0).InnerText)
        Write-Output ("<<BODY>>" + $t.Item(1).InnerText)
    }
    """
    proc = run_powershell(script, timeout=15)
    title = body = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("<<TITLE>>"):
            title = line[len("<<TITLE>>"):]
        elif line.startswith("<<BODY>>"):
            body = line[len("<<BODY>>"):]
    if title is None or body is None:
        return None
    return title, body


async def run_case(label: str, title: str, message: str) -> None:
    before = calc_pids()
    result = await invoke("notify", {"title": title, "message": message})
    check(f"{label}: tool returns ok", bool(result.get("ok")), str(result))

    await asyncio.sleep(0.8)  # let the toast land in Action Center history

    expected_title = title.replace("\r", " ").replace("\n", " ")
    expected_body = message.replace("\r", " ").replace("\n", " ")
    got = latest_toast()
    check(f"{label}: toast text is literal, not evaluated",
          got == (expected_title, expected_body),
          f"got={got!r} want={(expected_title, expected_body)!r}")

    after = calc_pids()
    check(f"{label}: no calculator process spawned", after == before,
          f"before={before} after={after}")


async def main() -> int:
    await run_case("plain text", "Jarvis Check", "Plain ordinary text")
    await run_case("$(...) subexpression", "$(calc.exe)", "body $(calc.exe) too")
    await run_case("quote breakout", "'; calc.exe #", "still just text")
    await run_case("apostrophe / contraction", "Contraction test", "it's working")
    await run_case("embedded newline", "Multi\nLine\rTitle", "Multi\nLine\rBody")

    print(f"\n{'=' * 60}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
