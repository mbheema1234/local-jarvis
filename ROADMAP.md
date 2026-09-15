# Roadmap

## v0.2.0 — production (tagged on `main`)

Merged from `dev` and tagged on `main`. This is what runs day to day now.
`v0.1.0` (commit `116ca70`) remains tagged as the prior baseline if a rollback
is ever needed.

A local, voice-driven desktop assistant for Windows with 73 tools across:

- **Apps** — launch, close, force-quit, list installed/running, fuzzy name matching.
- **Windows** — focus, minimize/maximize/close, snap, tile, show desktop.
- **System** — volume, mute, media keys, brightness, lock, sleep/shutdown/restart,
  hardware stats, network status, toast notifications, clipboard.
- **Other apps' settings** — `inspect_app`/`click_element`/`select_option`/
  `read_app_value` reach inside any already-open window (including Electron
  apps and browser tabs) by control name, no app-specific integration needed.
- **Web** — `search_web`, `search_news`, `research` (Perplexity Sonar), and a
  page-reading `fetch_url`, hardened against non-HTML content and unbounded
  downloads.
- **UI menu navigation** — `list_menu_items`/`click_menu_item` open and
  navigate menu bars and nav/hamburger flyouts in any app.
- **Gmail** — `search_emails`/`read_email`/`send_email` via OAuth (see setup
  status below).
- **Input** — keyboard/mouse automation, multi-monitor aware.
- **Files** — search, list, read, write, move, delete-to-Recycle-Bin, open.
- **Screen** — multi-monitor vision (`see_screen`, `find_on_screen`).
- **Assistant** — persistent memory, timers/reminders, routines.

Security model: never elevates, hard guards nothing can turn off (shell
pattern-screening, fenced writes, Recycle-Bin-only deletes), tiered
SAFE/MODERATE/HIGH confirmation, full audit log. See `README.md` → "Security
model" for the complete picture — none of it changed in v0.2.0.

### What's new in v0.2.0

Three features, each independently verified live before being committed (see
each commit's message for exactly what was tested).

#### Web reading — hardened `fetch_url`
`jarvis/tools/web.py`

The existing page-reader was upgraded rather than replaced: re-tiered
`SAFE → MODERATE` (it fetches untrusted external content over the network),
gained Content-Type sniffing (HTML gets tag-stripped, plain-text/JSON/XML is
returned verbatim, PDFs/images/binaries are cleanly rejected instead of being
mangled), and a hard 8MB streaming download cap so a fetch can't buffer an
arbitrary-size response into memory. A `SECURITY` docstring note and a system
prompt addition treat fetched page content as untrusted data, never
instructions — a page engineered to say "ignore previous instructions" is
something to report to the user, not obey.

**Verified live** against a real HTML page, a real JSON API, and a real PDF
URL (clean rejection, no garbage output). The exact 8MB cutoff was code-
reviewed but not exercised against real >8MB traffic — a known, minor,
accepted gap.

#### UI menu navigation
`jarvis/tools/uia.py`

Two new tools, both **`Risk.HIGH`** (a deliberate choice — new UI-automation
capability defaults to confirm-required, distinct from the existing
MODERATE-tiered `click_element`/`select_option`):

- `list_menu_items(window, menu)` — opens a menu bar item or nav/hamburger
  flyout button and lists what becomes visible, without clicking anything.
- `click_menu_item(window, menu, item)` — opens the menu and clicks a named
  item inside it, verified against the control's actual state afterward
  (not just trusting the click), leaving no menu hanging open on failure.

Menu items, like dropdown options, generally don't exist in the accessibility
tree until the menu is opened — this uses UIA's `ExpandCollapsePattern` to
know for certain whether a menu is already open (so a second call doesn't
toggle it shut), falling back to a tree-diff heuristic where that pattern
isn't supported.

**Verified live** against two structurally different real apps: Windows 11
Notepad's classic `File`/`Edit`/`View` menu bar (toggling Word Wrap on and
off, confirmed against the control's real `TogglePattern.ToggleState` read
independently of the tool's own report — not just the tool's self-reported
success) and Calculator's `Open Navigation` hamburger flyout (switching to
Scientific mode and back, confirmed via the mode label text). Error paths
(unknown menu, unknown item) fail cleanly and never leave a menu open.

#### Gmail integration
`jarvis/tools/email.py`, `scripts/gmail_auth.py`

OAuth-based (not raw SMTP/IMAP/app passwords), via
`google-api-python-client` + `google-auth-oauthlib`. Three tools:

- `search_emails(query, max_results)` — **MODERATE**
- `read_email(message_id)` — **MODERATE**
- `send_email(to, subject, body, cc)` — **HIGH**, no override; confirmed live
  that the confirmation gate runs *before* any credential check, so a send
  can never bypass user confirmation regardless of credential state.

