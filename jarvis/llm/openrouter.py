"""Thin async client for OpenRouter's chat-completions API."""

from __future__ import annotations

from typing import Any

import httpx

from ..config import load, openrouter_key
from ..log import get

log = get("jarvis.llm")

BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterError(RuntimeError):
    pass


class OpenRouter:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                timeout=httpx.Timeout(90.0, connect=10.0),
                headers={
                    "Authorization": f"Bearer {openrouter_key()}",
                    "Content-Type": "application/json",
                    # OpenRouter uses these for attribution on its dashboard.
                    "HTTP-Referer": "http://localhost/jarvis",
                    "X-Title": "Jarvis Desktop Assistant",
                },
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Send a chat completion request and return the first choice's message."""
        settings = load().models
        payload: dict[str, Any] = {
            "model": model or settings.agent,
            "messages": messages,
            "max_tokens": max_tokens or settings.max_tokens,
            "temperature": settings.temperature if temperature is None else temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        client = await self._get_client()
        try:
            response = await client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise OpenRouterError(f"Could not reach OpenRouter: {exc}") from exc

        if response.status_code == 401:
            raise OpenRouterError(
                "OpenRouter rejected the API key (401). Check OPENROUTER_API_KEY in .env."
            )
        if response.status_code == 402:
            raise OpenRouterError("OpenRouter credit exhausted (402). Top up to continue.")
        if response.status_code == 429:
            raise OpenRouterError("Rate limited by OpenRouter (429). Try again shortly.")
        if response.status_code >= 400:
            raise OpenRouterError(
                f"OpenRouter returned {response.status_code}: {response.text[:400]}"
            )

        data = response.json()
        if "choices" not in data or not data["choices"]:
            raise OpenRouterError(f"Malformed response from OpenRouter: {str(data)[:300]}")

        choice = data["choices"][0]
        return {
            "message": choice.get("message") or {},
            "finish_reason": choice.get("finish_reason"),
            "usage": data.get("usage", {}),
            "model": data.get("model"),
        }

    async def check_key(self) -> dict[str, Any]:
        """Validate the key and report remaining credit."""
        client = await self._get_client()
        response = await client.get("/key")
        if response.status_code != 200:
            raise OpenRouterError(f"Key check failed ({response.status_code}).")
        return response.json().get("data", {})

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()


client = OpenRouter()
