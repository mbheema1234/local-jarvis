"""Reading and driving the insides of other applications.

Windows UI Automation exposes an app's controls by name, which lets Jarvis find
"Cooling" or "Performance" semantically instead of hunting for pixels.

Two Windows-specific facts shape this module:

*Electron and CEF apps* (NZXT CAM, the NVIDIA App, Discord, Spotify) publish no
accessibility tree until something asks for one. Sending ``WM_GETOBJECT`` is the
documented nudge that makes Chromium build it; without that these apps look
completely empty. :func:`_wake_accessibility` does that before every read.

*Those same apps rarely implement the Invoke pattern*, so ``element.Invoke()``
does nothing on them. What they do report reliably is each element's bounding
rectangle -- so activation is a real mouse click at the element's centre, using
coordinates UI Automation gave us. Semantic lookup, physical click.

Everything here runs at the privileges of the current user, and UI Automation
cannot touch a window belonging to an elevated process. An app running as
admin is simply invisible to Jarvis, which is the intended boundary.
"""

from __future__ import annotations

import ctypes
import re
import time
from typing import Any

from ..log import get
from ..security import Risk
from .registry import tool

log = get("jarvis.tools.uia")

WM_GETOBJECT = 0x003D
OBJID_CLIENT = 0xFFFFFFFC

# Control types worth reporting; everything else is layout noise.
_USEFUL_TYPES = {
    "Button", "Hyperlink", "ComboBox", "ListItem", "RadioButton", "CheckBox",
    "Tab", "TabItem", "MenuItem", "Slider", "Edit", "Text", "Group", "Image",
    "Document", "TreeItem", "DataItem", "SplitButton",
}

# Elements that are purely presentational; useful to read, not to click.
_READ_ONLY_TYPES = {"Text", "Group", "Document", "Image"}


def _auto():
    """Import uiautomation with COM initialised for this worker thread."""
    import comtypes

    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # already initialised on this thread
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(2)
    return auto


def _find_window(title: str):
    """Locate a top-level window by fuzzy title match."""
    auto = _auto()
    needle = title.casefold().strip()

    windows = []
    for child in auto.GetRootControl().GetChildren():
        try:
            name = (child.Name or "").strip()
        except Exception:
            continue
        if name:
            windows.append((name, child))

    for name, win in windows:  # exact
        if name.casefold() == needle:
            return win
    for name, win in windows:  # substring
        if needle in name.casefold():
            return win

    from rapidfuzz import fuzz

    best, score = None, 0.0
    for name, win in windows:
        current = fuzz.WRatio(needle, name.casefold())
        if current > score:
            best, score = win, current
    return best if score >= 70 else None


def _wake_accessibility(window) -> None:
    """Ask Chromium-based apps to build their accessibility tree."""
    try:
        handle = window.NativeWindowHandle
    except Exception:
        return

    user32 = ctypes.windll.user32
    targets = [handle]

    # Chromium's real content lives in a "Chrome Legacy Window" child.
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def collect(child, _param):
        buf = ctypes.create_unicode_buffer(128)
        user32.GetWindowTextW(child, buf, 128)
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(child, cls, 128)
        if "Legacy" in buf.value or "Chrome_RenderWidget" in cls.value:
            targets.append(child)
        return True

    try:
        user32.EnumChildWindows(handle, collect, 0)
    except Exception:
        pass

    for target in targets:
        try:
            user32.SendMessageW(target, WM_GETOBJECT, 0, OBJID_CLIENT)
        except Exception:
            continue


