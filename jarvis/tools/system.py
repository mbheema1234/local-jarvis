"""System state: audio, display, power, clipboard, stats, and notifications."""

from __future__ import annotations

import datetime as dt
import shutil
from typing import Literal

import psutil

from ..log import get
from ..security import Risk
from ..winutil import powershell_json, run_powershell
from .registry import tool

log = get("jarvis.tools.system")


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------


def _volume_interface():
    """The default output device's IAudioEndpointVolume.

    Tools run on worker threads, which have no COM apartment of their own, so
    COM has to be initialised here rather than at import time.
    """
    import comtypes

    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # already initialised on this thread

    from pycaw.utils import AudioUtilities

    device = AudioUtilities.GetSpeakers()

    # pycaw >= 2023 exposes the interface directly; older releases require
    # activating it off the raw IMMDevice.
    endpoint = getattr(device, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint

    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume

    raw = getattr(device, "_dev", device)
    interface = raw.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


@tool(risk=Risk.SAFE, summary="Check the volume", tags=["system", "audio"])
def get_volume() -> dict:
    """Report the current master output volume and mute state."""
    volume = _volume_interface()
    return {
        "level": round(volume.GetMasterVolumeLevelScalar() * 100),
        "muted": bool(volume.GetMute()),
    }


@tool(
    risk=Risk.MODERATE,
    params={"level": "Target volume from 0 to 100."},
    summary=lambda a: f"Set volume to {a.get('level', '?')}%",
    tags=["system", "audio"],
)
def set_volume(level: int) -> dict:
    """Set the master output volume."""
    level = max(0, min(100, int(level)))
    volume = _volume_interface()
    volume.SetMasterVolumeLevelScalar(level / 100.0, None)
    if level > 0 and volume.GetMute():
        volume.SetMute(0, None)
    return {"level": level}


@tool(
    risk=Risk.MODERATE,
    params={"muted": "True to mute, False to unmute."},
    summary=lambda a: "Mute audio" if a.get("muted", True) else "Unmute audio",
    tags=["system", "audio"],
)
def set_mute(muted: bool = True) -> dict:
    """Mute or unmute system audio."""
    volume = _volume_interface()
    volume.SetMute(1 if muted else 0, None)
    return {"muted": muted}


@tool(
    risk=Risk.MODERATE,
    params={"action": "Which media key to press."},
    summary=lambda a: f"Media: {a.get('action', '?')}",
    tags=["system", "media"],
)
def media_control(
    action: Literal["play_pause", "next", "previous", "stop", "volume_up", "volume_down"],
) -> dict:
    """Send a media key. Works with Spotify, YouTube, and most players."""
    import win32api
    import win32con

    codes = {
        "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
        "volume_up": win32con.VK_VOLUME_UP, "volume_down": win32con.VK_VOLUME_DOWN,
    }
    key = codes[action]
    win32api.keybd_event(key, 0, 0, 0)
    win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
    return {"action": action}


# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------


@tool(
    risk=Risk.MODERATE,
    params={"level": "Brightness percentage from 0 to 100."},
    summary=lambda a: f"Set brightness to {a.get('level', '?')}%",
    tags=["system", "display"],
)
def set_brightness(level: int) -> dict:
    """Set display brightness. Only works on built-in/DDC-capable displays."""
    level = max(0, min(100, int(level)))
    proc = run_powershell(
        "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
        f".WmiSetBrightness(1,{level})"
    )
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "This display does not expose software brightness control "
                     "(common on desktop monitors).",
        }
    return {"level": level}


# --------------------------------------------------------------------------
# Power / session
# --------------------------------------------------------------------------


@tool(risk=Risk.MODERATE, summary="Lock the workstation", tags=["system", "power"])
def lock_workstation() -> dict:
    """Lock the PC, as Win+L does."""
    import ctypes

    ctypes.windll.user32.LockWorkStation()
    return {"locked": True}


@tool(
    risk=Risk.HIGH,
    params={"action": "Which power action to take."},
    summary=lambda a: f"{a.get('action', '?').title()} the PC",
    tags=["system", "power"],
)
def power_action(action: Literal["sleep", "shutdown", "restart", "sign_out"]) -> dict:
    """Sleep, shut down, restart, or sign out.

    Always confirm with the user first -- this closes their work.
    """
    commands = {
        # SetSuspendState is the only reliable unelevated sleep path.
        "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
        "shutdown": "shutdown.exe /s /t 5",
        "restart": "shutdown.exe /r /t 5",
        "sign_out": "shutdown.exe /l",
    }
    proc = run_powershell(commands[action])
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "").strip() or "Power action failed."}
    return {"action": action, "note": "Taking effect now."}


# --------------------------------------------------------------------------
# Clipboard
# --------------------------------------------------------------------------


@tool(risk=Risk.SAFE, summary="Read the clipboard", tags=["clipboard"])
def clipboard_read() -> dict:
    """Read the current text contents of the clipboard."""
    import pyperclip

    text = pyperclip.paste() or ""
    return {"text": text[:8000], "length": len(text)}


