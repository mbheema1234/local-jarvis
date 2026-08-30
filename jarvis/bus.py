"""A tiny async pub/sub bus.

Everything interesting that happens (state changes, transcripts, tool calls,
permission prompts) is published here; the WebSocket endpoint fans it out to
every connected dashboard. Publishing is safe from any thread.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Replayed to dashboards that connect late, so a refresh doesn't lose
        # the conversation.
        self._recent: list[dict[str, Any]] = []
        self._recent_limit = 200

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=512)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def replay(self) -> list[dict[str, Any]]:
        return list(self._recent)

    def publish(self, kind: str, **payload: Any) -> None:
        """Publish an event. Callable from any thread."""
        event = {"kind": kind, "ts": time.time(), **payload}
        if kind not in ("audio_level",):  # don't archive high-frequency noise
            self._recent.append(event)
            if len(self._recent) > self._recent_limit:
                del self._recent[: len(self._recent) - self._recent_limit]

        loop = self._loop
        if loop is None:
            return
        try:
            if asyncio.get_running_loop() is loop:
                self._dispatch(event)
                return
        except RuntimeError:
            pass  # not on any loop -> schedule below
        loop.call_soon_threadsafe(self._dispatch, event)

    def _dispatch(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A dashboard that cannot keep up loses frames rather than
                # stalling the assistant.
                pass


bus = EventBus()
