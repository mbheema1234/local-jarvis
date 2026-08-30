"""The native Windows shell: dashboard window, activation orb, tray, hotkeys.

pywebview hosts the UI in a real Win32 window backed by WebView2, so this is a
native desktop app rather than a browser tab. Two windows are created: the main
dashboard, and a small frameless always-on-top orb that the global hotkey
summons for hands-free activation.
"""

from __future__ import annotations

import io
import threading
import time
from typing import Any

import httpx
import webview

from ..config import load
from ..log import get

log = get("jarvis.ui")


class Shell:
    def __init__(self, port: int) -> None:
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.dashboard: webview.Window | None = None
        self.orb: webview.Window | None = None
        self._orb_visible = False
        self._tray = None
        # Closing the dashboard hides it rather than exiting, so "Hey Jarvis"
        # keeps working. Only an explicit Quit sets this.
        self._quitting = False
        self._hwnd = 0

    # -- HTTP helpers ------------------------------------------------------

    def _post(self, path: str, payload: dict | None = None) -> dict:
        try:
            response = httpx.post(f"{self.base}{path}", json=payload or {}, timeout=10.0)
            return response.json()
        except Exception as exc:
            log.warning("POST %s failed: %s", path, exc)
            return {"ok": False, "error": str(exc)}

    # -- actions -----------------------------------------------------------

    def activate(self) -> None:
        """Summon the orb and start listening. Bound to the global hotkey."""
        self.show_orb()
        threading.Thread(target=self._post, args=("/api/activate",), daemon=True).start()

    def panic(self) -> None:
        """Stop everything and deny anything waiting on approval."""
        log.warning("panic hotkey pressed")
        self._post("/api/cancel")
        self.hide_orb()

    def set_wake(self, enabled: bool) -> None:
        """Arm or mute always-on listening (tray toggle)."""
        threading.Thread(
            target=self._post, args=("/api/wake", {"enabled": enabled}), daemon=True
        ).start()

    def show_orb(self) -> None:
        if self.orb is None:
            return
        try:
            self.orb.show()
            self._orb_visible = True
        except Exception as exc:
            log.debug("could not show orb: %s", exc)

    def hide_orb(self) -> None:
        if self.orb is None:
            return
        try:
            self.orb.hide()
            self._orb_visible = False
        except Exception as exc:
            log.debug("could not hide orb: %s", exc)

    def toggle_orb(self) -> None:
        self.hide_orb() if self._orb_visible else self.show_orb()

    def _remember_hwnd(self) -> int:
        """Cache the dashboard's window handle while it is still visible.

        Once hidden, the window has no findable title, so the handle has to be
        captured beforehand to bring it back reliably.
        """
        if self._hwnd:
            return self._hwnd
        try:
            import pygetwindow as gw

            for win in gw.getAllWindows():
                if (win.title or "").strip() == "Jarvis":
                    self._hwnd = win._hWnd
                    break
        except Exception as exc:
            log.debug("could not find the dashboard handle: %s", exc)
        return self._hwnd

    def show_dashboard(self) -> None:
        """Bring the dashboard back from the tray."""
        if self.dashboard is None:
            return

        try:
            self.dashboard.show()
            self.dashboard.restore()
        except Exception as exc:
            log.warning("pywebview could not show the dashboard: %s", exc)

        # pywebview's show() does not reliably un-hide a window that was hidden
        # from another thread, so drive the Win32 handle directly as well. Both
        # paths are idempotent, and between them the window always comes back.
        handle = self._remember_hwnd()
        if not handle:
            log.warning("no window handle; cannot force the dashboard visible")
            return
        try:
            import win32con
            import win32gui

            win32gui.ShowWindow(handle, win32con.SW_SHOW)
            win32gui.ShowWindow(handle, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(handle)
        except Exception as exc:
            log.debug("could not raise the dashboard window: %s", exc)

    def on_dashboard_closing(self) -> bool:
        """Intercept the window's X button.

        Returning False cancels the close, so the dashboard hides to the tray
        and the wake-word listener stays running. Quit from the tray to exit
        for real.
        """
        if self._quitting:
            return True
        # Grab the handle now: once hidden the window cannot be found by title.
        self._remember_hwnd()
        log.info("dashboard hidden to tray (still listening)")
        self.hide_orb()
        try:
            self.dashboard.hide()
        except Exception as exc:
            log.debug("could not hide dashboard: %s", exc)
        return False

    def quit(self) -> None:
        log.info("shutting down")
        self._quitting = True
        try:
            from ..voice.wakeword import wake

            wake.stop()
        except Exception:
            pass
        try:
            if self._tray is not None:
                self._tray.stop()
        except Exception:
            pass
        try:
            if self.orb is not None:
                self.orb.destroy()
        except Exception:
            pass
        try:
            if self.dashboard is not None:
                self.dashboard.destroy()
        except Exception:
            pass


class OrbApi:
    """Methods the orb page can call via ``pywebview.api``."""

    def __init__(self, shell: Shell) -> None:
        self._shell = shell

    def activate(self) -> dict:
        threading.Thread(
            target=self._shell._post, args=("/api/activate",), daemon=True
        ).start()
        return {"ok": True}

    def cancel(self) -> dict:
        return self._shell._post("/api/cancel")

    def hide(self) -> dict:
        self._shell.hide_orb()
        return {"ok": True}

    def open_dashboard(self) -> dict:
        self._shell.show_dashboard()
        return {"ok": True}


# --------------------------------------------------------------------------
# Tray icon
# --------------------------------------------------------------------------


def _tray_image(size: int = 64):
    """Draw the tray icon at runtime so there is no binary asset to ship."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for i, (radius, colour) in enumerate(
        [(30, (34, 211, 238, 60)), (23, (34, 211, 238, 130)), (15, (125, 240, 255, 255))]
    ):
        centre = size / 2
        draw.ellipse(
            [centre - radius, centre - radius, centre + radius, centre + radius],
            fill=colour,
        )
    return image


def start_tray(shell: Shell) -> None:
    import pystray

    from ..config import load

    def wake_enabled(_item: object) -> bool:
        return load().voice.wake_enabled

    def toggle_wake(_icon: object, _item: object) -> None:
        shell.set_wake(not load().voice.wake_enabled)

    menu = pystray.Menu(
        pystray.MenuItem("Talk to Jarvis", lambda: shell.activate(), default=True),
        pystray.MenuItem('Listen for "Hey Jarvis"', toggle_wake,
                         checked=wake_enabled),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open dashboard", lambda: shell.show_dashboard()),
        pystray.MenuItem("Show/hide orb", lambda: shell.toggle_orb()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Stop everything", lambda: shell.panic()),
        pystray.MenuItem("Quit", lambda: shell.quit()),
    )
    icon = pystray.Icon("jarvis", _tray_image(), "Jarvis", menu)
    shell._tray = icon
    threading.Thread(target=icon.run, daemon=True, name="tray").start()
    log.info("tray icon running")


# --------------------------------------------------------------------------
# Global hotkeys
# --------------------------------------------------------------------------


def start_hotkeys(shell: Shell) -> None:
    """Register system-wide hotkeys.

    pynput's low-level hook works for an unelevated process, which is what we
    want -- note it cannot see keys pressed while an elevated window has focus,
    a Windows restriction we accept rather than work around.
    """
    from pynput import keyboard

    settings = load().ui
    bindings: dict[str, Any] = {}

    if settings.hotkey:
        bindings[settings.hotkey] = shell.activate
    if settings.panic_hotkey:
        bindings[settings.panic_hotkey] = shell.panic

    if not bindings:
        return

    try:
        listener = keyboard.GlobalHotKeys(bindings)
        listener.daemon = True
        listener.name = "hotkeys"
        listener.start()
        log.info("hotkeys registered: %s", ", ".join(bindings))
    except Exception as exc:
        log.error("could not register hotkeys (%s); use the tray instead", exc)


# --------------------------------------------------------------------------
# Window creation
# --------------------------------------------------------------------------


def wait_for_server(base: str, timeout: float = 25.0) -> bool:
    """Block until the API answers, so the window never loads a dead page."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/stats", timeout=2.0).status_code == 200:
                return True
        except Exception:
            time.sleep(0.25)
    return False


def build(port: int) -> Shell:
    shell = Shell(port)
    settings = load().ui

    shell.dashboard = webview.create_window(
        "Jarvis",
        f"{shell.base}/",
        width=settings.window_width,
        height=settings.window_height,
        min_size=(940, 620),
        background_color="#0a0e17",
        hidden=settings.start_minimized,
        on_top=settings.always_on_top,
    )

    shell.orb = webview.create_window(
        "Jarvis Orb",
        f"{shell.base}/static/orb.html",
        width=210,
        height=210,
        frameless=True,
        easy_drag=True,
        on_top=True,
        transparent=True,
        hidden=True,
        background_color="#0a0e17",
        js_api=OrbApi(shell),
    )

    shell.dashboard.events.closing += shell.on_dashboard_closing
    return shell
