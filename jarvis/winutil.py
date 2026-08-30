"""Small Windows helpers shared across tools."""

from __future__ import annotations

import json
import subprocess
from typing import Any

# Keeps helper processes from flashing a console window over whatever you're
# doing. Jarvis runs windowless, and so should anything it spawns.
NO_WINDOW = 0x08000000

_PS = [
    "powershell.exe",
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy", "Bypass",
    "-Command",
]


def run_powershell(script: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run a PowerShell snippet unelevated and capture its output."""
    return subprocess.run(
        [*_PS, script],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=NO_WINDOW,
        encoding="utf-8",
        errors="replace",
    )


def powershell_json(script: str, timeout: float = 30.0) -> Any:
    """Run PowerShell and parse its output as JSON.

    The snippet is wrapped so a single result still comes back as a list,
    which removes the usual PowerShell scalar/array ambiguity.
    """
    wrapped = f"$ProgressPreference='SilentlyContinue'; @({script}) | ConvertTo-Json -Depth 5 -Compress"
    proc = run_powershell(wrapped, timeout=timeout)
    text = (proc.stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]
