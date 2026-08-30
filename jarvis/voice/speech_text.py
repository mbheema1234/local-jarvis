"""Turn written text into something worth listening to.

The model is asked for plain prose, but it still reaches for markdown,
symbols, URLs and paths — and a speech engine reads those literally, so
"**radio**" comes out as "asterisk asterisk radio asterisk asterisk".

Everything here runs only on the way to the speaker. The dashboard keeps the
original text, formatting and all.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

_FENCED_CODE = re.compile(r"```[\w+-]*\n?.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_RULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$", re.MULTILINE)
_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)

# Emphasis: run longest-first so ** is consumed before *. Requires a
# non-space next to the marker so "2 * 3" and "a_b_c" survive intact.
_EMPHASIS = [
    re.compile(r"\*\*\*(\S(?:.*?\S)?)\*\*\*", re.DOTALL),
    re.compile(r"\*\*(\S(?:.*?\S)?)\*\*", re.DOTALL),
    re.compile(r"(?<![\w*])\*(\S(?:.*?\S)?)\*(?![\w*])", re.DOTALL),
    re.compile(r"(?<![\w_])__(\S(?:.*?\S)?)__(?![\w_])", re.DOTALL),
    re.compile(r"(?<![\w_])_(\S(?:.*?\S)?)_(?![\w_])", re.DOTALL),
]

# --------------------------------------------------------------------------
# Web and filesystem noise
# --------------------------------------------------------------------------

_URL = re.compile(r"https?://([^\s/)\]]+)(?:/[^\s)\]]*)?", re.IGNORECASE)
_WWW = re.compile(r"\bwww\.([^\s/)\]]+)", re.IGNORECASE)
_EMAIL = re.compile(r"\b([\w.+-]+)@([\w.-]+\.\w+)\b")
_WIN_PATH = re.compile(r"\b[A-Za-z]:\\(?:[^\s\\/:*?\"<>|]+\\)*([^\s\\/:*?\"<>|]*)")

# Emoji and other pictographs, which some engines announce by name.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF️‍]+"
)

# Invisible formatting marks; Gmail's button labels are full of these.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁯﻿]")

# --------------------------------------------------------------------------
# Symbol and unit replacements
# --------------------------------------------------------------------------

_SYMBOLS = [
    (re.compile(r"\s*[→⟶>]{1,2}\s*(?=\w)"), " then "),
    (re.compile(r"\s*[←⟵]\s*"), " from "),
    (re.compile(r"\s*[—–]\s*"), ", "),
    (re.compile(r"\s*\|\s*"), ", "),
    (re.compile(r"[•·]"), " "),
    (re.compile(r"\.{3,}|…"), ", "),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"(?<=\w)/(?=\w)"), " or "),
    (re.compile(r"[\"“”«»]"), ""),
    (re.compile(r"(?<=\d)\s*°\s*C\b", re.IGNORECASE), " degrees"),
    (re.compile(r"(?<=\d)\s*°\s*F\b", re.IGNORECASE), " degrees Fahrenheit"),
    (re.compile(r"(?<=\d)\s*°"), " degrees"),
    (re.compile(r"(?<=\d)\s*%"), " percent"),
    (re.compile(r"~(?=\d)"), "about "),
    (re.compile(r"(?<=\d)\s*x\s*(?=\d)"), " by "),
    # A lone asterisk between numbers is multiplication, not leftover markdown,
    # so it has to be named before the catch-all strip removes it.
    (re.compile(r"(?<=\d)\s*\*\s*(?=\d)"), " times "),
    (re.compile(r"(?<=\d)\s*\+\s*(?=\d)"), " plus "),
]

# Written forms a speech engine mangles or spells out letter by letter.
_UNITS = [
    (r"GB", "gigabytes"), (r"MB", "megabytes"), (r"KB", "kilobytes"),
    (r"TB", "terabytes"), (r"GHz", "gigahertz"), (r"MHz", "megahertz"),
    (r"ms", "milliseconds"), (r"fps", "F P S"), (r"kg", "kilograms"),
]
_UNIT_PATTERNS = [
    (re.compile(rf"(?<=\d)\s*{written}\b"), f" {spoken}")
    for written, spoken in _UNITS
]

_ABBREVIATIONS = [
    (re.compile(r"\be\.g\.", re.IGNORECASE), "for example"),
    (re.compile(r"\bi\.e\.", re.IGNORECASE), "that is"),
    (re.compile(r"\betc\.", re.IGNORECASE), "and so on"),
    (re.compile(r"\bvs\.?\b", re.IGNORECASE), "versus"),
    (re.compile(r"\bw/\b"), "with"),
    (re.compile(r"\bapprox\.", re.IGNORECASE), "approximately"),
]


def _shorten_domain(host: str) -> str:
    """example.com -> example dot com, minus any www."""
    host = re.sub(r"^www\.", "", host, flags=re.IGNORECASE)
    return host.replace(".", " dot ")


def for_speech(text: str, max_chars: int = 1200) -> str:
    """Rewrite ``text`` so a speech engine reads it the way a person would."""
    if not text:
        return ""

    out = _INVISIBLE.sub("", text)

    # Structure first, so markers never survive into the prose.
    out = _FENCED_CODE.sub(" ", out)
    out = _IMAGE.sub(r"\1", out)
    out = _LINK.sub(r"\1", out)
    out = _INLINE_CODE.sub(r"\1", out)
    out = _RULE.sub(" ", out)
    out = _HEADING.sub("", out)
    out = _BLOCKQUOTE.sub("", out)
    out = _STRIKE.sub(r"\1", out)
    for pattern in _EMPHASIS:
        out = pattern.sub(r"\1", out)

    # Lists become sentences; a bullet read aloud is just a pause.
    out = _BULLET.sub("", out)
    out = _NUMBERED.sub("", out)
    out = re.sub(r"^\s*\|[-:\s|]+\|\s*$", "", out, flags=re.MULTILINE)  # table rules

    # Web and filesystem noise.
    out = _EMAIL.sub(lambda m: f"{m.group(1)} at {_shorten_domain(m.group(2))}", out)
    out = _URL.sub(lambda m: _shorten_domain(m.group(1)), out)
    out = _WWW.sub(lambda m: _shorten_domain(m.group(1)), out)
    out = _WIN_PATH.sub(lambda m: m.group(1) or "that folder", out)

    out = _EMOJI.sub(" ", out)

    for pattern, replacement in _SYMBOLS:
        out = pattern.sub(replacement, out)
    for pattern, replacement in _UNIT_PATTERNS:
        out = pattern.sub(replacement, out)
    for pattern, replacement in _ABBREVIATIONS:
        out = pattern.sub(replacement, out)

    # Any stray emphasis characters left over from unbalanced markup.
    out = re.sub(r"(?<!\w)[*_~`#](?!\w)", " ", out)
    out = out.replace("*", " ").replace("`", " ")

    # Newlines become sentence breaks so the voice pauses naturally.
    out = re.sub(r"\n{2,}", ". ", out)
    out = re.sub(r"\n", ", ", out)

    # Tidy the punctuation the substitutions above tend to leave behind.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,;:])\s*(?=[,.;:!?])", "", out)
    out = re.sub(r"\.\s*,", ".", out)
    out = re.sub(r"(?:,\s*){2,}", ", ", out)
    out = re.sub(r"\.{2,}", ".", out)
    out = re.sub(r"^\s*[,.;:]\s*", "", out)
    out = out.strip()

    if len(out) > max_chars:
        # Cut at a sentence end rather than mid-word.
        clipped = out[:max_chars]
        stop = max(clipped.rfind(". "), clipped.rfind("! "), clipped.rfind("? "))
        out = clipped[: stop + 1] if stop > max_chars // 2 else clipped.rsplit(" ", 1)[0]

    return out
