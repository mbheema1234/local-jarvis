"""Verify the wake listener hands the microphone over cleanly.

The listener holds the mic exclusively while armed, so it must release it the
moment a turn starts and re-arm once the turn ends. Getting this wrong either
deadlocks the recorder or leaves Jarvis listening to its own voice.

    uv run python scripts/check_wake_handoff.py [port]
"""

from __future__ import annotations

import sys
import time

import httpx
import psutil

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
BASE = f"http://127.0.0.1:{PORT}"

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def wake_state() -> dict:
    return httpx.get(f"{BASE}/api/wake", timeout=10).json()


def pipeline_state() -> str:
    return httpx.get(f"{BASE}/api/state", timeout=15).json()["state"]


def wait_until(predicate, timeout: float = 45.0, interval: float = 0.4) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def main() -> int:
    print("\n[1] listener is armed at rest")
    initial = wake_state()
    check("wake model available", initial["available"], initial.get("model", ""))
    check("listening while idle", initial["listening"])

    print("\n[2] CPU cost of always-on listening")
    proc = next(
        (p for p in psutil.process_iter(["name", "cmdline"])
         if p.info["name"] == "python.exe"
         and any("jarvis" in str(c) for c in (p.info["cmdline"] or []))),
        None,
    )
    if proc is None:
        check("found the Jarvis process", False)
    else:
        proc.cpu_percent(interval=None)
        time.sleep(6)
        cpu = proc.cpu_percent(interval=None) / psutil.cpu_count()
        rss = proc.memory_info().rss / 1e6
        check("idle CPU stays low", cpu < 12.0, f"{cpu:.1f}% of total, {rss:.0f} MB RSS")

    print("\n[3] mic is released when a turn starts")
    httpx.post(f"{BASE}/api/activate", json={}, timeout=15)
    released = wait_until(lambda: not wake_state()["listening"], timeout=10)
    check("listener released the mic", released,
          "recorder now owns the device" if released else "still holding it")

    print("\n[4] recorder actually got the device")
    # No one is speaking, so this ends via the no-speech path rather than
    # a device error -- which is exactly what proves the handoff worked.
    got_idle = wait_until(lambda: pipeline_state() == "idle", timeout=40)
    check("turn completed cleanly", got_idle, f"state={pipeline_state()}")

    print("\n[5] listener re-arms afterwards")
    rearmed = wait_until(lambda: wake_state()["listening"], timeout=15)
    check("listening again", rearmed)

    print("\n[6] toggle off and on")
    httpx.post(f"{BASE}/api/wake", json={"enabled": False}, timeout=10)
    off = wait_until(lambda: not wake_state()["listening"], timeout=10)
    check("mute stops the listener", off)

    httpx.post(f"{BASE}/api/wake", json={"enabled": True}, timeout=10)
    back = wait_until(lambda: wake_state()["listening"], timeout=15)
    check("unmute restarts it", back)

    print(f"\n{'=' * 58}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(main())
