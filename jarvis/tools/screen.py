"""Seeing the screen.

Handles several monitors properly: each can be captured and described on its
own, and Jarvis can tell which is which by the windows sitting on them -- so
"the monitor with Discord on it" is something it can actually resolve.
"""

from __future__ import annotations

import base64
import io
import time
from typing import Any

from ..config import DATA_DIR, load
from ..log import get
from ..security import Risk
from .registry import tool

log = get("jarvis.tools.screen")

SHOTS_DIR = DATA_DIR / "screenshots"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Monitor geometry
# --------------------------------------------------------------------------


def _monitors() -> list[dict[str, Any]]:
    """Physical monitors, 1-indexed to match what the tools accept."""
    import mss

    with mss.mss() as sct:
        return [
            {
                "monitor": i,
                "width": m["width"],
                "height": m["height"],
                "left": m["left"],
                "top": m["top"],
                "orientation": "portrait" if m["height"] > m["width"] else "landscape",
            }
            for i, m in enumerate(sct.monitors[1:], start=1)
        ]


def _windows_by_monitor() -> dict[int, list[dict[str, str]]]:
    """Map each monitor to the visible windows whose centre falls on it."""
    import psutil
    import pygetwindow as gw

    try:
        import win32process
    except ImportError:  # pragma: no cover
        win32process = None

    monitors = _monitors()
    buckets: dict[int, list[dict[str, str]]] = {m["monitor"]: [] for m in monitors}

    for win in gw.getAllWindows():
        title = (win.title or "").strip()
        if not title or not win.visible or win.width < 60 or win.height < 60:
            continue
        centre_x = win.left + win.width // 2
        centre_y = win.top + win.height // 2

        for monitor in monitors:
            if not (monitor["left"] <= centre_x < monitor["left"] + monitor["width"]
                    and monitor["top"] <= centre_y < monitor["top"] + monitor["height"]):
                continue
            process = ""
            if win32process is not None:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(win._hWnd)
                    process = psutil.Process(pid).name()
                except Exception:
                    pass
            buckets[monitor["monitor"]].append({"title": title[:90], "process": process})
            break

    return buckets


