"""Tool registration, JSON-schema generation, and guarded dispatch.

Every capability Jarvis has is a function decorated with ``@tool``. The
decorator captures the risk tier and derives an OpenAI-compatible schema from
the type hints, so adding a capability is one function plus one decorator.

Dispatch always goes through :func:`invoke`, which is the single choke point
where policy is enforced and the audit record is written. Nothing calls a tool
function directly.
"""

from __future__ import annotations

import asyncio
import inspect
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, get_args, get_origin

from ..bus import bus
from ..log import get
from ..security import GuardError, PermissionDenied, Risk, audit, policy

log = get("jarvis.tools")

_PRIMITIVES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _json_type(annotation: Any) -> dict[str, Any]:
    """Translate a Python type hint into a JSON-schema fragment."""
    if annotation in _PRIMITIVES:
        return {"type": _PRIMITIVES[annotation]}

    origin = get_origin(annotation)

    if origin is Literal:
        options = list(get_args(annotation))
        kind = _PRIMITIVES.get(type(options[0]), "string")
        return {"type": kind, "enum": options}

    # Optional[X] / X | None -> schema of X (optionality is expressed by
    # leaving the field out of "required").
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in get_args(annotation) if a is not type(None)]
        if len(inner) == 1:
            return _json_type(inner[0])
        return {}

    if origin in (list, tuple):
        args = get_args(annotation)
        item = _json_type(args[0]) if args else {}
        return {"type": "array", "items": item or {"type": "string"}}

    if origin is dict or annotation is dict:
        return {"type": "object"}

    return {"type": "string"}


@dataclass
class Tool:
    name: str
    description: str
    risk: Risk
    func: Callable[..., Any]
    parameters: dict[str, Any]
    summary: Callable[[dict[str, Any]], str]
    is_async: bool = False
    tags: list[str] = field(default_factory=list)
    # Runs before the user is asked to approve anything. Raising GuardError
    # here rejects the call outright.
    precheck: Callable[[dict[str, Any]], None] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


REGISTRY: dict[str, Tool] = {}


def tool(
    *,
    risk: Risk = Risk.SAFE,
    params: dict[str, str] | None = None,
    summary: Callable[[dict[str, Any]], str] | str | None = None,
    name: str | None = None,
    tags: list[str] | None = None,
    precheck: Callable[[dict[str, Any]], None] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as a Jarvis tool.

    ``params`` maps argument names to the description the model sees -- this is
    the main lever for steering tool use, so it is worth writing carefully.
    ``summary`` renders the one-line, human-readable description shown in the
    confirmation prompt and the activity log.

    ``precheck`` validates the arguments against the hard guards *before* the
    user is asked to approve anything. Something that will be refused no matter
    what should never produce a prompt -- prompting for it would just teach the
    user to click Approve reflexively.
    """
    param_docs = params or {}

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or func.__name__
        signature = inspect.signature(func)
        hints = typing.get_type_hints(func)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for arg_name, parameter in signature.parameters.items():
            if arg_name in ("self", "cls"):
                continue
            schema = _json_type(hints.get(arg_name, str))
            if arg_name in param_docs:
                schema["description"] = param_docs[arg_name]
            if parameter.default is not inspect.Parameter.empty:
                schema.setdefault("default", parameter.default)
            else:
                required.append(arg_name)
            properties[arg_name] = schema

        doc = inspect.getdoc(func) or ""
        description = doc.split("\n\n")[0].strip()

        if callable(summary):
            summary_fn = summary
        elif isinstance(summary, str):
            def summary_fn(args: dict[str, Any], _t=summary) -> str:
                try:
                    return _t.format(**args)
                except Exception:
                    return _t
        else:
            def summary_fn(args: dict[str, Any], _n=tool_name) -> str:
                rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
                return f"{_n}({rendered})"

        REGISTRY[tool_name] = Tool(
            name=tool_name,
            description=description,
            risk=risk,
            func=func,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
            summary=summary_fn,
            is_async=inspect.iscoroutinefunction(func),
            tags=tags or [],
            precheck=precheck,
        )
        return func

    return decorator


def schemas() -> list[dict[str, Any]]:
    """All tool schemas, for the OpenRouter ``tools`` parameter."""
    return [t.schema() for t in REGISTRY.values()]


def describe() -> list[dict[str, Any]]:
    """Registry contents for the dashboard's capability list."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "risk": t.risk.value,
            "tags": t.tags,
            "effective_action": policy.action_for(t.name, t.risk),
        }
        for t in sorted(REGISTRY.values(), key=lambda t: (t.risk.value, t.name))
    ]


async def invoke(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run a tool through policy, execution, and audit.

    Always returns a dict. Failures are returned as ``{"ok": False, "error":
    ...}`` rather than raised, because the model needs to read the failure and
    recover from it.
    """
    entry = REGISTRY.get(name)
    if entry is None:
        return {"ok": False, "error": f"No such tool: {name}"}

    # Drop arguments the function does not accept -- models occasionally
    # hallucinate an extra field and that shouldn't hard-fail the call.
    accepted = set(inspect.signature(entry.func).parameters)
    clean = {k: v for k, v in args.items() if k in accepted}
    dropped = sorted(set(args) - accepted)
    if dropped:
        log.debug("dropping unexpected args for %s: %s", name, dropped)

    try:
        summary = entry.summary(clean)
    except Exception:
        summary = name

    bus.publish("tool_start", tool=name, risk=entry.risk.value, summary=summary, args=clean)

    # Hard guards first: reject what is categorically forbidden without ever
    # putting an approval dialog in front of the user.
    if entry.precheck is not None:
        try:
            entry.precheck(clean)
        except (GuardError, PermissionDenied) as exc:
            audit.record(name, clean, decision="blocked", status="blocked",
                         detail=str(exc), risk=entry.risk.value)
            bus.publish("tool_end", tool=name, ok=False, summary=summary, error=str(exc))
            return {"ok": False, "error": str(exc), "blocked": True}

    try:
        await policy.check(name, entry.risk, clean, summary)
    except PermissionDenied as exc:
        audit.record(name, clean, decision="denied", status="denied",
                     detail=str(exc), risk=entry.risk.value)
        bus.publish("tool_end", tool=name, ok=False, summary=summary, error=str(exc))
        return {"ok": False, "error": str(exc), "denied": True}

    try:
        if entry.is_async:
            result = await entry.func(**clean)
        else:
            result = await asyncio.to_thread(lambda: entry.func(**clean))
    except (GuardError, PermissionDenied) as exc:
        audit.record(name, clean, decision="blocked", status="blocked",
                     detail=str(exc), risk=entry.risk.value)
        bus.publish("tool_end", tool=name, ok=False, summary=summary, error=str(exc))
        return {"ok": False, "error": str(exc), "blocked": True}
    except Exception as exc:
        log.exception("tool %s failed", name)
        message = f"{type(exc).__name__}: {exc}"
        audit.record(name, clean, decision="allowed", status="error",
                     detail=message, risk=entry.risk.value)
        bus.publish("tool_end", tool=name, ok=False, summary=summary, error=message)
        return {"ok": False, "error": message}

    payload = result if isinstance(result, dict) else {"result": result}
    payload.setdefault("ok", True)
    audit.record(name, clean, decision="allowed", status="ok",
                 detail=summary, risk=entry.risk.value)
    bus.publish("tool_end", tool=name, ok=True, summary=summary, result=payload)
    return payload
