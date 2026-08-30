"""Hard safety rails.

These are deliberately *not* configurable from the dashboard or reachable by
the model. They encode the one promise Jarvis makes about itself: it operates
with exactly the privileges of the logged-in user and never tries to gain more.
"""

from __future__ import annotations

import ctypes
import os
import re
from pathlib import Path

import psutil

from ..config import load


class GuardError(RuntimeError):
    """A hard safety rule refused the operation."""


class ElevationError(GuardError):
    pass


# --------------------------------------------------------------------------
# Privilege
# --------------------------------------------------------------------------


def is_elevated() -> bool:
    """True if this process is running with an elevated (admin) token."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def assert_not_elevated() -> None:
    """Refuse to run as administrator.

    Running unelevated is what makes the rest of the design meaningful: UAC
    stays a real boundary, so even a fully compromised prompt cannot touch
    protected system state without you personally clicking through a UAC
    dialog that Jarvis has no way to press.
    """
    if not load().security.refuse_elevation:
        return
    if is_elevated():
        raise ElevationError(
            "Jarvis is running as Administrator and will not start.\n"
            "It is designed to run as your normal user so that UAC remains a "
            "real security boundary. Close this and start it without "
            "'Run as administrator'."
        )


# --------------------------------------------------------------------------
# Shell command screening
# --------------------------------------------------------------------------

# Patterns that either escalate privilege, disable protection, or destroy data
# at a scale no assistant should reach for on a voice command.
_FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    (r"\brunas\b", "privilege escalation"),
    (r"-Verb\s+RunAs", "privilege escalation"),
    (r"\bStart-Process\b[^\n]*\bRunAs\b", "privilege escalation"),
    (r"\bpsexec\b", "privilege escalation"),
    (r"Set-MpPreference|Add-MpPreference", "tampering with Defender"),
    (r"\bbcdedit\b", "boot configuration"),
    (r"\bdiskpart\b", "disk partitioning"),
    (r"\bformat\s+[a-zA-Z]:", "disk format"),
    (r"vssadmin\s+delete", "deleting shadow copies"),
    (r"\bcipher\s+/w", "secure wipe"),
    (r"reg(\.exe)?\s+(add|delete)\s+[\"']?HK(LM|EY_LOCAL_MACHINE)", "HKLM registry write"),
    (r"New-ItemProperty[^\n]*HKLM:", "HKLM registry write"),
    (r"\bnet\s+user\b[^\n]*/add", "account creation"),
    (r"\bnet\s+localgroup\b[^\n]*/add", "group membership change"),
    (r"Add-LocalGroupMember", "group membership change"),
    (r"\bschtasks\b[^\n]*/create", "scheduled task creation"),
    (r"Set-ExecutionPolicy\s+(Unrestricted|Bypass)", "execution policy change"),
    (r"\bicacls\b[^\n]*/grant", "ACL modification"),
    (r"\btakeown\b", "taking ownership of system files"),
    (r"Stop-Computer|shutdown\s+/[rs]", "use the power_action tool instead"),
    (r"Remove-Item[^\n]*\b[Cc]:\\+(Windows|Program Files)", "deleting system files"),
    (r"(rd|rmdir)\s+/s[^\n]*\b[a-zA-Z]:\\\s*$", "recursive root delete"),
    (r"Invoke-Expression[^\n]*(Invoke-WebRequest|iwr|curl|DownloadString)", "remote code execution"),
    (r"iex\s*\(", "remote code execution"),
    (r"certutil[^\n]*-urlcache", "remote payload download"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in _FORBIDDEN_PATTERNS]


def guard_shell_command(command: str) -> None:
    """Raise if a shell command matches a forbidden pattern."""
    if not command or not command.strip():
        raise GuardError("Empty command.")
    if len(command) > 4000:
        raise GuardError("Command is implausibly long; refusing.")
    for pattern, why in _COMPILED:
        if pattern.search(command):
            raise GuardError(
                f"Refused: this command looks like {why}. Jarvis does not run "
                f"commands that escalate privilege, disable security features, "
                f"or destroy data at scale. Run it yourself if you truly need it."
            )


# --------------------------------------------------------------------------
# Filesystem
# --------------------------------------------------------------------------

_PROTECTED_ROOTS = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    Path(os.environ.get("ProgramData", r"C:\ProgramData")),
]


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def guard_write_path(path: str | Path) -> Path:
    """Validate a path Jarvis is about to write to, move, or delete.

    Reads are unrestricted; only mutation is fenced.
    """
    target = Path(path).expanduser()
    try:
        resolved = target.resolve()
    except OSError as exc:
        raise GuardError(f"Cannot resolve path: {exc}") from exc

    if str(resolved).startswith("\\\\"):
        raise GuardError("Refusing to modify network (UNC) paths.")

    for protected in _PROTECTED_ROOTS:
        if _is_within(resolved, protected):
            raise GuardError(
                f"Refusing to modify {resolved}: it is inside a protected "
                f"system directory ({protected})."
            )

    # Never let a mutation land on a drive root.
    if resolved.parent == resolved:
        raise GuardError("Refusing to modify a drive root.")

    allowed = [Path(r).expanduser().resolve() for r in load().security.writable_roots]
    if allowed and not any(_is_within(resolved, root) for root in allowed):
        pretty = ", ".join(str(r) for r in allowed)
        raise GuardError(
            f"Refusing to modify {resolved}: it is outside the allowed roots "
            f"({pretty}). Add it under security.writable_roots in settings if "
            f"you want Jarvis to reach it."
        )
    return resolved


# --------------------------------------------------------------------------
# Processes
# --------------------------------------------------------------------------

# Killing any of these either logs you out or destabilises the session.
_CRITICAL_PROCESSES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
    "fontdrvhost.exe", "dwm.exe", "ctfmon.exe", "sihost.exe", "audiodg.exe",
    "memory compression", "python.exe", "pythonw.exe",
}


def guard_process(proc: psutil.Process) -> None:
    """Only allow terminating non-critical processes owned by the current user."""
    try:
        name = (proc.name() or "").lower()
    except psutil.Error as exc:
        raise GuardError(f"Cannot inspect process: {exc}") from exc

    if name in _CRITICAL_PROCESSES:
        raise GuardError(f"Refusing to terminate critical system process '{name}'.")

    if proc.pid in (0, 4):
        raise GuardError("Refusing to terminate a kernel process.")

    if proc.pid == os.getpid():
        raise GuardError("Refusing to terminate Jarvis itself.")

    try:
        owner = (proc.username() or "").lower()
    except psutil.AccessDenied:
        raise GuardError(
            f"'{name}' is not owned by you (it needs admin rights to touch). "
            f"Jarvis runs unelevated and will not try."
        ) from None
    except psutil.Error as exc:
        raise GuardError(f"Cannot determine owner of '{name}': {exc}") from exc

    me = ""
    try:
        me = (psutil.Process().username() or "").lower()
    except psutil.Error:
        pass
    if me and owner != me:
        raise GuardError(
            f"Refusing to terminate '{name}': it belongs to {owner}, not you."
        )