Requests only `gmail.readonly` + `gmail.send` scopes — not the broader
`gmail.modify` or full-mailbox access. A one-time interactive script
(`scripts/gmail_auth.py`) handles the browser OAuth consent flow, which
cannot be completed by an agent, and saves the resulting token to
`data/gmail_token.json` (gitignored).

**Fully live and verified against the real account** — `scripts/check_email_live.py`,
26/26 passing. `search_emails`/`read_email` return real inbox data (subjects,
senders, dates, decoded plain-text bodies); `send_email`'s HIGH risk tier and
confirmation gate are verified against the real event bus and policy resolver
(denied before any network call could happen) and its MIME construction is
verified against a stubbed Gmail service — no real send was ever made.

**Deliberately deferred**, not built: attachments, HTML email, drafts,
reply-threading, multi-account support.

---

## v0.2.1-dev — on `dev`, not yet released

#### Right-click context menus
`jarvis/tools/uia.py`, `scripts/check_context_menu.py`

The top item from the previous "What's next" list, now built. Two new tools,
both **`Risk.HIGH`**, matching the tier the existing menu-bar tools use:

- `list_context_menu_items(window, target)` — right-clicks a named element
  and lists what the context menu reveals, without clicking anything in it.
- `click_context_menu_item(window, target, item)` — right-clicks a named
  element and clicks a named item in the menu that appears, cleaning up
  rather than leaving a menu hanging open on failure.

Context menus need a different approach from `_open_menu`'s menu bars and
flyouts: there is no dedicated opener control with an `ExpandCollapsePattern`
to read, and a second right-click *dismisses* an open menu rather than being
the harmless no-op a second left-click on a menu-bar button is. So the tools
detect an already-open menu before clicking anything — either a top-level
shell-owned popup (classic Win32: Explorer, desktop, taskbar) or a `Menu`
node nested inside the target window's own tree (WinUI/UWP, modern Notepad
among them).

**Verified live** against real Windows 11 Notepad — 15/15, deterministic
across three consecutive runs. `Select all` then `Copy` was confirmed
against the **real Windows clipboard** read by `Get-Clipboard` outside Jarvis
entirely, not the tools' own self-reported success. Both error paths
(nonexistent target, nonexistent item) fail cleanly and leave no menu open.
Return-path logging confirmed the back-to-back reuse cases are served by the
pre-click detection path specifically, so they pass because reuse genuinely
works rather than because a fresh reopen happens to paper over it.

Getting there took three distinct bugs, each hidden behind the one before it,
all found by live testing rather than review — worth recording, since the
first two were individually plausible and individually insufficient:

1. **Stale diff baseline.** Items were found by diffing the window tree
   before vs. after the right-click. With a menu already open from a prior
   call, its items were already in the "before" snapshot, so every item of
   the genuinely-open menu filtered out as "not new" — an empty list, and a
   misleading "No item named X" error.
2. **Size-dependent geometry test.** The already-open check matched a
   candidate by testing whether the *menu's center* was within 400px of the
   click point. A context menu is anchored at the click point as a *corner*,
   so a tall menu's center recedes past that radius as items are added.
   Measured: click point (981, 542) against a menu at left=981, top=542 —
   the click sits exactly on the corner, 0px off. Now matched on corner
   proximity, which is independent of how tall the menu is.
3. **`IsOffscreen` pruning the whole subtree.** The tree walk skipped
   offscreen nodes with a bare `continue`, which also skips everything
   beneath them. WinUI hangs these menus under an intermediate `Window
   "Popup"` host that reports `IsOffscreen=True` with a zero-size box while
   the `Menu` inside it is genuinely on screen — so the branch holding the
   real menu was pruned before the geometry test ever ran, making fix #2
   unreachable. `_scan` in the same file already had the correct pattern:
   descend into offscreen nodes, just don't treat them as candidates.

---

## What's next

A short list of genuinely useful follow-ups noticed while building the
above — not a backlog dump.

- **Exercise the `fetch_url` 8MB cap against real traffic.** The streaming
  logic was code-reviewed, not proven against an actual >8MB response — worth
  a real test once a suitable stable large-file URL is on hand.
- **A combined integration check.** `check_fetch_url_hardening.py`,
  `check_menu_tools.py`, and Gmail's tools each have their own coverage;
  once Gmail OAuth is actually set up, a single script exercising all three
  new capabilities together (plus the existing `check_integration.py`
  permission round-trip) would catch cross-feature regressions a solo check
  can't.
- **Git push reliability.** Git Credential Manager hung repeatedly on write
  operations (`push`) during this work, resolving itself only partway
  through — read-only access was unaffected throughout. Worth switching to
  `gh auth login` or a PAT-based credential helper so this doesn't recur.