def _scan(window, max_elements: int = 220, interactive_only: bool = False) -> list[dict[str, Any]]:
    """Walk a window's control tree into a flat, de-duplicated list.

    Web pages produce enormous trees -- a Gmail tab is close to a thousand
    elements -- so ``interactive_only`` keeps the result to things you can
    actually act on.
    """
    elements: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    allowed = _USEFUL_TYPES - _READ_ONLY_TYPES if interactive_only else _USEFUL_TYPES

    def walk(node, depth: int = 0) -> None:
        if depth > 22 or len(elements) >= max_elements:
            return
        try:
            children = node.GetChildren()
        except Exception:
            return
        for child in children:
            if len(elements) >= max_elements:
                return
            try:
                name = (child.Name or "").strip()
                kind = child.ControlTypeName.replace("Control", "")
                box = child.BoundingRectangle
                hidden = bool(child.IsOffscreen)
            except Exception:
                continue

            # Browsers keep a live accessibility tree for every background tab,
            # so without this filter a scan mixes several pages together and a
            # click can land on an element from a tab you cannot even see.
            # Skipping offscreen nodes also cuts the tree by an order of
            # magnitude, which keeps scans fast.
            if hidden:
                walk(child, depth + 1)
                continue

            if name and kind in allowed and box.width() > 0 and box.height() > 0:
                key = (name, kind, box.xcenter(), box.ycenter())
                if key not in seen:
                    seen.add(key)
                    elements.append({
                        "name": name[:80],
                        "type": kind,
                        "x": box.xcenter(),
                        "y": box.ycenter(),
                        "clickable": kind not in _READ_ONLY_TYPES,
                    })
            walk(child, depth + 1)

    walk(window)
    return elements


def _rank_matches(elements: list[dict[str, Any]], needle: str) -> list[dict[str, Any]]:
    """Order elements by how well their name matches, best first.

    Real UIs are full of near-collisions: Gmail's send button is named
    "Send (Ctrl-Enter)" while "More send options" and "Insert signature" also
    contain "send". Preferring exact, then prefix, then the shortest containing
    name picks the obvious one instead of whichever came first in the tree.
    """
    needle = needle.casefold().strip()
    exact, prefix, contains = [], [], []
    for element in elements:
        name = element["name"].casefold()
        if name == needle:
            exact.append(element)
        elif name.startswith(needle):
            prefix.append(element)
        elif needle in name:
            contains.append(element)

    prefix.sort(key=lambda e: len(e["name"]))
    contains.sort(key=lambda e: len(e["name"]))
    return exact + prefix + contains


def _find_control(window, name: str, types: tuple[str, ...] = ()):
    """Return the live control object for a named element, not just its box.

    Coordinates alone are not enough for form filling: clicking a text box does
    not always move keyboard focus, and web layouts shift as you fill them in.
    Holding the control lets us call SetFocus and then verify it worked.
    """
    needle = name.casefold().strip()
    best = None
    best_rank = 99

    def rank(control_name: str) -> int:
        lowered = control_name.casefold()
        if lowered == needle:
            return 0
        if lowered.startswith(needle):
            return 1
        if needle in lowered:
            return 2
        return 99

    def walk(node, depth: int = 0) -> None:
        nonlocal best, best_rank
        if depth > 22 or best_rank == 0:
            return
        try:
            children = node.GetChildren()
        except Exception:
            return
        for child in children:
            try:
                child_name = (child.Name or "").strip()
                kind = child.ControlTypeName.replace("Control", "")
                if child.IsOffscreen:
                    walk(child, depth + 1)
                    continue
            except Exception:
                continue
            if child_name and (not types or kind in types):
                score = rank(child_name)
                if score < best_rank:
                    best, best_rank = child, score
                    if score == 0:
                        return
            walk(child, depth + 1)

    walk(window)
    return best


def _focus(control) -> bool:
    """Give a control keyboard focus, falling back to a click. Verifies."""
    from .inputs import _pyautogui, on_screen

    for attempt in range(2):
        try:
            control.SetFocus()
            time.sleep(0.35)
            if control.HasKeyboardFocus:
                return True
        except Exception:
            pass

        try:
            box = control.BoundingRectangle
        except Exception:
            return False
        if not on_screen(box.xcenter(), box.ycenter()):
            return False
        _pyautogui().click(x=box.xcenter(), y=box.ycenter())
        time.sleep(0.45)
        try:
            if control.HasKeyboardFocus:
                return True
        except Exception:
            # Some elements never report focus; a click is the best we can do.
            return attempt > 0
    return False


