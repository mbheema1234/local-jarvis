"""FastAPI server: serves the dashboard and streams live events to it."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .appindex import index
from .bus import bus
from .config import WEB_DIR
from .llm.agent import agent
from .llm.openrouter import client
from .log import get
from .security import audit, is_elevated, policy
from .tools import describe, invoke
from .tools.system import system_snapshot
from .voice.pipeline import pipeline
from .voice.tts import Speaker

log = get("jarvis.server")

app = FastAPI(title="Jarvis", docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------


class MessageIn(BaseModel):
    text: str


class PermissionIn(BaseModel):
    id: str
    approved: bool


class ToolIn(BaseModel):
    name: str
    args: dict[str, Any] = {}


class SettingsIn(BaseModel):
    patch: dict[str, Any]


class SpeakIn(BaseModel):
    text: str


class WakeIn(BaseModel):
    enabled: bool


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket) -> None:
    await socket.accept()
    queue = bus.subscribe()
    log.info("dashboard connected")

    try:
        # Replay recent events so a reload doesn't lose the conversation.
        await socket.send_json({"kind": "replay", "events": bus.replay()})
        await socket.send_json({"kind": "state", "state": pipeline.state})

        while True:
            event = await queue.get()
            await socket.send_json(event)
    except WebSocketDisconnect:
        log.info("dashboard disconnected")
    except Exception as exc:
        log.debug("websocket closed: %s", exc)
    finally:
        bus.unsubscribe(queue)


# --------------------------------------------------------------------------
# Voice / conversation
# --------------------------------------------------------------------------


@app.post("/api/activate")
async def api_activate() -> dict:
    """Start a voice turn -- the mic button and the global hotkey both land here."""
    await pipeline.activate()
    return {"ok": True, "state": pipeline.state}


@app.post("/api/cancel")
async def api_cancel() -> dict:
    """Stop listening/speaking and deny anything awaiting approval."""
    pipeline.cancel()
    denied = policy.deny_all()
    return {"ok": True, "denied_pending": denied}


@app.post("/api/message")
async def api_message(body: MessageIn) -> dict:
    """Send a typed message, as if it had been spoken."""
    text = body.text.strip()
    if not text:
        return {"ok": False, "error": "Empty message."}
    reply = await pipeline.handle_text(text)
    return {"ok": True, "reply": reply}


@app.post("/api/speak")
async def api_speak(body: SpeakIn) -> dict:
    await pipeline.say(body.text)
    return {"ok": True}


@app.post("/api/reset")
async def api_reset() -> dict:
    agent.reset()
    return {"ok": True}


@app.post("/api/wake")
async def api_wake(body: WakeIn) -> dict:
    """Turn always-on listening on or off without restarting."""
    from .voice.wakeword import wake

    config.update({"voice": {"wake_enabled": body.enabled}})
    if body.enabled and not wake.listening:
        await pipeline.start_wake_word()
    else:
        pipeline.set_wake_enabled(body.enabled)
    return {"ok": True, "listening": wake.listening, "available": wake.available}


@app.get("/api/wake")
async def api_wake_state() -> dict:
    from .voice.wakeword import wake

    return {
        "enabled": config.load().voice.wake_enabled,
        "listening": wake.listening,
        "available": wake.available,
        "model": config.load().voice.wake_model,
    }


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------


@app.post("/api/permission")
async def api_permission(body: PermissionIn) -> dict:
    resolved = policy.resolve(body.id, body.approved)
    return {"ok": resolved}


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


@app.get("/api/state")
async def api_state() -> dict:
    """Everything the dashboard needs on load."""
    from .voice.wakeword import wake

    settings = config.load()
    return {
        "state": pipeline.state,
        "elevated": is_elevated(),
        "wake": {
            "enabled": settings.voice.wake_enabled,
            "listening": wake.listening,
            "available": wake.available,
        },
        "settings": settings.model_dump(),
        "tools": describe(),
        "routines": [r.model_dump() for r in settings.routines],
        "stats": system_snapshot(),
        "audit": audit.tail(60),
        "history": agent.history[-30:],
        "tokens_used": agent.total_cost_tokens,
    }


@app.get("/api/ping")
async def api_ping() -> dict:
    """Identify this server, so a second launch can find the live instance."""
    return {"app": "jarvis", "state": pipeline.state}


@app.post("/api/show")
async def api_show() -> dict:
    """Bring the dashboard window forward.

    Called when someone launches Jarvis again while it is already running --
    the second copy hands over instead of starting a duplicate.
    """
    # Starlette keeps these in State._state, so __dict__ never has them.
    callback = getattr(app.state, "show_window", None)
    if callback is None:
        return JSONResponse(
            {"ok": False, "error": "No window to show (running headless)."},
            status_code=409,
        )
    await asyncio.to_thread(callback)
    return {"ok": True}


@app.get("/api/stats")
async def api_stats() -> dict:
    return system_snapshot()


@app.get("/api/audit")
async def api_audit(limit: int = 120) -> dict:
    return {"entries": audit.tail(limit)}


@app.get("/api/running")
async def api_running() -> dict:
    """Open windows, for the dashboard's live panel.

    Deliberately bypasses the tool registry: this polls every few seconds, and
    routing it through invoke() would bury the real activity log under
    background noise and fill the conversation with phantom tool calls.
    """
    from .tools.apps import _user_windows

    windows = await asyncio.to_thread(_user_windows)
    return {"count": len(windows), "windows": windows}


@app.get("/api/apps")
async def api_apps(q: str = "", limit: int = 30) -> dict:
    if q:
        hits = [
            {"name": e.name, "kind": e.kind, "score": round(s)}
            for e, s in index.search(q, limit=limit)
            if s >= 50
        ]
    else:
        hits = [
            {"name": e.name, "kind": e.kind, "score": 100}
            for e in index.entries if not e.is_junk
        ][:limit]
    return {"apps": hits}


@app.get("/api/key")
async def api_key() -> dict:
    """Report OpenRouter credit, so the dashboard can show what's left."""
    try:
        data = await client.check_key()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    limit = data.get("limit")
    usage = data.get("usage", 0)
    return {
        "ok": True,
        "usage": usage,
        "limit": limit,
        "remaining": (limit - usage) if isinstance(limit, (int, float)) else None,
    }


