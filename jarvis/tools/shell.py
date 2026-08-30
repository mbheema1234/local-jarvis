"""Escape hatch: running a PowerShell command.

This is the one tool that can do something the other tools have not
anticipated, so it is the most tightly fenced: HIGH risk (confirmation
required by default), screened against a forbidden-pattern list, and run with
no shell elevation and a hard timeout.
"""

from __future__ import annotations

from ..log import get
from ..security import Risk, guard_shell_command
from ..winutil import run_powershell
from .registry import tool

log = get("jarvis.tools.shell")


@tool(
    risk=Risk.HIGH,
    params={
        "command": "The PowerShell command to run. Keep it to a single, specific action.",
        "reason": "A short plain-English explanation of why this is needed, shown to the user.",
    },
    summary=lambda a: f"Run PowerShell: {(a.get('command') or '')[:110]}",
    tags=["shell"],
    precheck=lambda a: guard_shell_command(a.get("command", "")),
)
def run_powershell_command(command: str, reason: str = "") -> dict:
    """Run a PowerShell command as the current user.

    Use only when no dedicated tool covers the task. Commands that escalate
    privilege, disable security features, or destroy data are rejected outright.
    """
    guard_shell_command(command)
    log.info("running shell command: %s", command[:200])

    try:
        proc = run_powershell(command, timeout=45)
    except Exception as exc:
        return {"ok": False, "error": f"Command failed to start: {exc}"}

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": stdout[:4000],
        "stderr": stderr[:1500],
        "truncated": len(stdout) > 4000,
    }
