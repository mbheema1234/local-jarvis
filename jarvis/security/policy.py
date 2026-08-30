"""Risk tiers and the human-in-the-loop confirmation flow."""

from __future__ import annotations

import asyncio
import uuid
from enum import Enum
from typing import Any

from ..bus import bus
from ..config import load
from ..log import get

log = get("jarvis.policy")


class Risk(str, Enum):
    """How much damage a tool could do if the model gets it wrong."""

    SAFE = "safe"          # read-only or trivially reversible
    MODERATE = "moderate"  # changes visible state: launch apps, volume, typing
    HIGH = "high"          # destructive or broad: delete, kill, shell


class PermissionDenied(RuntimeError):
    """The user (or policy) refused this action."""


class Policy:
    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[bool]] = {}

    # -- decisions ---------------------------------------------------------

    def action_for(self, tool: str, risk: Risk) -> str:
        sec = load().security
        override = sec.tool_overrides.get(tool)
        if override:
            return override
        return {Risk.SAFE: sec.safe, Risk.MODERATE: sec.moderate, Risk.HIGH: sec.high}[risk]

    async def check(self, tool: str, risk: Risk, args: dict[str, Any], summary: str) -> None:
        """Allow the call, or raise PermissionDenied.

        ``summary`` is the plain-English description shown to the user.
        """
        action = self.action_for(tool, risk)

        if action == "allow":
            return
        if action == "deny":
            raise PermissionDenied(
                f"'{tool}' is disabled in your settings. Enable it under "
                f"Security if you want Jarvis to do that."
            )

        approved = await self._ask(tool, risk, args, summary)
        if not approved:
            raise PermissionDenied(f"You declined: {summary}")

    # -- confirmation round trip ------------------------------------------

    async def _ask(self, tool: str, risk: Risk, args: dict[str, Any], summary: str) -> bool:
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[request_id] = future

        bus.publish(
            "permission_request",
            id=request_id,
            tool=tool,
            risk=risk.value,
            summary=summary,
            args=args,
            timeout=load().security.confirm_timeout_s,
        )

        try:
            approved = await asyncio.wait_for(
                future, timeout=load().security.confirm_timeout_s
            )
        except asyncio.TimeoutError:
            approved = False
            log.info("permission request %s timed out -> denied", request_id)
        finally:
            self._pending.pop(request_id, None)

        bus.publish("permission_resolved", id=request_id, approved=approved)
        return approved

    def resolve(self, request_id: str, approved: bool) -> bool:
        """Called from the API when the user clicks Approve/Deny."""
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.get_loop().call_soon_threadsafe(future.set_result, approved)
        return True

    def pending_ids(self) -> list[str]:
        return list(self._pending)

    def deny_all(self) -> int:
        """Panic path: refuse everything currently awaiting approval."""
        count = 0
        for request_id in list(self._pending):
            if self.resolve(request_id, False):
                count += 1
        return count


policy = Policy()
