"""The agent loop: turn a user utterance into tool calls and a spoken reply."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..bus import bus
from ..config import HISTORY_PATH, load
from ..log import get
from ..tools import registry
from .openrouter import OpenRouterError, client
from .prompts import system_prompt

log = get("jarvis.agent")

# How many prior turns to carry. Voice conversations are short and context
# beyond this is rarely worth the tokens.
MAX_HISTORY_TURNS = 24


class Agent:
    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self.last_usage: dict[str, Any] = {}
        self.total_cost_tokens = 0

    # -- history -----------------------------------------------------------

    def reset(self) -> None:
        self.history.clear()
        bus.publish("history_cleared")

    def _trim(self) -> None:
        if len(self.history) <= MAX_HISTORY_TURNS:
            return
        # Never strand a tool result whose originating assistant message was
        # dropped -- that makes the next request invalid.
        cut = len(self.history) - MAX_HISTORY_TURNS
        while cut < len(self.history) and self.history[cut].get("role") == "tool":
            cut += 1
        self.history = self.history[cut:]

    def save_history(self) -> None:
        try:
            HISTORY_PATH.write_text(
                json.dumps(self.history[-40:], indent=1, default=str), encoding="utf-8"
            )
        except Exception:
            log.debug("could not persist history", exc_info=True)

    # -- main loop ---------------------------------------------------------

    async def run(self, user_text: str) -> str:
        """Handle one user turn. Returns the assistant's spoken reply."""
        async with self._lock:
            return await self._run(user_text)

    async def _run(self, user_text: str) -> str:
        settings = load()
        bus.publish("state", state="thinking")
        bus.publish("message", role="user", text=user_text)

        self.history.append({"role": "user", "content": user_text})
        self._trim()

        messages = [{"role": "system", "content": system_prompt()}, *self.history]
        tools = registry.schemas()

        model = settings.models.agent
        budget = settings.models.max_tool_iterations
        escalated = False
        iteration = 0

        try:
            while iteration < budget:
                iteration += 1
                try:
                    result = await client.chat(messages, tools=tools, model=model)
                except OpenRouterError:
                    # A model can be briefly unavailable or rate limited. One
                    # retry on the fallback beats failing the whole turn.
                    if escalated or not settings.models.escalate_on_stuck:
                        raise
                    escalated = True
                    model = settings.models.fallback
                    log.warning("primary model failed; retrying on %s", model)
                    bus.publish("escalated", model=model, reason="model unavailable")
                    result = await client.chat(messages, tools=tools, model=model)

                message = result["message"]
                self.last_usage = result.get("usage", {})
                self.total_cost_tokens += self.last_usage.get("total_tokens", 0) or 0

                tool_calls = message.get("tool_calls") or []
                assistant_entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content") or "",
                }
                if tool_calls:
                    assistant_entry["tool_calls"] = tool_calls

                messages.append(assistant_entry)
                self.history.append(assistant_entry)

                if not tool_calls:
                    reply = (message.get("content") or "").strip()
                    if not reply:
                        reply = "Done."
                        self.history[-1]["content"] = reply
                    bus.publish("message", role="assistant", text=reply)
                    bus.publish("state", state="idle")
                    self.save_history()
                    return reply

                # Run this round's tool calls concurrently. Anything needing
                # confirmation still blocks on the user, but independent calls
                # (opening two apps) no longer serialise behind each other.
                outcomes = await asyncio.gather(
                    *(self._execute(call) for call in tool_calls)
                )
                for entry in outcomes:
                    messages.append(entry)
                    self.history.append(entry)

                log.info("tool round %d complete (%d calls) on %s",
                         iteration, len(tool_calls), model)

                # Out of rounds but still calling tools: it is going in circles.
                # Hand the problem to the stronger model rather than giving up,
                # with a nudge to rethink instead of grinding on the same plan.
                if (iteration >= budget and not escalated
                        and settings.models.escalate_on_stuck):
                    escalated = True
                    model = settings.models.fallback
                    budget += settings.models.escalation_extra_iterations
                    log.warning("agent stalled after %d rounds; escalating to %s",
                                iteration, model)
                    bus.publish("escalated", model=model, reason="stalled")
                    messages.append({
                        "role": "user",
                        "content": (
                            "You have spent several rounds without finishing. Stop "
                            "and reconsider from scratch: what is actually being "
                            "asked, what have you already learned, and what is the "
                            "shortest way to finish? If something is genuinely "
                            "blocking you, say so plainly instead of retrying."
                        ),
                    })

            # Even the fallback could not finish.
            reply = "I got stuck on that one. Could you rephrase it?"
            self.history.append({"role": "assistant", "content": reply})
            bus.publish("message", role="assistant", text=reply)
            bus.publish("state", state="idle")
            return reply

        except OpenRouterError as exc:
            log.error("model error: %s", exc)
            bus.publish("error", text=str(exc))
            bus.publish("state", state="idle")
            return f"I couldn't reach the model. {exc}"
        except Exception as exc:
            log.exception("agent failure")
            bus.publish("error", text=str(exc))
            bus.publish("state", state="idle")
            return "Something went wrong on my side. Check the activity log."

    async def _execute(self, call: dict[str, Any]) -> dict[str, Any]:
        """Run one tool call and format it as a `tool` role message."""
        function = call.get("function") or {}
        name = function.get("name") or ""
        raw_args = function.get("arguments") or "{}"

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            args = {}
            result: dict[str, Any] = {
                "ok": False,
                "error": f"Arguments were not valid JSON: {raw_args[:200]}",
            }
        else:
            result = await registry.invoke(name, args)

        return {
            "role": "tool",
            "tool_call_id": call.get("id", ""),
            "name": name,
            "content": json.dumps(result, default=str)[:4000],
        }


agent = Agent()
