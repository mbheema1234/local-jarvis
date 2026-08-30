"""Import smoke test: confirms every native-dependent module loads on this machine."""

import importlib
import sys

MODULES = [
    "webview", "pystray", "PIL", "pynput", "fastapi", "uvicorn", "httpx",
    "pydantic", "sounddevice", "numpy", "faster_whisper", "edge_tts",
    "pyttsx3", "pyautogui", "pygetwindow", "win32api", "win32gui", "win32con",
    "psutil", "pycaw.pycaw", "comtypes", "pyperclip", "mss", "rapidfuzz",
]

failed = []
for name in MODULES:
    try:
        importlib.import_module(name)
        print(f"  ok    {name}")
    except Exception as exc:  # noqa: BLE001 - we want every failure, not the first
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
        failed.append(name)

print()
try:
    import sounddevice as sd

    default_in = sd.query_devices(kind="input")
    print(f"default input device: {default_in['name']}  ({default_in['default_samplerate']:.0f} Hz)")
except Exception as exc:  # noqa: BLE001
    print(f"audio input probe failed: {exc}")

print(f"\n{len(MODULES) - len(failed)}/{len(MODULES)} imports ok")
sys.exit(1 if failed else 0)