def _capture(monitor: int = 0, max_width: int = 1500) -> tuple[bytes, tuple[int, int]]:
    """Grab a monitor as PNG bytes. ``monitor=0`` means every screen at once."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        available = sct.monitors
        index = monitor if 0 <= monitor < len(available) else 0
        raw = sct.grab(available[index])

    image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), image.size


async def _ask_vision(prompt: str, png: bytes, max_tokens: int = 700) -> str:
    """Send an image and a question to the vision model."""
    from ..llm.openrouter import OpenRouterError, client

    encoded = base64.b64encode(png).decode("ascii")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        ],
    }]
    try:
        result = await client.chat(
            messages, model=load().models.vision,
            max_tokens=max_tokens, temperature=0.1,
        )
    except OpenRouterError as exc:
        raise RuntimeError(f"Vision model unavailable: {exc}") from exc
    return (result["message"].get("content") or "").strip()


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@tool(risk=Risk.SAFE, summary="List the monitors", tags=["screen"])
def list_monitors() -> dict:
    """List every monitor and which windows are open on each.

    Use this when the user refers to "my monitor" or "the other screen" without
    saying which. The window lists let you ask them a concrete question, like
    "the portrait one with Discord, or the main one with Firefox?"
    """
    monitors = _monitors()
    windows = _windows_by_monitor()

    for monitor in monitors:
        entries = windows.get(monitor["monitor"], [])
        monitor["window_count"] = len(entries)
        monitor["windows"] = [w["title"] for w in entries[:8]]
        monitor["apps"] = sorted({w["process"] for w in entries if w["process"]})
        if monitor["left"] == 0 and monitor["top"] == 0:
            monitor["note"] = "primary"

    return {"count": len(monitors), "monitors": monitors}


@tool(
    risk=Risk.SAFE,
    params={
        "monitor": "Which monitor to capture. 0 captures all of them side by side.",
        "save": "Also write the capture to data/screenshots.",
    },
    summary=lambda a: f"Screenshot monitor {a.get('monitor', 0)}",
    tags=["screen"],
)
def screenshot(monitor: int = 0, save: bool = True) -> dict:
    """Capture a monitor. Use see_screen if you need to know what is on it."""
    data, size = _capture(monitor)
    result = {"monitor": monitor, "width": size[0], "height": size[1], "bytes": len(data)}
    if save:
        path = SHOTS_DIR / f"screen{monitor}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        path.write_bytes(data)
        result["path"] = str(path)
    return result


@tool(
    risk=Risk.SAFE,
    params={
        "question": "What you want to know about what is on screen.",
        "monitor": "Which monitor to look at. 0 looks at all of them at once.",
    },
    summary=lambda a: f"Look at monitor {a.get('monitor', 0)}",
    tags=["screen"],
)
async def see_screen(
    question: str = "What is on this screen?",
    monitor: int = 0,
) -> dict:
    """Look at a monitor and answer a question about what is displayed.

    Use whenever the user asks what you can see, or refers to something they
    are looking at. With three monitors, start with monitor 0 to see everything
    at once, then look at a single one for detail.
    """
    monitors = _monitors()
    if monitor > len(monitors):
        return {
            "ok": False,
            "error": f"There is no monitor {monitor}; there are {len(monitors)}.",
        }

    png, size = _capture(monitor)
    where = ("all three monitors side by side" if monitor == 0
             else f"monitor {monitor}")
    prompt = (
        f"{question}\n\n"
        f"This is a screenshot of {where} on a Windows PC. Describe what you "
        f"actually see: the applications, windows and content. Be specific and "
        f"concrete. Answer in two or three sentences, as if speaking aloud -- "
        f"no markdown and no lists."
    )

    try:
        answer = await _ask_vision(prompt, png)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "monitor": monitor,
        "answer": answer,
        "captured": f"{size[0]}x{size[1]}",
        "monitor_count": len(monitors),
    }


@tool(
    risk=Risk.SAFE,
    params={
        "target": "What to locate, described as it appears, e.g. 'the blue Compose button'.",
        "monitor": "Which monitor to search. Use a specific one for accuracy.",
    },
    summary=lambda a: f"Find {a.get('target', '?')!r} on screen",
    tags=["screen"],
)
async def find_on_screen(target: str, monitor: int = 1) -> dict:
    """Locate something on screen and return coordinates you can click.

    This is the fallback for apps that expose no controls to inspect_app. Prefer
    inspect_app and click_element when they work -- they are exact, whereas this
    is the model's best visual estimate. Always confirm the result with
    see_screen after clicking.
    """
    monitors = _monitors()
    if not 1 <= monitor <= len(monitors):
        return {"ok": False, "error": f"Monitor {monitor} does not exist."}

    geometry = monitors[monitor - 1]
    png, (shot_w, shot_h) = _capture(monitor)

    prompt = (
        f"Find this on the screenshot: {target}\n\n"
        f"The image is {shot_w} by {shot_h} pixels. Reply with ONLY the pixel "
        f"coordinates of its centre, as two integers separated by a comma, "
        f"like: 640,360\n"
        f"If it is not visible, reply with exactly: NOT_FOUND"
    )

    try:
        answer = await _ask_vision(prompt, png, max_tokens=40)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}

    if "NOT_FOUND" in answer.upper():
        return {"ok": False, "error": f"Could not see {target!r} on monitor {monitor}."}

    import re

    match = re.search(r"(\d+)\s*,\s*(\d+)", answer)
    if not match:
        return {"ok": False, "error": f"Vision model gave no coordinates: {answer[:80]!r}"}

    shot_x, shot_y = int(match.group(1)), int(match.group(2))

    # The capture was downscaled, so map back to real desktop coordinates.
    scale_x = geometry["width"] / shot_w
    scale_y = geometry["height"] / shot_h
    screen_x = geometry["left"] + int(shot_x * scale_x)
    screen_y = geometry["top"] + int(shot_y * scale_y)

    return {
        "target": target,
        "x": screen_x,
        "y": screen_y,
        "monitor": monitor,
        "note": "Estimated visually. Click with mouse_click, then verify with see_screen.",
    }


@tool(
    risk=Risk.SAFE,
    params={"question": "What you want to know about the screen."},
    summary="Look at the screen",
    tags=["screen"],
)
async def describe_screen(question: str = "What is on the screen right now?") -> dict:
    """Look at all monitors at once and answer a question about them.

    Kept for convenience; see_screen can target one monitor.
    """
    return await see_screen(question=question, monitor=0)
