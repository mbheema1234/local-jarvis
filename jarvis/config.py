"""Configuration: filesystem layout, user settings, and secrets.

Secrets live in ``.env`` (gitignored). Everything else lives in
``config/settings.json``, which is created from defaults on first run and is
editable both by hand and from the dashboard.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
WEB_DIR = Path(__file__).resolve().parent / "ui" / "web"

SETTINGS_PATH = CONFIG_DIR / "settings.json"
AUDIT_PATH = DATA_DIR / "audit.jsonl"
MEMORY_PATH = DATA_DIR / "memory.json"
APP_INDEX_PATH = DATA_DIR / "app_index.json"
HISTORY_PATH = DATA_DIR / "history.json"

for _d in (CONFIG_DIR, DATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")


# --------------------------------------------------------------------------
# Settings model
# --------------------------------------------------------------------------

RiskAction = Literal["allow", "confirm", "deny"]


class ModelSettings(BaseModel):
    agent: str = "anthropic/claude-sonnet-5"
    # Also Sonnet 5: benchmarked against five candidates on real screenshots of
    # this machine, it was the only one that read small on-screen text
    # accurately and answered in the requested format instead of narrating.
    vision: str = "anthropic/claude-sonnet-5"
    # Search-native model used by the `research` tool. Billed per web search
    # (~$0.005) on top of tokens, so it is not the default search path.
    search: str = "perplexity/sonar"

    # Brought in only when the agent stalls, so the expensive model is paid for
    # on the handful of hard turns rather than every trivial one.
    fallback: str = "anthropic/claude-opus-5"
    escalate_on_stuck: bool = True

    max_tokens: int = 2048
    temperature: float = 0.2
    # Hard stop on a single request's tool-calling loop, so a confused model
    # can never spin forever against your desktop. Driving another app's UI
    # takes several look-click-look rounds, so this needs headroom.
    max_tool_iterations: int = 20
    # Extra rounds granted to the fallback model after an escalation.
    escalation_extra_iterations: int = 8


class VoiceSettings(BaseModel):
    enabled: bool = True
    # faster-whisper model size. tiny.en/base.en are near-instant on CPU;
    # small.en is noticeably more accurate and still realtime on most machines.
    stt_model: str = "base.en"
    stt_device: Literal["auto", "cpu", "cuda"] = "auto"
    stt_compute_type: str = "int8"
    # Microphone selected by name (case-insensitive substring). Names are
    # stable; indices are not, because virtual audio drivers renumber devices
    # whenever they load. If the named device is absent Jarvis stops listening
    # rather than silently switching to a different microphone.
    input_device_name: str = "Comica"
    # Numeric override. Set this only to pin one exact interface; it bypasses
    # name matching entirely.
    input_device: int | None = None
    sample_rate: int = 16000
    # Energy-based endpointing.
    silence_threshold: float = 0.012
    silence_duration_s: float = 1.0
    max_utterance_s: float = 30.0
    min_utterance_s: float = 0.35
    # Seconds to wait for you to start speaking on the first turn.
    lead_in_s: float = 3.0

    # After a reply, keep listening so you can just carry on talking instead of
    # saying "Hey Jarvis" before every sentence.
    conversation_mode: bool = True
    # How long to wait for a follow-up before the conversation closes and the
    # wake word is needed again.
    conversation_timeout_s: float = 10.0
    # Safety stop, so a noisy room cannot keep a conversation alive forever.
    conversation_max_turns: int = 25

    # Always-on "Hey Jarvis". Detection runs locally; audio is never stored or
    # transmitted, and the recorder only starts once the phrase fires.
    wake_enabled: bool = True
    wake_model: str = "hey_jarvis_v0.1"
    # Raise toward 1.0 if it triggers on background noise; lower it if it
    # misses you. Synthetic speech scores ~0.999 and non-matches ~0.000.
    wake_threshold: float = 0.5
    # Ignore repeat detections within this many seconds.
    wake_cooldown_s: float = 2.5
    # Pause after speaking before re-arming, so the tail of a reply can't
    # retrigger the listener.
    wake_rearm_delay_s: float = 0.5

    tts_enabled: bool = True
    # "edge" = Microsoft neural voices (needs internet, sounds far better).
    # "sapi" = fully offline Windows voices.
    tts_engine: Literal["edge", "sapi"] = "edge"
    tts_voice: str = "en-GB-RyanNeural"
    tts_rate: str = "+8%"


class SecuritySettings(BaseModel):
    # risk tier -> what to do when a tool in that tier is called
    safe: RiskAction = "allow"
    moderate: RiskAction = "allow"
    high: RiskAction = "confirm"
    # Per-tool overrides win over the tier default, e.g. {"run_powershell": "deny"}
    tool_overrides: dict[str, RiskAction] = Field(default_factory=dict)
    # Seconds to wait for a dashboard approval before auto-denying.
    confirm_timeout_s: float = 45.0
    # Roots under which file writes/deletes are permitted. "~" is expanded.
    writable_roots: list[str] = Field(default_factory=lambda: ["~"])
    # Refuse to start if the process is running elevated.
    refuse_elevation: bool = True


class UISettings(BaseModel):
    port: int = 8787
    hotkey: str = "<ctrl>+<alt>+j"
    panic_hotkey: str = "<ctrl>+<alt>+<esc>"
    start_minimized: bool = False
    window_width: int = 1340
    window_height: int = 880
    always_on_top: bool = False


class Routine(BaseModel):
    """A named macro: a list of tool calls run in order."""

    name: str
    description: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)


class Settings(BaseModel):
    models: ModelSettings = Field(default_factory=ModelSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    ui: UISettings = Field(default_factory=UISettings)

    user_name: str = "Sir"
    # Extra persona/context text appended to the system prompt.
    persona_notes: str = ""

    # Spoken name -> what to actually launch. Lets you say "my nvidia app"
    # and have it resolve deterministically instead of by fuzzy match.
    app_aliases: dict[str, str] = Field(
        default_factory=lambda: {
            "epic": "Epic Games Launcher",
            "epic games": "Epic Games Launcher",
            "nvidia": "NVIDIA App",
            "my nvidia app": "NVIDIA App",
            "browser": "Google Chrome",
            "code": "Visual Studio Code",
            "terminal": "Windows Terminal",
        }
    )

    routines: list[Routine] = Field(
        default_factory=lambda: [
            Routine(
                name="gaming",
                description="Launch the gaming stack and set up audio.",
                steps=[
                    {"tool": "launch_app", "args": {"name": "Epic Games Launcher"}},
                    {"tool": "launch_app", "args": {"name": "NVIDIA App"}},
                    {"tool": "set_volume", "args": {"level": 55}},
                ],
            ),
            Routine(
                name="focus",
                description="Quiet the machine down for deep work.",
                steps=[
                    {"tool": "set_volume", "args": {"level": 20}},
                    {"tool": "launch_app", "args": {"name": "Visual Studio Code"}},
                ],
            ),
        ]
    )


# --------------------------------------------------------------------------
# Load / save
# --------------------------------------------------------------------------

_lock = threading.RLock()
_cached: Settings | None = None


def load(force: bool = False) -> Settings:
    """Return the active settings, reading from disk on first use."""
    global _cached
    with _lock:
        if _cached is not None and not force:
            return _cached
        if SETTINGS_PATH.exists():
            try:
                raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                _cached = Settings.model_validate(raw)
            except Exception:
                # A corrupt settings file must not brick startup; fall back to
                # defaults and preserve the bad file for inspection.
                SETTINGS_PATH.replace(SETTINGS_PATH.with_suffix(".json.bad"))
                _cached = Settings()
        else:
            _cached = Settings()
        save(_cached)
        return _cached


def save(settings: Settings) -> None:
    global _cached
    with _lock:
        _cached = settings
        SETTINGS_PATH.write_text(
            json.dumps(settings.model_dump(), indent=2), encoding="utf-8"
        )


def update(patch: dict[str, Any]) -> Settings:
    """Deep-merge ``patch`` into the current settings and persist."""

    def merge(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        for key, value in new.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = value
        return base

    with _lock:
        current = load().model_dump()
        merged = Settings.model_validate(merge(current, patch))
        save(merged)
        return merged


def openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Put it in the .env file at the "
            "project root (copy .env.example)."
        )
    return key
