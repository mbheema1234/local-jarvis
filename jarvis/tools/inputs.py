"""Keyboard and mouse automation.

These drive the real cursor and keyboard, so everything here is at least
MODERATE risk -- a mistyped hotkey lands in whatever window has focus.
"""

from __future__ import annotations

from typing import Literal

from ..log import get
from ..security import Risk
from .registry import tool

log = get("jarvis.tools.inputs")

# Names the model is likely to produce, mapped to what pyautogui expects.
_KEY_ALIASES = {
    "win": "win", "windows": "win", "cmd": "win", "meta": "win",
    "control": "ctrl", "escape": "esc", "return": "enter",
    "pgup": "pageup", "pgdn": "pagedown", "del": "delete", "ins": "insert",
}


def _normalise(key: str) -> str:
    key = key.strip().lower()
    return _KEY_ALIASES.get(key, key)


def _pyautogui():
    import pyautogui

    # Never let a stray corner-of-screen cursor kill the assistant mid-task.
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.02
    return pyautogui


def virtual_screen() -> tuple[int, int, int, int]:
    """The bounding box of *all* monitors: (left, top, width, height).

    pyautogui.size() reports only the primary display, so bounds-checking
    against it silently rejects every click on a second monitor.
    """
    import ctypes

    user32 = ctypes.windll.user32
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def on_screen(x: int, y: int) -> bool:
    left, top, width, height = virtual_screen()
    return left <= x < left + width and top <= y < top + height


@tool(
    risk=Risk.MODERATE,
    params={
        "text": "The text to type into the focused window.",
        "interval": "Seconds between keystrokes; raise it for slow apps.",
    },
    summary=lambda a: f"Type {len(a.get('text', ''))} characters",
    tags=["input"],
)
def type_text(text: str, interval: float = 0.01) -> dict:
    """Type text into whatever window currently has focus."""
    pyautogui = _pyautogui()
    pyautogui.write(text, interval=max(0.0, min(interval, 0.3)))
    return {"typed": len(text)}


@tool(
    risk=Risk.MODERATE,
    params={"keys": "Keys to press together, e.g. ['ctrl','s'] or ['alt','tab']."},
    summary=lambda a: "Press " + "+".join(a.get("keys", []) or ["?"]),
    tags=["input"],
)
def press_keys(keys: list[str]) -> dict:
    """Press a key combination in the focused window."""
    if not keys:
        return {"ok": False, "error": "No keys given."}
    pyautogui = _pyautogui()
    combo = [_normalise(k) for k in keys]
    if len(combo) == 1:
        pyautogui.press(combo[0])
    else:
        pyautogui.hotkey(*combo)
    return {"pressed": "+".join(combo)}


@tool(
    risk=Risk.MODERATE,
    params={
        "x": "Screen X coordinate. Omit to click where the cursor already is.",
        "y": "Screen Y coordinate.",
        "button": "Which mouse button.",
        "clicks": "Number of clicks (2 for a double-click).",
    },
    summary=lambda a: f"{a.get('button', 'left').title()}-click at ({a.get('x', 'cursor')}, {a.get('y', '')})",
    tags=["input"],
)
def mouse_click(
    x: int | None = None,
    y: int | None = None,
    button: Literal["left", "right", "middle"] = "left",
    clicks: int = 1,
) -> dict:
    """Click the mouse, optionally moving to a screen coordinate first."""
    pyautogui = _pyautogui()
    if x is not None and y is not None:
        if not on_screen(x, y):
            left, top, width, height = virtual_screen()
            return {
                "ok": False,
                "error": f"({x}, {y}) is outside the desktop "
                         f"({width}x{height} from ({left},{top})).",
            }
        pyautogui.click(x=x, y=y, button=button, clicks=max(1, min(clicks, 3)))
    else:
        pyautogui.click(button=button, clicks=max(1, min(clicks, 3)))
    return {"clicked": button, "at": [x, y]}


@tool(
    risk=Risk.MODERATE,
    params={"x": "Target X coordinate.", "y": "Target Y coordinate.",
            "duration": "Seconds the movement should take."},
    summary=lambda a: f"Move cursor to ({a.get('x')}, {a.get('y')})",
    tags=["input"],
)
def mouse_move(x: int, y: int, duration: float = 0.2) -> dict:
    """Move the mouse cursor to a screen coordinate."""
    pyautogui = _pyautogui()
    pyautogui.moveTo(x, y, duration=max(0.0, min(duration, 2.0)))
    return {"at": [x, y]}


@tool(
    risk=Risk.MODERATE,
    params={"amount": "Scroll amount; positive scrolls up, negative scrolls down."},
    summary=lambda a: f"Scroll {'up' if a.get('amount', 0) > 0 else 'down'}",
    tags=["input"],
)
def scroll(amount: int = -3) -> dict:
    """Scroll the window under the cursor."""
    pyautogui = _pyautogui()
    pyautogui.scroll(amount * 120)
    return {"scrolled": amount}


@tool(risk=Risk.SAFE, summary="Read the cursor position", tags=["input"])
def cursor_position() -> dict:
    """Report the cursor position and the layout of all monitors."""
    pyautogui = _pyautogui()
    x, y = pyautogui.position()
    left, top, width, height = virtual_screen()
    primary = pyautogui.size()
    return {
        "x": x,
        "y": y,
        "primary_screen": [primary[0], primary[1]],
        "desktop_origin": [left, top],
        "desktop_size": [width, height],
    }
