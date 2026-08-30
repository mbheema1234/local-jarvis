"""Verify Jarvis uses only the configured microphone.

The important property is negative: when the pinned mic is unavailable, Jarvis
must stop listening rather than quietly switching to another one.

    uv run python scripts/check_mic.py
"""

from __future__ import annotations

import sys

passed, failed = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))


def main() -> int:
    import sounddevice as sd

    from jarvis import config
    from jarvis.voice import devices

    configured = config.load().voice.input_device_name
    print(f"\nconfigured microphone: {configured!r}\n")

    print("[1] resolution")
    device = devices.resolve()
    check("resolves to a device", device is not None, str(device))
    check("resolved device matches the configured name",
          configured.casefold() in device.name.casefold(),
          f"{device.name!r} contains {configured!r}")

    default_index = sd.default.device[0]
    default_name = sd.query_devices(default_index)["name"]
    check("did NOT pick the system default mic",
          device.index != default_index,
          f"system default is {default_name!r}, using {device.name!r}")

    print("\n[2] the chosen interface actually opens at 16 kHz")
    check("opens at the rate Whisper needs",
          devices._opens(device, 16000),
          f"{device.host_api} @ 16000 Hz")

    print("\n[3] signal level")
    result = devices.measure(seconds=2.0)
    check("device delivers audio", not result["silent"],
          f"peak={result['peak']} rms={result['rms']} on {result['device']}"
          + ("  <-- DIGITAL SILENCE" if result["silent"] else ""))

    print("\n[4] refuses to fall back when the mic is missing")
    devices.invalidate()
    original = config.load().voice.input_device_name
    try:
        config.update({"voice": {"input_device_name": "NoSuchMicrophone12345"}})
        devices.invalidate()
        try:
            chosen = devices.resolve()
            check("raises instead of substituting another mic", False,
                  f"fell back to {chosen!r}")
        except devices.DeviceUnavailable as exc:
            check("raises instead of substituting another mic", True, str(exc)[:100])
    finally:
        config.update({"voice": {"input_device_name": original}})
        devices.invalidate()

    print(f"\n{'=' * 62}\n  {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("  failing: " + ", ".join(failed))
    return 1 if failed else 0


sys.exit(main())
