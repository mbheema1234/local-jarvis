"""Input device selection.

Device *indices* are not stable on this machine -- virtual audio drivers
(Voicemeeter and friends) renumber everything when they load, so a pinned
integer silently becomes the wrong microphone. Devices are therefore selected
by name, resolved fresh, and validated by actually opening them.

Not every host API can deliver the 16 kHz that Whisper and the wake word model
need: WASAPI refuses anything but the device's native rate, and WDM-KS often
fails outright. Rather than encode that as a rule, each candidate is opened for
real and the first one that works is used.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..config import load
from ..log import get

log = get("jarvis.voice.devices")

# Tried in this order. MME and DirectSound resample in PortAudio, so they can
# serve 16 kHz from a 48 kHz device; the other two generally cannot.
_HOST_API_PREFERENCE = ["MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS"]


class DeviceUnavailable(RuntimeError):
    """The configured microphone is not present, or will not open."""


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    host_api: str

    def __str__(self) -> str:
        return f"{self.name} [{self.host_api}, index {self.index}]"


_lock = threading.Lock()
_cache: dict[tuple[str, int], InputDevice] = {}


def list_inputs() -> list[dict]:
    """Every capture device, de-duplicated by name for display."""
    import sounddevice as sd

    apis = sd.query_hostapis()
    seen: dict[str, dict] = {}
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] < 1:
            continue
        name = device["name"]
        entry = seen.setdefault(name, {
            "name": name,
            "index": index,
            "channels": device["max_input_channels"],
            "host_apis": [],
        })
        entry["host_apis"].append(apis[device["hostapi"]]["name"])
    return sorted(seen.values(), key=lambda d: d["name"].casefold())


def _candidates(fragment: str) -> list[InputDevice]:
    """Capture devices whose name contains ``fragment``, best host API first."""
    import sounddevice as sd

    apis = sd.query_hostapis()
    needle = fragment.casefold().strip()
    found: list[InputDevice] = []

    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] < 1:
            continue
        if needle and needle not in device["name"].casefold():
            continue
        found.append(InputDevice(
            index=index,
            name=device["name"],
            host_api=apis[device["hostapi"]]["name"],
        ))

    def rank(dev: InputDevice) -> int:
        try:
            return _HOST_API_PREFERENCE.index(dev.host_api)
        except ValueError:
            return len(_HOST_API_PREFERENCE)

    return sorted(found, key=rank)


def _opens(device: InputDevice, sample_rate: int) -> bool:
    import sounddevice as sd

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=1280,
            device=device.index,
        ):
            return True
    except Exception as exc:
        log.debug("%s will not open at %d Hz: %s", device, sample_rate, exc)
        return False


def resolve(sample_rate: int | None = None) -> InputDevice:
    """Return the microphone Jarvis should use.

    Raises :class:`DeviceUnavailable` when the configured device is missing.
    It deliberately does not fall back to another microphone -- if you pinned a
    specific mic, quietly listening on a different one would be worse than not
    listening at all.
    """
    cfg = load().voice
    rate = sample_rate or cfg.sample_rate

    # An explicit numeric override wins and skips name matching entirely.
    if cfg.input_device is not None:
        import sounddevice as sd

        info = sd.query_devices(cfg.input_device)
        apis = sd.query_hostapis()
        return InputDevice(
            index=int(cfg.input_device),
            name=info["name"],
            host_api=apis[info["hostapi"]]["name"],
        )

    fragment = (cfg.input_device_name or "").strip()
    if not fragment:
        import sounddevice as sd

        index = sd.default.device[0]
        info = sd.query_devices(index)
        return InputDevice(index=index, name=info["name"], host_api="default")

    key = (fragment.casefold(), rate)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    candidates = _candidates(fragment)
    if not candidates:
        raise DeviceUnavailable(
            f"No microphone matching {fragment!r} is connected. Jarvis is "
            f"configured to use only that device and will not fall back to "
            f"another one. Plug it in, or change the microphone in Settings."
        )

    for device in candidates:
        if _opens(device, rate):
            log.info("using microphone: %s", device)
            with _lock:
                _cache[key] = device
            return device

    tried = ", ".join(f"{d.host_api}" for d in candidates)
    raise DeviceUnavailable(
        f"Found {candidates[0].name!r} but none of its audio interfaces would "
        f"open at {rate} Hz (tried: {tried})."
    )


def invalidate() -> None:
    """Drop cached resolutions, e.g. after the device list changes."""
    with _lock:
        _cache.clear()


def measure(seconds: float = 2.5) -> dict:
    """Record briefly from the configured mic and report the signal level.

    Used by the dashboard's mic test: it distinguishes "wrong device" from
    "right device, no signal", which are otherwise indistinguishable.
    """
    import numpy as np
    import sounddevice as sd

    cfg = load().voice
    device = resolve()

    frames = []
    with sd.InputStream(
        samplerate=cfg.sample_rate,
        channels=1,
        dtype="float32",
        blocksize=1600,
        device=device.index,
    ) as stream:
        for _ in range(int(seconds * cfg.sample_rate / 1600)):
            data, _overflow = stream.read(1600)
            frames.append(data[:, 0].copy())

    audio = np.concatenate(frames) if frames else np.zeros(1, dtype="float32")
    rms = float(np.sqrt(np.mean(audio**2)))
    peak = float(np.max(np.abs(audio)))

    # Below this the input is indistinguishable from digital silence -- the
    # device is enumerating but not actually delivering audio.
    silent = peak < 0.0005

    return {
        "device": device.name,
        "host_api": device.host_api,
        "index": device.index,
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "silent": silent,
        "speech_detected": rms >= cfg.silence_threshold,
        "threshold": cfg.silence_threshold,
    }