@tool(
    risk=Risk.MODERATE,
    params={"text": "Text to place on the clipboard."},
    summary="Write to the clipboard",
    tags=["clipboard"],
)
def clipboard_write(text: str) -> dict:
    """Replace the clipboard contents with the given text."""
    import pyperclip

    pyperclip.copy(text)
    return {"length": len(text)}


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------


def _gpu_stats() -> list[dict]:
    """GPU load via nvidia-smi, when an NVIDIA card is present."""
    if not shutil.which("nvidia-smi"):
        return []
    from ..winutil import NO_WINDOW
    import subprocess

    try:
        proc = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW,
        )
    except Exception:
        return []
    gpus = []
    for line in (proc.stdout or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 5:
            gpus.append({
                "name": parts[0], "load_percent": _to_int(parts[1]),
                "memory_used_mb": _to_int(parts[2]), "memory_total_mb": _to_int(parts[3]),
                "temperature_c": _to_int(parts[4]),
            })
    return gpus


def _to_int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def system_snapshot() -> dict:
    """Current machine vitals. Also used by the dashboard's live stats panel."""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    snapshot = {
        "cpu_percent": psutil.cpu_percent(interval=0.15),
        "cpu_cores": psutil.cpu_count(logical=True),
        "memory": {
            "used_gb": round(memory.used / 1e9, 1),
            "total_gb": round(memory.total / 1e9, 1),
            "percent": memory.percent,
        },
        "disk_c": {
            "used_gb": round(disk.used / 1e9, 1),
            "total_gb": round(disk.total / 1e9, 1),
            "percent": disk.percent,
        },
        "uptime_hours": round(
            (dt.datetime.now().timestamp() - psutil.boot_time()) / 3600, 1
        ),
        "gpus": _gpu_stats(),
        "time": dt.datetime.now().strftime("%A %d %B %Y, %H:%M"),
    }

    battery = psutil.sensors_battery()
    if battery is not None:
        snapshot["battery"] = {
            "percent": round(battery.percent),
            "plugged_in": battery.power_plugged,
        }
    return snapshot


@tool(risk=Risk.SAFE, summary="Check system stats", tags=["system"])
def get_system_stats() -> dict:
    """Report CPU, memory, disk, GPU, battery, uptime, and the current time."""
    return system_snapshot()


@tool(risk=Risk.SAFE, summary="List running processes", tags=["processes"])
def list_processes(top: int = 15) -> dict:
    """List the processes using the most memory right now."""
    procs = []
    for proc in psutil.process_iter(["name", "pid", "memory_info", "cpu_percent"]):
        try:
            info = proc.info
            procs.append({
                "name": info.get("name"),
                "pid": info.get("pid"),
                "memory_mb": round((info["memory_info"].rss if info.get("memory_info") else 0) / 1e6, 1),
            })
        except psutil.Error:
            continue
    procs.sort(key=lambda p: p["memory_mb"], reverse=True)
    return {"processes": procs[:top]}


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------


def _ps_single_quote(s: str) -> str:
    """Render `s` as a single-quoted PowerShell string literal.

    Unlike double-quoted PS strings, single-quoted ones never interpolate
    `$variable` references or `$(...)` subexpressions -- the only escaping
    a single-quoted literal needs is doubling embedded single quotes.
    """
    return "'" + s.replace("'", "''") + "'"


@tool(
    risk=Risk.SAFE,
    params={"title": "Notification heading.", "message": "Body text."},
    summary=lambda a: f"Notify: {a.get('title', '')}",
    tags=["system"],
)
def notify(title: str, message: str = "") -> dict:
    """Show a Windows toast notification."""
    # Titles/bodies can contain arbitrary user- or web-sourced text (this tool
    # is Risk.SAFE, so nothing prompts for confirmation before it runs). They
    # must land in the script as inert data, never as PowerShell syntax. A
    # single-quoted PS literal doesn't interpolate $variables or $(...)
    # subexpressions the way a double-quoted one does, so we escape into that
    # form rather than substituting quote characters into a double-quoted one.
    safe_title = _ps_single_quote(title.replace("\r", " ").replace("\n", " "))
    safe_message = _ps_single_quote(message.replace("\r", " ").replace("\n", " "))
    script = f"""
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $texts = $template.GetElementsByTagName("text")
    $texts.Item(0).AppendChild($template.CreateTextNode({safe_title})) > $null
    $texts.Item(1).AppendChild($template.CreateTextNode({safe_message})) > $null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Jarvis").Show($toast)
    """
    proc = run_powershell(script, timeout=15)
    if proc.returncode != 0:
        return {"ok": False, "error": "Could not post the notification."}
    return {"shown": True}


@tool(risk=Risk.SAFE, summary="Check network status", tags=["system"])
def network_status() -> dict:
    """Report the active network connection and its signal strength."""
    rows = powershell_json(
        "Get-NetConnectionProfile | Select-Object Name, InterfaceAlias, IPv4Connectivity"
    )
    io = psutil.net_io_counters()
    return {
        "connections": rows,
        "sent_gb": round(io.bytes_sent / 1e9, 2),
        "received_gb": round(io.bytes_recv / 1e9, 2),
    }
