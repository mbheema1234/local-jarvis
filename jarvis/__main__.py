"""Entry point: ``uv run python -m jarvis``.

Threading layout:
  main thread  -- pywebview (Windows requires the GUI on the main thread)
  server       -- uvicorn and its asyncio loop; everything async lives here
  tray         -- pystray icon
  hotkeys      -- pynput global hotkey listener
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading

import webview

from . import config, singleton, ui
from .log import get, setup
from .security import ElevationError, assert_not_elevated, is_elevated

log = get("jarvis.main")

BANNER = r"""
   _   _   _____   _____  _   _ _____ ____
  | | / \ |  _  \ \  _  || | / /_   _/ ___|
  | |/ _ \| |_| |  \ | / | |/ /  | | \___ \
 _| / ___ \  _  /  / | \ |   \  _| |_ ___) |
|__/_/   \_\_| \_\/__|__\|_|\_\|_____|____/   local desktop assistant
"""


def _free_port(preferred: int) -> int:
    """Use the configured port, or the next free one if it is taken."""
    for candidate in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", candidate)) != 0:
                return candidate
    raise RuntimeError(f"No free port near {preferred}.")


def _serve(port: int) -> None:
    import uvicorn

    from .server import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def main() -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="Local desktop assistant")
    parser.add_argument("--no-window", action="store_true",
                        help="Run headless: serve the API and dashboard without a native window.")
    parser.add_argument("--port", type=int, default=None, help="Override the server port.")
    parser.add_argument("--allow-elevated", action="store_true",
                        help="Bypass the refusal to run as administrator (not recommended).")
    parser.add_argument("--allow-multiple", action="store_true",
                        help="Permit a second instance. Only useful for testing: two "
                             "copies fight over the microphone.")
    args = parser.parse_args()

    setup()
    print(BANNER)

    settings = config.load()
    if args.allow_elevated:
        settings.security.refuse_elevation = False

    try:
        assert_not_elevated()
    except ElevationError as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 2

    if is_elevated():
        log.warning("running elevated because --allow-elevated was passed")

    # One instance only. Two copies would each hold ~600 MB of speech models
    # and both listen on the same microphone, so "Hey Jarvis" would fire twice.
    if not args.allow_multiple and not singleton.acquire():
        existing = singleton.find_running(settings.ui.port)
        if existing is not None and singleton.show_existing(existing):
            print("  Jarvis is already running - brought its window to the front.\n")
        else:
            print("  Jarvis is already running. Use the tray icon to open it,\n"
                  "  or quit that copy first.\n")
        return 0

    port = args.port or _free_port(settings.ui.port)
    if port != settings.ui.port:
        log.info("port %d in use; using %d", settings.ui.port, port)

    threading.Thread(target=_serve, args=(port,), daemon=True, name="server").start()

    base = f"http://127.0.0.1:{port}"
    if not ui.wait_for_server(base):
        print("Server did not start in time; check data/jarvis.log", file=sys.stderr)
        return 1

    log.info("dashboard ready at %s", base)

    if args.no_window:
        print(f"\n  Dashboard: {base}\n  Ctrl+C to stop.\n")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0

    shell = ui.build(port)
    ui.start_tray(shell)
    ui.start_hotkeys(shell)

    # Lets a second launch raise this window instead of starting a duplicate.
    from .server import app as server_app

    server_app.state.show_window = shell.show_dashboard

    hotkey = config.load().ui.hotkey
    print(f"  Dashboard : {base}")
    print(f"  Activate  : {hotkey}  (or click the tray icon)")
    print(f"  Panic     : {config.load().ui.panic_hotkey}\n")

    # Blocks until every window closes.
    webview.start(debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