def _open_window(title: str):
    """Find a window and prepare it for inspection, or raise a friendly error."""
    window = _find_window(title)
    if window is None:
        raise LookupError(
            f"No open window matches {title!r}. Use list_windows to see what is open."
        )
    _wake_accessibility(window)
    time.sleep(1.2)  # Chromium needs a moment to build the tree
    return window


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@tool(
    risk=Risk.SAFE,
    params={
        "window": "Title of the app window, or part of it -- 'Firefox', 'NZXT CAM'.",
        "filter": "Narrow results to element names containing this text. Use it on "
                  "web pages, which have hundreds of elements.",
        "interactive_only": "Only return things you can click or type into.",
    },
    summary=lambda a: f"Inspect the {a.get('window', '?')} window",
    tags=["apps", "uia"],
)
def inspect_app(
    window: str,
    filter: str = "",
    interactive_only: bool = False,
    max_elements: int = 120,
) -> dict:
    """List the controls and readable text inside another application's window.

    Works on web pages too: with a browser window you get the page's own
    buttons, links and text boxes by name. Pages are large, so pass a filter
    ("compose", "to", "send") or interactive_only to keep results usable.

    Element names here are exactly what click_element and set_element_text
    expect. Call it again after any click, because the page will have changed.
    """
    try:
        target = _open_window(window)
    except LookupError as exc:
        return {"ok": False, "error": str(exc)}

    cap = max(20, min(max_elements, 260))
    # Filtering happens after the walk, so scan wider when narrowing later.
    elements = _scan(target, max_elements=cap * 4 if filter else cap,
                     interactive_only=interactive_only)
    total = len(elements)

    if filter:
        needle = filter.casefold()
        elements = [e for e in elements if needle in e["name"].casefold()][:cap]

    if not elements:
        hint = (f"Nothing matched {filter!r}; try a shorter or different word."
                if filter else
                f"{window!r} exposed no readable controls. Some apps only do so "
                f"once focused, or draw everything as a picture -- try "
                f"see_screen and find_on_screen instead.")
        return {"ok": False, "error": hint}

    try:
        title = target.Name
    except Exception:
        title = window
    result = {
        "window": title,
        "count": len(elements),
        "elements": elements,
        "hint": "Use click_element or set_element_text with an exact name from this list.",
    }
    if filter:
        result["scanned"] = total
        result["filtered_by"] = filter
    return result


@tool(
    risk=Risk.MODERATE,
    params={
        "window": "Title of the app window.",
        "name": "Exact element name from inspect_app.",
        "occurrence": "Which match to use when several share a name (1 = first).",
    },
    summary=lambda a: f"Click {a.get('name', '?')!r} in {a.get('window', '?')}",
    tags=["apps", "uia"],
)
def click_element(window: str, name: str, occurrence: int = 1) -> dict:
    """Click a named control inside another application.

    Brings the window forward and clicks the element's centre. After clicking,
    call inspect_app again to see what changed before deciding the next step.
    """
    from .inputs import _pyautogui, on_screen

    try:
        target = _open_window(window)
    except LookupError as exc:
        return {"ok": False, "error": str(exc)}

    elements = _scan(target, max_elements=700)
    matches = _rank_matches(elements, name)
    if not matches:
        near = [e["name"] for e in elements if e["clickable"]][:12]
        return {
            "ok": False,
            "error": f"No element named {name!r} in {window!r}.",
            "available": near,
        }

    index = max(1, occurrence) - 1
    if index >= len(matches):
        return {
            "ok": False,
            "error": f"Only {len(matches)} element(s) named {name!r}; "
                     f"occurrence {occurrence} does not exist.",
        }
    element = matches[index]

    if not on_screen(element["x"], element["y"]):
        return {
            "ok": False,
            "error": f"{name!r} is at ({element['x']}, {element['y']}), which is "
                     f"off the desktop -- the window may be minimised.",
        }

    try:
        target.SetActive()
    except Exception:
        log.debug("could not focus %s before clicking", window)
    time.sleep(0.25)

    pyautogui = _pyautogui()
    pyautogui.click(x=element["x"], y=element["y"])
    time.sleep(0.6)  # let the app react before anything reads the tree again

    return {
        "clicked": element["name"],
        "type": element["type"],
        "at": [element["x"], element["y"]],
        "window": window,
        "note": "Call inspect_app again to see the resulting state.",
    }


