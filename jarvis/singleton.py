"""Single-instance enforcement.

Jarvis holds the microphone and starts at login, so a second copy is never
what anyone wants: two wake-word listeners fight over the same input device,
each pins several hundred megabytes of speech models, and one "Hey Jarvis"
fires twice.

A named mutex is the reliable Windows way to detect this -- it is owned by the
process and released automatically if that process is killed, so a crash can
never leave a stale lock behind the way a lockfile would.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import httpx

from .log import get

log = get("jarvis.singleton")

# Session-local: two different users logged into the same machine should each
# get their own Jarvis.
MUTEX_NAME = "Local\\JarvisDesktopAssistant.SingleInstance"
_ERROR_ALREADY_EXISTS = 183

_handle: wintypes.HANDLE | None = None


def acquire() -> bool:
    """Claim the single-instance lock. False means Jarvis is already running."""
    global _handle

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]

    handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    last_error = kernel32.GetLastError()

    if not handle:
        log.warning("could not create the instance mutex; continuing anyway")
        return True

    if last_error == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False

    # Held for the lifetime of the process; Windows releases it on exit.
    _handle = handle
    return True


def release() -> None:
    global _handle
    if _handle:
        ctypes.windll.kernel32.ReleaseMutex(_handle)
        ctypes.windll.kernel32.CloseHandle(_handle)
        _handle = None


def find_running(start_port: int, span: int = 20) -> int | None:
    """Locate the port the existing instance is serving on."""
    for port in range(start_port, start_port + span):
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/api/ping", timeout=1.5)
            if response.status_code == 200 and response.json().get("app") == "jarvis":
                return port
        except Exception:
            continue
    return None


def show_existing(port: int) -> bool:
    """Ask the instance that is already running to bring its window forward."""
    try:
        response = httpx.post(f"http://127.0.0.1:{port}/api/show", timeout=8.0)
        # A 200 alone is not proof: the endpoint reports failure in the body
        # when there is no window to raise.
        return response.status_code == 200 and response.json().get("ok") is True
    except Exception:
        return False
