"""Append-only audit log.

Every tool invocation lands here as one JSON line, whether it was allowed,
denied, or failed. The dashboard reads it back so there is always a visible
record of what Jarvis did on your machine.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from ..config import AUDIT_PATH

# Argument names whose values should never be written to disk.
_REDACT_KEYS = {"password", "passwd", "secret", "token", "api_key", "apikey"}
_MAX_VALUE_LEN = 400


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if k.lower() in _REDACT_KEYS else _sanitize(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(v) for v in value[:20]]
    if isinstance(value, str) and len(value) > _MAX_VALUE_LEN:
        return value[:_MAX_VALUE_LEN] + f"...<+{len(value) - _MAX_VALUE_LEN} chars>"
    return value


class AuditLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def record(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        decision: str = "allowed",
        status: str = "ok",
        detail: str = "",
        risk: str = "safe",
    ) -> dict[str, Any]:
        entry = {
            "ts": time.time(),
            "tool": tool,
            "risk": risk,
            "args": _sanitize(args or {}),
            "decision": decision,
            "status": status,
            "detail": _sanitize(detail),
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            with AUDIT_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return entry

    def tail(self, limit: int = 200) -> list[dict[str, Any]]:
        if not AUDIT_PATH.exists():
            return []
        with self._lock:
            lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


audit = AuditLog()
