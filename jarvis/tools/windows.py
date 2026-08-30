"""Window management: focus, snap, arrange, and list."""

from __future__ import annotations

from typing import Literal

from ..log import get
from ..security import Risk
from .registry import tool

log = get("jarvis.tools.windows")


def _find(title: str):
    """Best-effort window lookup: exact title, then substring, then fuzzy."""
    import pygetwindow as gw

    needle = title.casefold().strip()
    candidates = [w for w in gw.getAllWindows() if (w.title or "").strip() and w.visible]

    for win in candidates:
        if win.title.casefold() == needle:
            return win
    for win in candidates:
        if needle in win.title.casefold():
            return win

    from rapidfuzz import fuzz

    best, best_score = None, 0.0
    for win in candidates:
        score = fuzz.WRatio(needle, win.title.casefold())
        if score > best_score:
            best, best_score = win, score
    return best if best_score >= 65 else None


def _work_area() -> tuple[int, int, int, int]:
    """The usable desktop rectangle, excluding the taskbar."""
    import win32api
    import win32con

    try:
        monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        return left, top, right - left, bottom - top
    except Exception:
        import pyautogui

        width, height = pyautogui.size()
        return 0, 0, width, height


@tool(risk=Risk.SAFE, summary="List open windows", tags=["windows"])
def list_windows() -> dict:
    """List every visible window, with the active one flagged."""
    import pygetwindow as gw

    try:
        active = gw.getActiveWindow()
        active_title = active.title if active else None
    except Exception:
        active_title = None

    windows = [
        {
            "title": w.title,
            "minimized": w.isMinimized,
            "maximized": w.isMaximized,
            "size": [w.width, w.height],
            "active": w.title == active_title,
        }
        for w in gw.getAllWindows()
        if (w.title or "").strip() and w.visible
    ]
    return {"count": len(windows), "windows": windows}


@tool(
    risk=Risk.MODERATE,
    params={"title": "Window title (or part of it) to bring to the front."},
    summary=lambda a: f"Focus {a.get('title', '?')!r}",
    tags=["windows"],
)
def focus_window(title: str) -> dict:
    """Bring a window to the foreground and give it keyboard focus."""
    win = _find(title)
    if win is None:
        return {"ok": False, "error": f"No visible window matching {title!r}."}
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
    except Exception:
        # activate() raises when another process holds the foreground lock;
        # a minimize/restore cycle reliably wins focus back.
        try:
            win.minimize()
            win.restore()
        except Exception as exc:
            return {"ok": False, "error": f"Could not focus {win.title!r}: {exc}"}
    return {"focused": win.title}


@tool(
    risk=Risk.MODERATE,
    params={"title": "Window title (or part of it).", "action": "What to do with it."},
    summary=lambda a: f"{a.get('action', '?').title()} {a.get('title', '?')!r}",
    tags=["windows"],
)
def window_action(
    title: str,
    action: Literal["minimize", "maximize", "restore", "close"],
) -> dict:
    """Minimize, maximize, restore, or close a window."""
    win = _find(title)
    if win is None:
        return {"ok": False, "error": f"No visible window matching {title!r}."}
    try:
        getattr(win, action)()
    except Exception as exc:
        return {"ok": False, "error": f"{action} failed on {win.title!r}: {exc}"}
    return {"window": win.title, "action": action}


@tool(
    risk=Risk.MODERATE,
    params={"title": "Window title (or part of it).", "position": "Where to put it."},
    summary=lambda a: f"Snap {a.get('title', '?')!r} to {a.get('position', '?')}",
    tags=["windows"],
)
def snap_window(
    title: str,
    position: Literal["left", "right", "top", "bottom", "fullscreen", "center"],
) -> dict:
    """Snap a window to half the screen, maximize it, or centre it."""
    win = _find(title)
    if win is None:
        return {"ok": False, "error": f"No visible window matching {title!r}."}

    x, y, width, height = _work_area()
    layouts = {
        "left": (x, y, width // 2, height),
        "right": (x + width // 2, y, width // 2, height),
        "top": (x, y, width, height // 2),
        "bottom": (x, y + height // 2, width, height // 2),
        "fullscreen": (x, y, width, height),
        "center": (x + width // 4, y + height // 8, width // 2, int(height * 0.75)),
    }
    left, top, new_width, new_height = layouts[position]

    try:
        if win.isMinimized:
            win.restore()
        if win.isMaximized and position != "fullscreen":
            win.restore()
        win.moveTo(left, top)
        win.resizeTo(new_width, new_height)
    except Exception as exc:
        return {"ok": False, "error": f"Could not snap {win.title!r}: {exc}"}
    return {"window": win.title, "position": position}


@tool(
    risk=Risk.MODERATE,
    params={"titles": "Window titles to tile, left to right."},
    summary=lambda a: "Tile " + ", ".join(a.get("titles", []) or ["?"]),
    tags=["windows"],
)
def tile_windows(titles: list[str]) -> dict:
    """Tile two to four windows side by side across the screen."""
    if not 2 <= len(titles) <= 4:
        return {"ok": False, "error": "Tiling needs between 2 and 4 windows."}

    x, y, width, height = _work_area()
    column = width // len(titles)
    tiled, missing = [], []

    for i, title in enumerate(titles):
        win = _find(title)
        if win is None:
            missing.append(title)
            continue
        try:
            if win.isMinimized or win.isMaximized:
                win.restore()
            win.moveTo(x + i * column, y)
            win.resizeTo(column, height)
            tiled.append(win.title)
        except Exception as exc:
            missing.append(f"{title} ({exc})")

    return {"ok": bool(tiled), "tiled": tiled, "not_found": missing}


@tool(risk=Risk.MODERATE, summary="Minimize everything (show desktop)", tags=["windows"])
def show_desktop() -> dict:
    """Minimize all windows to reveal the desktop."""
    import pyautogui

    pyautogui.hotkey("win", "d")
    return {"done": True}