@app.get("/api/voices")
async def api_voices() -> dict:
    return {"voices": await Speaker.list_voices()}


@app.get("/api/devices")
async def api_devices() -> dict:
    from .voice.audio import recorder
    from .voice.devices import DeviceUnavailable, resolve

    try:
        active = str(resolve())
        error = ""
    except DeviceUnavailable as exc:
        active, error = "", str(exc)

    return {
        "devices": await asyncio.to_thread(recorder.list_devices),
        "configured": config.load().voice.input_device_name,
        "active": active,
        "error": error,
    }


@app.post("/api/mic-test")
async def api_mic_test() -> dict:
    """Record briefly from the configured mic and report the signal level.

    Distinguishes "wrong device selected" from "right device, no signal" --
    which look identical from the outside.
    """
    from .voice.devices import DeviceUnavailable, invalidate, measure

    invalidate()
    try:
        result = await asyncio.to_thread(measure)
    except DeviceUnavailable as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, **result}


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------


@app.post("/api/tool")
async def api_tool(body: ToolIn) -> dict:
    """Run a tool directly. Backs the dashboard's quick-action tiles."""
    return await invoke(body.name, body.args)


@app.post("/api/settings")
async def api_settings(body: SettingsIn) -> dict:
    try:
        updated = config.update(body.patch)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    bus.publish("settings_changed")
    return {"ok": True, "settings": updated.model_dump()}


# --------------------------------------------------------------------------
# Static dashboard
# --------------------------------------------------------------------------


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    bus.bind_loop(asyncio.get_running_loop())
    log.info("event bus bound to server loop")

    # Warm the app index and the speech model in the background so the first
    # activation is fast rather than a 10-second stall.
    asyncio.create_task(asyncio.to_thread(index.ensure))
    if config.load().voice.enabled:
        asyncio.create_task(_start_voice())

    asyncio.create_task(_stats_ticker())
    asyncio.create_task(_speak_requests())


async def _start_voice() -> None:
    """Warm Whisper first, then arm the wake word.

    Order matters: if "Hey Jarvis" fired before the speech model was loaded,
    the first command would be swallowed by a ten-second model load.
    """
    await pipeline.warm_up()
    await pipeline.start_wake_word()


async def _stats_ticker() -> None:
    """Push machine vitals to the dashboard every few seconds."""
    while True:
        try:
            snapshot = await asyncio.to_thread(system_snapshot)
            bus.publish("stats", **snapshot)
        except Exception:
            log.debug("stats tick failed", exc_info=True)
        await asyncio.sleep(4)


async def _speak_requests() -> None:
    """Let non-async code (timers) ask for speech via the bus."""
    queue = bus.subscribe()
    while True:
        event = await queue.get()
        if event.get("kind") == "speak_request":
            try:
                await pipeline.say(event.get("text", ""))
            except Exception:
                log.debug("speak request failed", exc_info=True)
