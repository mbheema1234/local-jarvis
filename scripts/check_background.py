"""Does Jarvis keep working with the dashboard closed, and refuse duplicates?

Closes the window exactly as clicking the X does (WM_CLOSE), then checks the
process, the wake listener and the agent all survive.

    uv run python scripts/check_background.py
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time

import httpx
import psutil

BASE = "http://127.0.0.1:8787"
WM_CLOSE = 0x0010

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


def jarvis_processes() -> list[psutil.Process]:
    """Actual Jarvis instances.

    Counting anything with "jarvis" in its command line is wrong: it catches
    this test script (which lives under the project path) and `uv run`'s
    launcher shim, which exits immediately after handing off.
    """
    import os

    found = []
    for proc in psutil.process_iter(["name", "cmdline", "ppid"]):
        try:
            if proc.info["name"] not in ("python.exe", "pythonw.exe"):
                continue
            if proc.pid == os.getpid():
                continue
            cmdline = [str(part) for part in (proc.info["cmdline"] or [])]
            if "-m" not in cmdline:
                continue
            index = cmdline.index("-m")
            if index + 1 >= len(cmdline) or cmdline[index + 1] != "jarvis":
                continue
            # The shim keeps its child's command line but does no work; the
            # real instance is the one holding the speech models in memory.
            if proc.memory_info().rss < 100 * 1024 * 1024:
                continue
            found.append(proc)
        except psutil.Error:
            continue
    return found


def jarvis_window() -> int:
    """Handle of the visible dashboard window, or 0."""
    import pygetwindow as gw

    for win in gw.getAllWindows():
        if (win.title or "").strip() == "Jarvis" and win.visible:
            return win._hWnd
    return 0


def main() -> int:
    print("\n[1] a single instance is running")
    procs = jarvis_processes()
    check("exactly one Jarvis process", len(procs) == 1,
          f"{len(procs)} found: {[p.pid for p in procs]}")
    if not procs:
        print("\n  Start Jarvis first.")
        return 1
    pid = procs[0].pid

    print("\n[2] a second launch is refused, not duplicated")
    result = subprocess.run(
        ["C:\\Users\\bheem\\AppData\\Local\\hermes\\bin\\uv.exe", "run",
         "python", "-m", "jarvis", "--allow-elevated"],
        capture_output=True, text=True, timeout=120,
        cwd="C:\\Users\\bheem\\projects\\jarvis",
    )
    output = (result.stdout or "") + (result.stderr or "")
    check("second launch exited instead of starting up",
          "already running" in output.lower(),
          next((line.strip() for line in output.splitlines()
                if "already running" in line.lower()), output[-120:]))
    check("still only one process afterwards", len(jarvis_processes()) == 1,
          f"{len(jarvis_processes())} running")

    print("\n[3] close the dashboard window (the same as clicking X)")
    handle = jarvis_window()
    check("dashboard window was open", handle != 0)
    if handle:
        ctypes.windll.user32.PostMessageW(handle, WM_CLOSE, 0, 0)
        time.sleep(3)
        check("window is now hidden", jarvis_window() == 0)

    print("\n[4] with the window closed, everything still runs")
    check("process is still alive", psutil.pid_exists(pid), f"pid {pid}")

    try:
        state = httpx.get(f"{BASE}/api/state", timeout=15).json()
        check("server still responding", state.get("state") is not None,
              f"state={state.get('state')}")
    except Exception as exc:
        check("server still responding", False, str(exc))

    try:
        wake = httpx.get(f"{BASE}/api/wake", timeout=10).json()
        check("wake word still armed", wake.get("listening") is True, str(wake))
    except Exception as exc:
        check("wake word still armed", False, str(exc))

    print("\n[5] it can still do real work while closed")
    try:
        reply = httpx.post(f"{BASE}/api/message",
                           json={"text": "What is my CPU usage right now?"},
                           timeout=120).json().get("reply", "")
        # Replies are written to be spoken, so numbers come out as words
        # ("eight point nine percent") -- looking for digits would fail on a
        # perfectly good answer.
        answered = "percent" in reply.lower() or "cpu" in reply.lower()
        check("answered a command with no window open", answered, reply[:120])
    except Exception as exc:
        check("answered a command with no window open", False, str(exc))

    print("\n[6] the window can be brought back")
    try:
        httpx.post(f"{BASE}/api/show", timeout=10)
        time.sleep(2.5)
        check("dashboard reopened on request", jarvis_window() != 0)
    except Exception as exc:
        check("dashboard reopened on request", False, str(exc))

    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(main())
