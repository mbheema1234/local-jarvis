"""The voice loop: listen, transcribe, think, speak.

One activation runs one turn. State transitions are published on the bus so
the dashboard orb always reflects what Jarvis is actually doing.
"""

from __future__ import annotations

import asyncio
import threading

from ..bus import bus
from ..config import load
from ..llm.agent import agent
from ..log import get
from .audio import recorder
from .stt import transcriber
from .tts import speaker
from .wakeword import wake

log = get("jarvis.voice")

# Said at the end of a conversation rather than as a request. Matched only on a
# short utterance, so "thanks, now open Spotify" is still treated as a command.
_FAREWELLS = (
    "thanks jarvis", "thank you jarvis", "thanks", "thank you", "that's all",
    "thats all", "that is all", "nothing else", "no thanks", "no thank you",
    "goodbye", "bye jarvis", "bye", "never mind", "nevermind", "we're done",
    "were done", "i'm done", "im done", "that'll be all", "thatll be all",
    "stop listening", "go to sleep", "dismissed",
)


def _is_farewell(text: str) -> bool:
    cleaned = text.strip().strip(".!,").casefold()
    if len(cleaned.split()) > 4:
        return False
    return any(cleaned == phrase or cleaned.startswith(phrase + " ")
               for phrase in _FAREWELLS)


class VoicePipeline:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self.state = "idle"

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def _set_state(self, state: str) -> None:
        self.state = state
        bus.publish("state", state=state)
        # The wake listener holds the microphone exclusively, so it has to let
        # go for the duration of a turn. Re-arming is deferred (see _rearm) so
        # the tail of a spoken reply cannot trigger it.
        if state != "idle":
            wake.pause()
            return

        delay = load().voice.wake_rearm_delay_s
        try:
            asyncio.get_running_loop().call_later(delay, self._rearm)
        except RuntimeError:
            # Called from a thread with no running loop (e.g. cancel() via the
            # tray); a plain timer is fine, re-arming is not loop-bound.
            threading.Timer(delay, self._rearm).start()

    @staticmethod
    def _rearm() -> None:
        if load().voice.wake_enabled:
            wake.resume()

    # -- control -----------------------------------------------------------

    async def activate(self) -> None:
        """Start one listen-think-speak turn.

        If Jarvis is already speaking, this interrupts it and listens instead,
        so you can talk over a reply that has gone on too long.
        """
        if speaker.speaking:
            speaker.stop()
            await asyncio.sleep(0.05)

        if self.busy:
            log.info("activation ignored: turn already in progress")
            return

        self._task = asyncio.create_task(self._turn())

    def cancel(self) -> None:
        """Abort the current turn immediately."""
        recorder.cancel()
        speaker.stop()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._set_state("idle")

    async def warm_up(self) -> None:
        """Preload the Whisper model so the first activation isn't slow."""
        bus.publish("stt_loading")
        try:
            await asyncio.to_thread(transcriber.load)
            bus.publish("stt_ready")
            log.info("speech recognition warm")
        except Exception as exc:
            log.error("could not load whisper: %s", exc)
            bus.publish("error", text=f"Speech recognition unavailable: {exc}")

    # -- wake word ---------------------------------------------------------

    async def start_wake_word(self) -> bool:
        """Arm always-on "Hey Jarvis" detection."""
        if not load().voice.wake_enabled:
            return False

        loop = asyncio.get_running_loop()

        def on_wake() -> None:
            # Fired on the listener thread; hop back onto the server loop.
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._wake_turn())
            )

        started = await asyncio.to_thread(wake.start, on_wake)
        bus.publish("wake_state", listening=started, available=wake.available)
        if not started:
            log.warning("wake word unavailable; hotkey and orb still work")
        return started

    async def _wake_turn(self) -> None:
        bus.publish("wake_detected")
        await self.activate()

    def set_wake_enabled(self, enabled: bool) -> None:
        """Toggle the wake word at runtime, without restarting."""
        if enabled:
            wake.resume()
        else:
            wake.pause()
        bus.publish("wake_state", listening=wake.listening, available=wake.available)

    # -- the turn ----------------------------------------------------------

    async def _turn(self) -> None:
        """Run a conversation: one exchange, then keep listening for more.

        After the wake word, Jarvis stays in the conversation until you stop
        talking to it, so follow-up questions don't each need "Hey Jarvis".
        """
        cfg = load().voice
        turns = 0

        def on_level(level: float) -> None:
            # Called from the audio thread. bus.publish already marshals onto
            # the server loop, so no extra hop is needed here.
            bus.publish("audio_level", level=round(level, 4))

        try:
            while True:
                first = turns == 0
                self._set_state("listening")
                if not first:
                    bus.publish("conversation", active=True, turn=turns)

                # Give a longer opening for a follow-up: mid-conversation
                # people pause to think, and cutting them off there is what
                # makes an assistant feel like a vending machine.
                lead_in = cfg.lead_in_s if first else cfg.conversation_timeout_s
                audio = await asyncio.to_thread(recorder.record, on_level, lead_in)

                if audio is None:
                    if first:
                        bus.publish("notice", text="I didn't catch anything.")
                    else:
                        log.info("conversation ended after %d turns", turns)
                        bus.publish("conversation", active=False, turn=turns)
                    break

                self._set_state("transcribing")
                text = await asyncio.to_thread(transcriber.transcribe, audio)
                if not text or len(text.strip()) < 2:
                    if first:
                        bus.publish("notice", text="I didn't catch that.")
                        break
                    # Mid-conversation, a stray noise shouldn't end things.
                    continue

                bus.publish("transcript", text=text)

                if not first and _is_farewell(text):
                    self._set_state("speaking")
                    await speaker.speak("Right you are, Sir.")
                    bus.publish("conversation", active=False, turn=turns)
                    break

                reply = await agent.run(text)

                self._set_state("speaking")
                await speaker.speak(reply)

                turns += 1
                if not cfg.conversation_mode or turns >= cfg.conversation_max_turns:
                    break

            self._set_state("idle")

        except asyncio.CancelledError:
            log.info("turn cancelled")
            bus.publish("conversation", active=False)
            self._set_state("idle")
            raise
        except Exception as exc:
            log.exception("voice turn failed")
            bus.publish("error", text=str(exc))
            bus.publish("conversation", active=False)
            self._set_state("idle")

    async def say(self, text: str) -> None:
        """Speak a line without involving the model (timers, greetings)."""
        self._set_state("speaking")
        await speaker.speak(text)
        self._set_state("idle")

    async def handle_text(self, text: str) -> str:
        """Run a typed message through the agent and speak the reply."""
        reply = await agent.run(text)
        self._set_state("speaking")
        await speaker.speak(reply)
        self._set_state("idle")
        return reply


pipeline = VoicePipeline()
