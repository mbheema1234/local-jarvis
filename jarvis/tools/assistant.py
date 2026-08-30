"""Assistant-level features: persistent memory, routines, and timers."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any

from ..bus import bus
from ..config import MEMORY_PATH, Routine, load, save
from ..log import get
from ..security import Risk
from .registry import REGISTRY, tool

log = get("jarvis.tools.assistant")


# --------------------------------------------------------------------------
# Memory - facts that survive restarts and get injected into the system prompt
# --------------------------------------------------------------------------

_memory_lock = threading.Lock()


def _read_memory() -> dict[str, str]:
    if not MEMORY_PATH.exists():
        return {}
    try:
        return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_memory(data: dict[str, str]) -> None:
    MEMORY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def all_memories() -> dict[str, str]:
    with _memory_lock:
        return _read_memory()


@tool(
    risk=Risk.SAFE,
    params={"key": "Short label for the fact, e.g. 'main monitor' or 'work hours'.",
            "value": "The fact to remember."},
    summary=lambda a: f"Remember {a.get('key', '?')!r}",
    tags=["memory"],
)
def remember(key: str, value: str) -> dict:
    """Store a fact about the user or their setup, permanently.

    Use this whenever the user says to remember something, or states a durable
    preference worth carrying into future conversations.
    """
    with _memory_lock:
        data = _read_memory()
        data[key.strip()] = value.strip()
        _write_memory(data)
    bus.publish("memory_changed", count=len(data))
    return {"remembered": {key: value}, "total": len(data)}


@tool(risk=Risk.SAFE, summary="Recall stored facts", tags=["memory"])
def recall(key: str = "") -> dict:
    """Retrieve stored facts. Omit the key to list everything remembered."""
    data = all_memories()
    if key:
        needle = key.casefold()
        matched = {k: v for k, v in data.items() if needle in k.casefold()}
        return {"memories": matched}
    return {"memories": data, "count": len(data)}


@tool(
    risk=Risk.MODERATE,
    params={"key": "The fact to forget."},
    summary=lambda a: f"Forget {a.get('key', '?')!r}",
    tags=["memory"],
)
def forget(key: str) -> dict:
    """Delete a stored fact."""
    with _memory_lock:
        data = _read_memory()
        removed = data.pop(key, None)
        if removed is None:
            for existing in list(data):
                if existing.casefold() == key.casefold():
                    removed = data.pop(existing)
                    break
        _write_memory(data)
    if removed is None:
        return {"ok": False, "error": f"Nothing remembered under {key!r}."}
    bus.publish("memory_changed", count=len(data))
    return {"forgot": key}


# --------------------------------------------------------------------------
# Routines - named sequences of tool calls
# --------------------------------------------------------------------------


@tool(risk=Risk.SAFE, summary="List routines", tags=["routines"])
def list_routines() -> dict:
    """List saved routines (named multi-step shortcuts like 'gaming')."""
    return {
        "routines": [
            {"name": r.name, "description": r.description, "steps": len(r.steps)}
            for r in load().routines
        ]
    }


@tool(
    risk=Risk.MODERATE,
    params={"name": "Which routine to run."},
    summary=lambda a: f"Run the {a.get('name', '?')!r} routine",
    tags=["routines"],
)
async def run_routine(name: str) -> dict:
    """Run a saved routine, executing each of its steps in order."""
    from .registry import invoke  # imported here to avoid a circular import

    routine = next(
        (r for r in load().routines if r.name.casefold() == name.casefold()), None
    )
    if routine is None:
        available = [r.name for r in load().routines]
        return {"ok": False, "error": f"No routine named {name!r}.", "available": available}

    results = []
    for step in routine.steps:
        tool_name = step.get("tool")
        if tool_name not in REGISTRY:
            results.append({"tool": tool_name, "ok": False, "error": "unknown tool"})
            continue
        outcome = await invoke(tool_name, step.get("args", {}))
        results.append({"tool": tool_name, "ok": outcome.get("ok", False)})
        if not outcome.get("ok"):
            results[-1]["error"] = outcome.get("error")
        await asyncio.sleep(0.3)

    succeeded = sum(1 for r in results if r["ok"])
    return {
        "routine": routine.name,
        "steps_run": len(results),
        "succeeded": succeeded,
        "results": results,
    }


@tool(
    risk=Risk.MODERATE,
    params={
        "name": "Name for the routine, e.g. 'gaming'.",
        "steps": "Ordered list of steps, each {'tool': <tool name>, 'args': {...}}.",
        "description": "What the routine is for.",
    },
    summary=lambda a: f"Save routine {a.get('name', '?')!r}",
    tags=["routines"],
)
def save_routine(name: str, steps: list[dict], description: str = "") -> dict:
    """Save a named routine so it can be run later in one command.

    Every step's tool must exist; unknown tools are rejected rather than
    silently stored.
    """
    unknown = [s.get("tool") for s in steps if s.get("tool") not in REGISTRY]
    if unknown:
        return {"ok": False, "error": f"Unknown tools: {unknown}", "available": sorted(REGISTRY)}

    settings = load()
    routines = [r for r in settings.routines if r.name.casefold() != name.casefold()]
    routines.append(Routine(name=name, description=description, steps=steps))
    settings.routines = routines
    save(settings)
    bus.publish("routines_changed")
    return {"saved": name, "steps": len(steps)}


@tool(
    risk=Risk.MODERATE,
    params={"name": "Routine to delete."},
    summary=lambda a: f"Delete routine {a.get('name', '?')!r}",
    tags=["routines"],
)
def delete_routine(name: str) -> dict:
    """Delete a saved routine."""
    settings = load()
    before = len(settings.routines)
    settings.routines = [r for r in settings.routines if r.name.casefold() != name.casefold()]
    if len(settings.routines) == before:
        return {"ok": False, "error": f"No routine named {name!r}."}
    save(settings)
    bus.publish("routines_changed")
    return {"deleted": name}


# --------------------------------------------------------------------------
# Timers and reminders
# --------------------------------------------------------------------------

_timers: dict[str, dict[str, Any]] = {}


@tool(
    risk=Risk.SAFE,
    params={"seconds": "How long until it fires.", "label": "What the timer is for."},
    summary=lambda a: f"Set a {a.get('seconds', '?')}s timer",
    tags=["timers"],
)
async def set_timer(seconds: int, label: str = "Timer") -> dict:
    """Set a timer or reminder that notifies the user when it elapses."""
    if seconds <= 0:
        return {"ok": False, "error": "Duration must be positive."}
    if seconds > 24 * 3600:
        return {"ok": False, "error": "Timers are capped at 24 hours."}

    timer_id = uuid.uuid4().hex[:8]
    fires_at = time.time() + seconds

    async def fire() -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        _timers.pop(timer_id, None)
        bus.publish("timer_fired", id=timer_id, label=label)
        from .system import notify

        notify(f"{label}", "Your timer has finished.")
        bus.publish("speak_request", text=f"{label} is up.")

    task = asyncio.create_task(fire())
    _timers[timer_id] = {"id": timer_id, "label": label, "fires_at": fires_at, "task": task}
    bus.publish("timers_changed")
    return {"id": timer_id, "label": label, "seconds": seconds}


@tool(risk=Risk.SAFE, summary="List active timers", tags=["timers"])
def list_timers() -> dict:
    """List timers that have not fired yet."""
    now = time.time()
    return {
        "timers": [
            {"id": t["id"], "label": t["label"], "remaining_s": max(0, round(t["fires_at"] - now))}
            for t in _timers.values()
        ]
    }


@tool(
    risk=Risk.SAFE,
    params={"timer_id": "The id of the timer to cancel."},
    summary=lambda a: f"Cancel timer {a.get('timer_id', '?')}",
    tags=["timers"],
)
def cancel_timer(timer_id: str) -> dict:
    """Cancel a running timer."""
    timer = _timers.pop(timer_id, None)
    if timer is None:
        return {"ok": False, "error": f"No active timer with id {timer_id!r}."}
    timer["task"].cancel()
    bus.publish("timers_changed")
    return {"cancelled": timer["label"]}