@tool(
    risk=Risk.MODERATE,
    params={
        "window": "Title of the app window, e.g. 'Firefox'.",
        "field": "Name of the text box, exactly as inspect_app reported it.",
        "text": "The text to type into it.",
        "clear": "Replace what is already in the field. Leave this off when "
                 "filling in an empty form.",
        "submit": "Press Enter afterwards. Use for search boxes and address bars.",
    },
    summary=lambda a: f"Type into {a.get('field', '?')!r} in {a.get('window', '?')}",
    tags=["apps", "uia"],
)
def set_element_text(
    window: str,
    field: str,
    text: str,
    clear: bool = False,
    submit: bool = False,
) -> dict:
    """Type text into a named text box in another app or web page.

    Clicks the field to focus it, then types. This is how you fill in a form --
    an email's To and Subject boxes, a search field, a login form.
    """
    from .inputs import _pyautogui, on_screen

    try:
        target = _open_window(window)
    except LookupError as exc:
        return {"ok": False, "error": str(exc)}

    try:
        target.SetActive()
    except Exception:
        pass
    time.sleep(0.3)

    control = _find_control(target, field, ("Edit", "ComboBox", "Document"))
    if control is None:
        control = _find_control(target, field)
    if control is None:
        editable = [e["name"] for e in _scan(target, max_elements=700)
                    if e["type"] in ("Edit", "ComboBox", "Document")]
        return {
            "ok": False,
            "error": f"No field named {field!r} in {window!r}.",
            "available_fields": editable[:15],
        }

    resolved = (control.Name or field).strip()

    if not _focus(control):
        return {
            "ok": False,
            "error": f"Could not put the cursor in {resolved!r}. It may be "
                     f"hidden behind another window.",
        }

    pyautogui = _pyautogui()

    if clear:
        # Triple-click selects only this field's contents. Ctrl+A is scoped to
        # the whole document in a rich web editor -- in Gmail's compose it
        # selects the entire message, so typing then wipes it.
        try:
            box = control.BoundingRectangle
            pyautogui.click(x=box.xcenter(), y=box.ycenter(), clicks=3, interval=0.08)
            time.sleep(0.25)
            pyautogui.press("delete")
            time.sleep(0.15)
        except Exception:
            pass

    # Typed rather than pasted, so the page's own input handlers fire -- rich
    # editors like Gmail's ignore text injected any other way.
    pyautogui.write(text, interval=0.012)
    time.sleep(0.5)

    # Read the field back where we can. Only a real value counts: an element's
    # Name is its label ("Subject"), so comparing against that would report
    # failure every time the text actually landed correctly.
    observed: str | None = None
    try:
        if control.IsValuePatternAvailable():
            observed = control.GetValuePattern().Value or ""
    except Exception:
        observed = None

    landed = (text.strip()[:24].casefold() in observed.casefold()
              if observed is not None else None)

    if submit:
        pyautogui.press("enter")
        time.sleep(0.6)

    result = {
        "field": resolved,
        "typed": len(text),
        "submitted": submit,
        "window": window,
    }
    if landed is True:
        result["verified"] = True
        result["note"] = f"Text is in {resolved!r}. Move on to the next field."
    elif landed is False:
        result["ok"] = False
        result["verified"] = False
        result["observed"] = (observed or "")[:120]
        result["note"] = (
            f"{resolved!r} reports different contents -- the text may have gone "
            f"into whichever field had focus. Check with see_screen before retrying."
        )
    else:
        # Most web form fields expose no readable value; focus was confirmed
        # before typing, so this is expected rather than suspicious.
        result["verified"] = None
        result["note"] = (f"Typed into {resolved!r}. That field does not report its "
                          f"own contents, so use see_screen if you need certainty.")
    return result


