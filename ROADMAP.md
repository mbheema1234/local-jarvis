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

OAuth consent is complete and `data/gmail_token.json` holds a real, live
token. `send_email`'s risk tier and confirmation gate are **verified live**
(real event bus, real policy resolver, denied before any network call could
happen) and its MIME construction is verified against a stubbed Gmail
service. `search_emails`/`read_email` still fail — not a code defect: the
**Gmail API itself is not yet enabled** in the associated Google Cloud
project (a separate toggle from granting OAuth scopes). Both tools return
this cleanly as `{"ok": False, "error": "Gmail search failed: ..."}` rather
than crashing.

**One remaining step for the user**: visit
https://console.developers.google.com/apis/api/gmail.googleapis.com/overview?project=549471656060,
click Enable, wait a minute or two for it to propagate, then rerun
`uv run python scripts/check_email_live.py` to confirm `search_emails`/
`read_email` against the real inbox.

**Deliberately deferred**, not built: attachments, HTML email, drafts,
reply-threading, multi-account support.

---

## What's next

A short list of genuinely useful follow-ups noticed while building the
above — not a backlog dump.

- **Right-click context menus.** `list_menu_items`/`click_menu_item` cover
  menu bars and nav/hamburger flyouts, but not context menus opened by a
  right-click. Same "open, then scan" pattern would likely extend to them
  with a dedicated opener (simulate the right-click instead of a left-click
  on a named control).
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
