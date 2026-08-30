"""Launching, listing, and closing applications."""

from __future__ import annotations

import time

import psutil

from ..appindex import index
from ..log import get
from ..security import Risk, guard_process
from .registry import tool

log = get("jarvis.tools.apps")


@tool(
    risk=Risk.SAFE,
    params={"query": "Substring to filter app names by. Omit to list everything."},
    summary="List installed applications",
    tags=["apps"],
)
def list_apps(query: str = "", limit: int = 40) -> dict:
    """List applications installed on this PC that Jarvis can launch."""
    if query:
        hits = [entry for entry, score in index.search(query, limit=limit) if score >= 55]
    else:
        hits = [e for e in index.entries if not e.is_junk][:limit]
    return {
        "count": len(hits),
        "apps": [{"name": e.name, "kind": e.kind} for e in hits],
    }


@tool(
    risk=Risk.MODERATE,
    params={"name": "The application to open, e.g. 'Epic Games Launcher', 'Spotify', 'NVIDIA App'."},
    summary=lambda a: f"Open {a.get('name', '?')}",
    tags=["apps"],
)
def launch_app(name: str) -> dict:
    """Open an installed application by name.

    Handles fuzzy names -- 'my nvidia app' finds 'NVIDIA App'.
    """
    entry = index.resolve(name)
    if entry is None:
        suggestions = [e.name for e, score in index.search(name, limit=5) if score >= 45]
        return {
            "ok": False,
            "error": f"No installed app matches {name!r}.",
            "did_you_mean": suggestions,
        }

    index.launch(entry)
    log.info("launched %s (%s)", entry.name, entry.app_id)
    return {"launched": entry.name, "kind": entry.kind}


@tool(
    risk=Risk.MODERATE,
    params={"names": "The applications to open, in order."},
    summary=lambda a: "Open " + ", ".join(a.get("names", []) or ["?"]),
    tags=["apps"],
)
def launch_apps(names: list[str]) -> dict:
    """Open several applications at once.

    Prefer this over repeated launch_app calls when the user names more than
    one app in a single breath.
    """
    launched, failed = [], []
    for name in names:
        entry = index.resolve(name)
        if entry is None:
            failed.append(name)
            continue
        index.launch(entry)
        launched.append(entry.name)
        time.sleep(0.4)  # let the shell breathe between launches
    return {"ok": not failed, "launched": launched, "not_found": failed}


def _user_windows() -> list[dict]:
    """Visible top-level windows belonging to the current user, with owners."""
    import pygetwindow as gw

    try:
        import win32process
    except ImportError:  # pragma: no cover
        win32process = None

    out = []
    for win in gw.getAllWindows():
        title = (win.title or "").strip()
        if not title or not win.visible:
            continue
        info = {"title": title, "handle": win._hWnd, "process": None, "pid": None}
        if win32process is not None:
            try:
                _, pid = win32process.GetWindowThreadProcessId(win._hWnd)
                info["pid"] = pid
                info["process"] = psutil.Process(pid).name()
            except Exception:
                pass
        out.append(info)
    return out


@tool(
    risk=Risk.SAFE,
    summary="List running applications",
    tags=["apps"],
)
def list_running_apps() -> dict:
    """List applications currently open, by window title."""
    windows = _user_windows()
    return {"count": len(windows), "windows": windows}


@tool(
    risk=Risk.MODERATE,
    params={"name": "The application or window to close."},
    summary=lambda a: f"Close {a.get('name', '?')}",
    tags=["apps"],
)
def close_app(name: str) -> dict:
    """Close an application gracefully by window title or process name.

    Asks the app to close (as clicking the X would); it does not force-kill.
    """
    import win32con
    import win32gui

    needle = name.casefold()
    closed = []
    for win in _user_windows():
        haystack = f"{win['title']} {win.get('process') or ''}".casefold()
        if needle in haystack:
            try:
                win32gui.PostMessage(win["handle"], win32con.WM_CLOSE, 0, 0)
                closed.append(win["title"])
            except Exception as exc:
                log.warning("could not close %s: %s", win["title"], exc)

    if not closed:
        return {"ok": False, "error": f"No open window matches {name!r}."}
    return {"closed": closed}


@tool(
    risk=Risk.HIGH,
    params={"name": "Process or window name to force-terminate."},
    summary=lambda a: f"Force-quit {a.get('name', '?')}",
    tags=["apps", "processes"],
)
def force_quit(name: str) -> dict:
    """Force-terminate an unresponsive application.

    Unsaved work is lost, so prefer close_app. Only reaches processes owned by
    the current user.
    """
    needle = name.casefold()
    killed, refused = [], []
    for proc in psutil.process_iter(["name", "pid"]):
        pname = (proc.info.get("name") or "")
        if needle not in pname.casefold():
            continue
        try:
            guard_process(proc)
            proc.terminate()
            killed.append(f"{pname} (pid {proc.pid})")
        except Exception as exc:
            refused.append(f"{pname}: {exc}")

    if not killed:
        return {"ok": False, "error": f"Nothing terminated for {name!r}.", "refused": refused}
    return {"terminated": killed, "refused": refused}


@tool(
    risk=Risk.SAFE,
    summary="Rebuild the installed-app index",
    tags=["apps"],
)
def refresh_app_index() -> dict:
    """Re-scan the Start Menu. Use after installing something new."""
    return {"indexed": index.refresh()}