@tool(
    risk=Risk.MODERATE,
    params={
        "window": "Title of the app window.",
        "option": "The option to select, e.g. 'Silent' or 'Performance'.",
        "opener": "Element to click first to reveal the options -- in most modern "
                  "apps, the currently-selected value itself. Give several "
                  "comma-separated alternatives when you don't know which is "
                  "showing, e.g. 'Performance, Silent, Fixed'.",
    },
    summary=lambda a: f"Select {a.get('option', '?')!r} in {a.get('window', '?')}",
    tags=["apps", "uia"],
)
def select_option(window: str, option: str, opener: str = "") -> dict:
    """Pick an option from a dropdown or settings list inside another app.

    Handles the common case where the options only exist once the control is
    open: it clicks ``opener`` to expand the list, then clicks ``option``.
    In apps like NZXT CAM the opener is the currently-selected value -- clicking
    "Performance" opens the list that contains "Silent".
    """
    from .inputs import _pyautogui, on_screen

    try:
        target = _open_window(window)
    except LookupError as exc:
        return {"ok": False, "error": str(exc)}

    needle = option.casefold().strip()

    def locate(elements):
        exact = [e for e in elements if e["name"].casefold() == needle]
        return exact[0] if exact else next(
            (e for e in elements if needle in e["name"].casefold()), None
        )

    pyautogui = _pyautogui()
    try:
        target.SetActive()
    except Exception:
        pass
    time.sleep(0.25)

    elements = _scan(target)
    found = locate(elements)
    opened_with = None

    # Not on screen yet -- open the control that reveals it.
    if found is None and opener:
        # Accept several candidates, comma-separated. Whichever value a
        # dropdown currently shows is the thing you have to click to open it,
        # and that changes with the setting -- so a saved routine needs to be
        # able to name every mode it might find there.
        candidates = [c.strip().casefold() for c in opener.split(",") if c.strip()]
        control = None
        for candidate in candidates:
            control = next(
                (e for e in elements if e["name"].casefold() == candidate),
                next((e for e in elements if candidate in e["name"].casefold()), None),
            )
            if control is not None:
                break

        if control is None:
            return {
                "ok": False,
                "error": f"None of {opener!r} were present in {window!r} to open.",
                "available": [e["name"] for e in elements if e["clickable"]][:12],
            }
        if not on_screen(control["x"], control["y"]):
            return {"ok": False, "error": f"{opener!r} is off-screen."}

        pyautogui.click(x=control["x"], y=control["y"])
        opened_with = control["name"]
        time.sleep(1.0)
        elements = _scan(target)
        found = locate(elements)

    if found is None:
        # Leave no dropdown hanging open on failure.
        if opened_with:
            pyautogui.press("escape")
        return {
            "ok": False,
            "error": f"{option!r} did not appear in {window!r}"
                     + (f" after opening {opened_with!r}." if opened_with
                        else ". Pass 'opener' -- usually the currently-selected value."),
            "available": [e["name"] for e in elements][:20],
        }

    if not on_screen(found["x"], found["y"]):
        return {"ok": False, "error": f"{option!r} is off-screen."}

    pyautogui.click(x=found["x"], y=found["y"])
    time.sleep(1.2)

    # Confirm it stuck rather than trusting the click. Accessible listboxes
    # announce their state as "option X selected, 2 of 5", which is a far
    # stronger signal than the option's name merely being present -- the name
    # stays visible either way, since it is still a choice in the list.
    after = [e["name"] for e in _scan(target)]
    announced = next(
        (n for n in after if re.search(r"option\s+.+?\s+selected", n, re.IGNORECASE)),
        None,
    )
    if announced:
        match = re.search(r"option\s+(.+?)\s+selected", announced, re.IGNORECASE)
        chosen = (match.group(1) if match else "").strip()
        confirmed = needle in chosen.casefold()
    else:
        confirmed = any(needle in n.casefold() for n in after)

    return {
        "ok": confirmed,
        "selected": found["name"],
        "opened_with": opened_with,
        "window": window,
        "confirmed": confirmed,
        "note": f"{found['name']} is now selected in {window}. The change is done; "
                f"tell the user and stop." if confirmed
                else f"Clicked {found['name']}, but {window} did not confirm the "
                     f"change. Check with inspect_app before retrying.",
    }


@tool(
    risk=Risk.SAFE,
    params={
        "window": "Title of the app window.",
        "label": "Text label whose nearby value you want, e.g. 'CPU Temperature'.",
    },
    summary=lambda a: f"Read {a.get('label', '?')!r} from {a.get('window', '?')}",
    tags=["apps", "uia"],
)
def read_app_value(window: str, label: str) -> dict:
    """Read a value displayed next to a label in another application.

    Useful for questions like "what does CAM say my liquid temperature is".
    """
    try:
        target = _open_window(window)
    except LookupError as exc:
        return {"ok": False, "error": str(exc)}

    elements = _scan(target)
    needle = label.casefold().strip()

    anchors = [e for e in elements if needle in e["name"].casefold()]

    if not anchors:
        # Labels rarely match a spoken phrase word for word -- someone asking
        # for "pump speed" wants the element called "Pump". Fall back to the
        # individual words, most specific first.
        words = [w for w in needle.split() if len(w) > 2]
        for word in sorted(words, key=len, reverse=True):
            anchors = [e for e in elements if word in e["name"].casefold()]
            if anchors:
                log.debug("matched %r via word %r", label, word)
                break

    if not anchors:
        readable = [e["name"] for e in elements if e["type"] in ("Text", "Group")][:20]
        return {
            "ok": False,
            "error": f"Nothing labelled {label!r} in {window!r}.",
            "available_labels": readable,
        }

    # Grouped controls often carry the whole reading in their own name
    # ("Pump 2607 RPM Liquid Performance"), which beats guessing by geometry.
    for anchor in anchors:
        if anchor["type"] == "Group" and len(anchor["name"]) > len(label) + 2:
            return {"label": label, "value": anchor["name"], "source": "group"}

    anchor = anchors[0]
    nearby = sorted(
        (e for e in elements
         if e is not anchor
         and abs(e["y"] - anchor["y"]) < 60
         and 0 < abs(e["x"] - anchor["x"]) < 320),
        key=lambda e: (abs(e["y"] - anchor["y"]), abs(e["x"] - anchor["x"])),
    )
    if not nearby:
        return {"label": anchor["name"], "value": anchor["name"], "source": "self"}

    return {
        "label": anchor["name"],
        "value": " ".join(e["name"] for e in nearby[:4]),
        "source": "nearby",
    }


@tool(
    risk=Risk.SAFE,
    params={
        "window": "Title of the app window.",
        "name": "Element name to wait for.",
        "timeout": "Seconds to keep checking.",
    },
    summary=lambda a: f"Wait for {a.get('name', '?')!r} in {a.get('window', '?')}",
    tags=["apps", "uia"],
)
def wait_for_element(window: str, name: str, timeout: float = 8.0) -> dict:
    """Wait for an element to appear after a click.

    Use between steps when an app animates or loads a page, so the next click
    doesn't land before its target exists.
    """
    deadline = time.time() + max(1.0, min(timeout, 30.0))
    needle = name.casefold().strip()

    while time.time() < deadline:
        try:
            target = _open_window(window)
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        for element in _scan(target):
            if needle in element["name"].casefold():
                return {"found": element["name"], "at": [element["x"], element["y"]]}
        time.sleep(0.5)

    return {"ok": False, "error": f"{name!r} did not appear within {timeout:g}s."}
