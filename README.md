# Jarvis

A local, voice-driven desktop assistant for Windows. Native window, real
control of your machine, and a hard rule that it never asks for administrator
rights.

Say *"Hey Jarvis, open the Epic Games Launcher and my NVIDIA app, I want to
game"* and it opens both.

---

## Quick start

```powershell
cd C:\Users\bheem\projects\jarvis

# 1. Your OpenRouter key lives here (already created, gitignored)
notepad .env

# 2. Install dependencies and run
.\scripts\Start-Jarvis.ps1

# 3. Optional: desktop + Start Menu shortcuts
.\scripts\Install-Shortcuts.ps1            # add -AtLogin to start with Windows
```

First launch downloads the Whisper speech model (~150 MB) in the background.

| | |
|---|---|
| **Activate** | say **"Hey Jarvis"**, press `Ctrl+Alt+J`, click the orb, or the tray icon |
| **Stop everything** | `Ctrl+Alt+Esc`, or `Esc` in the dashboard |
| **Mute listening** | click the *Listening* chip, or the tray menu |
| **Dashboard** | opens automatically; also at `http://127.0.0.1:8787` |

Say "Hey Jarvis" and his face appears over whatever you're doing and starts
listening. Stop talking and he acts — no button to hold down.

**It's a conversation, not a series of commands.** After a reply Jarvis keeps
listening, so follow-ups need no wake word:

> **You:** Hey Jarvis, how many monitors do I have?
> **Jarvis:** You have three, Sir. The main one is Firefox…
> **You:** What's on the second one?          *(no "Hey Jarvis" needed)*
> **Jarvis:** The portrait one has NZXT CAM, Discord and the NVIDIA App.
> **You:** Thanks Jarvis.                      *(ends it)*

The conversation closes after ten seconds of silence, or when you thank him or
say goodbye. Turn it off in Settings if you prefer one command at a time.

Closing the dashboard window **hides it to the tray** rather than quitting, so
Jarvis keeps listening. Launching it again raises the existing window instead of
starting a second copy. Quit properly from the tray menu.

---

## His face

Jarvis has one — a simple cartoon in the spirit of Eddy from *Lab Rats*: a
rounded frame, two crescent eyes and a big open grin. It is drawn as SVG paths
rewritten every frame, so expressions blend rather than switching between fixed
images.

| State | What he does |
|---|---|
| idle | content, breathing, blinking at irregular intervals |
| listening | eyes open with pupils, brows up, mouth reacting to the mic |
| thinking | looking up and away, one brow raised, mouth a flat line |
| speaking | happy eyes, mouth driven by the **actual loudness** of the audio |

That last one is real lip-sync, not a timed flap: the speech is analysed into a
40 ms loudness envelope and streamed to the UI as it plays.

---

## Always-on "Hey Jarvis"

Runs on openWakeWord's pretrained `hey_jarvis` model — a real wake-word model
for this exact phrase, not keyword-matching over a transcript.

**How it behaves**

- Listening is armed whenever Jarvis is idle, and only then. The moment a turn
  starts, the listener releases the microphone so the recorder can have it, and
  re-arms half a second after the reply finishes — so Jarvis can't retrigger on
  its own voice.
- A 2.5-second cooldown stops one "Hey Jarvis" firing twice.
- Toggle it from the sidebar chip, the tray menu, or Settings. The chip flashes
  when the phrase fires.

**What it costs**

Roughly **0.1% CPU** at idle. Resident memory is ~395 MB, most of which is the
Whisper and wake models held in RAM so responses are instant.

**Privacy.** Audio is analysed in a rolling in-memory buffer and discarded
immediately — nothing is written to disk and nothing leaves the machine. Only
after the phrase fires does recording begin, and transcription still happens
locally via Whisper. Your OpenRouter key is only used once there is actual text
to reason about.

**Tuning.** Measured scores are ~0.999 on the phrase and ~0.000 on unrelated
speech, so the 0.5 default sits in a wide margin. Raise `voice.wake_threshold`
if something in your room sets it off; lower it if it misses you.

**"Even when the app is closed."** Something has to hold the microphone, so a
process must be running — nothing can listen with zero processes. What you can
have is Jarvis being *invisible*: no window, tray only, started automatically at
login:

```powershell
.\scripts\Install-Shortcuts.ps1 -AtLogin
```

It then starts hidden with Windows and listens from boot. To stop it listening
without quitting, use the tray toggle.

---

## How it's built

```
You speak  →  Whisper (local)  →  Claude Sonnet 5 via OpenRouter
                                          ↓  picks tools
                                  permission layer
                                          ↓
                                  68 desktop tools
                                          ↓
                          reply spoken back via neural TTS
```

| Layer | Choice | Why |
|---|---|---|
| Window | pywebview + WebView2 | A real Win32 window, not a browser tab |
| Speech in | faster-whisper, local | Nothing you say is uploaded; ~7–16x realtime on CPU |
| Speech out | edge-tts, SAPI fallback | Neural voices online, offline voices when not |
| Spoken text | rewritten before synthesis | Markdown, URLs and symbols are read out literally otherwise |
| Reasoning | OpenRouter | Swap models from Settings without touching code |
| App launching | `Get-StartApps` + `shell:AppsFolder` | One mechanism covers Store and desktop apps alike |

The dashboard, the orb, and the voice loop all talk to one local FastAPI server
over a WebSocket, so every surface shows the same state at the same time.

### Project layout

```
jarvis/
  __main__.py       entry point and thread layout
  config.py         settings model, loaded from config/settings.json
  appindex.py       index of every launchable app on the machine
  server.py         FastAPI: REST + WebSocket + serves the dashboard
  bus.py            pub/sub that keeps every surface in sync
  llm/              OpenRouter client, agent loop, system prompt
  voice/            mic capture, Whisper, TTS, the listen→think→speak turn
  security/         risk tiers, confirmation flow, hard guards, audit log
  tools/            the 68 capabilities, one module per domain
  ui/               native shell (window, orb, tray, hotkeys) + dashboard
scripts/            launchers, shortcut installer, verification suite
```

---

## How it sounds

Everything spoken is rewritten for the ear first (`jarvis/voice/speech_text.py`).
Speech engines read formatting literally — `**radio**` comes out as "asterisk
asterisk radio asterisk asterisk" — so before synthesis Jarvis strips markdown,
turns links into "espn dot com", shortens file paths to just the filename,
expands `38°C` and `45%` and `16GB`, drops emoji, and converts arrows and pipes
into ordinary words.

```
model wrote : **Done!** I've set cooling to *Silent* — now ~2400 RPM. See `NZXT CAM` → Cooling 🎉
you hear    : Done! I've set cooling to Silent, now about 2400 RPM. See NZXT CAM Cooling
```

The dashboard still shows the original wording, and light formatting renders
there properly instead of showing raw characters.

---

## Working on Jarvis with agents

Four specialists live in `.claude/agents/`, so ongoing work has a consistent
shape rather than being improvised each time.

| Agent | Role |
|---|---|
| `jarvis-orchestrator` | Plans the work and delegates; the one to ask for anything end-to-end |
| `jarvis-developer` | Implements features and fixes in `jarvis/` |
| `jarvis-tester` | Writes and runs `scripts/check_*.py` against the **running** app |
| `jarvis-git` | Commits and pushes to GitHub |

The intended flow is **plan → develop → test → commit**, and the orchestrator
runs it. Ask it for a whole job:

> "Add a tool for my Hue lights, test it properly, and push it."

Each agent carries the conventions that actually matter here: risk tiers on
every tool, hard guards that are never weakened to make something pass, tests
that run against real hardware rather than mocks, and a standing rule that
`.env` never reaches the remote.

Two habits are baked in deliberately, because both cost real time during the
build: a failing test is not automatically a code bug (in this project the test
has more often been the thing that was wrong), and work is never committed as
verified unless it actually was.

New agent definitions are picked up when a Claude Code session starts, so
restart the session after editing them.

---

## Security model

The design goal was *full control of your desktop, zero privilege escalation*.

**1. It never elevates.** Jarvis refuses to start if launched as Administrator.
This isn't cosmetic: running unelevated means UAC stays a real boundary, so even
a fully hijacked prompt cannot touch protected system state without you
personally clicking a UAC dialog that Jarvis has no way to press.

**2. Hard guards that nothing can turn off.** Not exposed in settings, not
reachable by the model:

- Shell commands are pattern-screened and rejected outright — privilege
  escalation (`runas`, `-Verb RunAs`), Defender tampering, `HKLM` writes,
  `bcdedit`/`diskpart`, shadow-copy deletion, remote code execution, account
  and scheduled-task creation.
- File writes are fenced to your configured roots and refuse `C:\Windows`,
  `Program Files`, `ProgramData`, drive roots, and UNC paths.
- Deletes go to the Recycle Bin, never a hard unlink.
- Process termination only reaches non-critical processes you own.

Guards run **before** you are asked to approve anything. Something that will be
refused regardless never produces a dialog — otherwise you'd be trained to click
Approve reflexively, which is how approval prompts stop working.

**3. Tiered consent.** Every tool declares a risk level, and you choose what
each tier does (Settings → Security):

| Tier | Examples | Default |
|---|---|---|
| Safe | read stats, list apps, screenshot | allow |
| Moderate | open apps, type, click, volume | allow |
| High | delete, force-quit, shell, power | **ask first** |

Prompts appear in the dashboard with the exact arguments, and auto-deny after
45 seconds. `Ctrl+Alt+Esc` denies everything pending instantly.

**4. Everything is logged.** Every call — allowed, denied, or blocked — is
appended to `data/audit.jsonl` and shown under Activity.

**5. The key stays local.** `.env` is gitignored; the server binds to
`127.0.0.1` only and is not reachable from your network.

### What it deliberately cannot do

Install software, change system-wide settings, modify other users' files, edit
`HKLM`, or disable security tooling. If you need one of those, it will tell you
to do it yourself.

---

## What it can do

68 tools across:

**Apps** — launch (single or several at once), close, force-quit, list installed
and running, fuzzy name matching (`"my nvidia app"` → `NVIDIA App`).

**Windows** — focus, minimize/maximize/close, snap left/right/fullscreen, tile
2–4 windows, show desktop.

**System** — volume, mute, media keys, brightness, lock, sleep/shutdown/restart,
CPU/RAM/GPU/disk/battery stats, network status, toast notifications, clipboard.

**Other apps' settings** — Jarvis can reach inside apps that are already open
and change things, without any app-specific integration. `inspect_app` reads a
window's controls by name, `click_element` clicks one, `select_option` handles
dropdowns, and `read_app_value` pulls out live readings.

```
You: "Set my cooling to silent"        →  NZXT CAM switches profile
You: "What's my liquid temperature?"   →  reads it straight out of CAM
```

This works on Electron apps (NZXT CAM, the NVIDIA App, Discord, Spotify) because
Jarvis nudges Chromium into publishing its accessibility tree first — without
that they look completely empty to Windows. Elements are found *semantically* by
name, then clicked at the coordinates UI Automation reports, since these apps
rarely implement invokable actions.

**Websites work the same way.** Pass a browser window to `inspect_app` and you
get the page's own buttons, links and text boxes by name — so Jarvis can drive a
site rather than guess at pixels:

```
You: "Open Gmail, compose a message to alex@example.com about lunch"
     →  clicks Compose, fills To / Subject / Message Body
     →  shows you the draft and waits — it never sends without you saying so
```

Only elements actually on screen are considered. Browsers keep a live
accessibility tree for *every background tab*, so without that filter a scan
mixes several pages together and a click can land on an invisible one.

Once a sequence works, save it as a routine and it becomes one deterministic
command. Two are set up already: `silent cooling` and `performance cooling`.

**Input** — type text, key combinations, mouse click/move/scroll. Multi-monitor
aware: coordinates span the whole virtual desktop, not just the primary screen.

**Files** — search, list, read, write, move, delete to Recycle Bin, open with
default app. Shortcuts like `desktop` and `downloads` work.

**Screen** — Jarvis can see all three monitors. `list_monitors` reports what is
open on each, `see_screen` looks at one (or all at once) and answers questions
about it, and `find_on_screen` locates something visually when nothing else can.

When you say "my monitor" and it matters which, Jarvis asks using what's
actually on them rather than making you recite numbers:

> *"Which one, Sir? The main one with Firefox and Gmail, the portrait one with
> Discord and NZXT CAM, or the one with Visual Studio Code?"*

**Web** — real internet search. `search_web` reads DuckDuckGo results directly,
`search_news` finds recent articles, `fetch_url` reads any page in full, and
`research` asks Perplexity Sonar a question and gets a sourced answer. Jarvis is
instructed to search rather than guess, so anything current — scores, prices,
weather, release dates — comes from the live web, not stale training data. It
can also just open a search in your browser if you'd rather look yourself.

**Assistant** — persistent memory (`"remember my main monitor is the left one"`),
timers and reminders, and **routines**: named multi-step shortcuts.

```
You: "Open Epic and NVIDIA, set volume to 55, and save that as my gaming setup"
You: "Run my gaming setup"        # from then on
```

Routines are editable in the dashboard and run from a tile.

---

## Configuration

Everything lives in `config/settings.json`, editable by hand or from Settings.
Worth knowing:

| Setting | Default | Notes |
|---|---|---|
| `models.agent` | `anthropic/claude-sonnet-5` | Any OpenRouter tool-calling model |
| `models.vision` | `anthropic/claude-sonnet-5` | Used for screen questions |
| `models.fallback` | `anthropic/claude-opus-5` | Takes over when the agent stalls |
| `models.escalate_on_stuck` | `true` | Escalate instead of giving up |
| `models.search` | `perplexity/sonar` | Only the `research` tool; plain search is free |
| `voice.wake_enabled` | `true` | Always-on "Hey Jarvis" |
| `voice.wake_threshold` | `0.5` | Raise if it false-triggers, lower if it misses |
| `voice.conversation_mode` | `true` | Keep listening after a reply |
| `voice.conversation_timeout_s` | `10` | Silence before the conversation closes |
| `voice.stt_model` | `base.en` | `small.en` is more accurate, still realtime |
| `voice.tts_voice` | `en-GB-RyanNeural` | Full list in the Settings dropdown |
| `voice.silence_threshold` | `0.012` | Lower = picks up quieter speech |
| `security.high` | `confirm` | Set to `deny` to disable high-risk tools entirely |
| `app_aliases` | see file | Teach it your nicknames for apps |

Changes apply immediately except the hotkey, which needs a restart.

---

## Verifying it works

```powershell
uv run python scripts\smoke_imports.py       # native deps load, mic detected
uv run python scripts\check_tools.py         # all 68 tools produce valid schemas
uv run python scripts\check_speech_text.py   # markdown never reaches the speech engine
uv run python scripts\check_conversation.py  # multi-turn conversation, context, farewells
uv run python scripts\check_escalation.py    # hands a stalled turn to the fallback model
uv run python scripts\bench_vision.py        # compares vision models on your real screen
uv run python scripts\check_agent.py         # real model call driving real tools
uv run python scripts\check_voice.py         # STT accuracy + voice->tool routing
uv run python scripts\check_wakeword.py      # wake model fires on the phrase only
uv run python scripts\check_search.py        # proves it searches instead of guessing
uv run python scripts\check_mic.py           # mic pinning, and refusal to fall back
uv run python scripts\check_uia.py           # reads inside NZXT CAM (--click to click too)
uv run python scripts\check_cooling.py       # changes the cooling mode and restores it
uv run python scripts\check_vision.py        # three monitors + reading web pages
uv run python scripts\check_gmail.py         # fills a Gmail draft (never sends)
uv run python scripts\check_screens_agent.py # asks which monitor when ambiguous

# these need Jarvis already running
uv run python scripts\check_integration.py   # 17 checks: permissions, guards, launching
uv run python scripts\check_wake_handoff.py  # 8 checks: mic handoff + CPU cost
uv run python scripts\check_wake_live.py     # plays "Hey Jarvis" aloud, checks the mic hears it
uv run python scripts\check_background.py    # runs with the window closed; refuses duplicates
```

`check_integration.py` is the interesting one — it exercises the permission
round-trip (approve *and* deny), proves the hard guards block without
prompting, launches and closes a real app, and confirms the audit log recorded
all of it.

`check_wake_live.py` is the only test that covers the real acoustic path:
speaker → room → microphone → detector. If it fails, that is usually a tuning
problem rather than a code fault.

---

## Troubleshooting

**"running as Administrator and will not start"** — working as intended. Launch
from a normal terminal or the desktop shortcut. `--allow-elevated` overrides it,
but then you've given up the main safety property.

**Hotkey does nothing** — Windows blocks low-level hooks from reaching an
unelevated process while an *elevated* window has focus. Use the tray icon, or
click the orb.

**Mic hears nothing** — check the input device under Settings, and lower
`voice.silence_threshold` if you speak quietly.

**"Hey Jarvis" doesn't trigger** — run `check_wake_live.py` to see whether the
mic hears it at all. Lower `voice.wake_threshold` toward 0.3, or check that the
*Listening* chip in the sidebar is lit. If another app has grabbed the mic
exclusively, Jarvis cannot listen; the log will say so.

**It triggers on its own** — raise `voice.wake_threshold` toward 0.7. If it
retriggers right after speaking, raise `voice.wake_rearm_delay_s`.

**Closing the window doesn't quit it** — that's deliberate; it hides to the
tray so listening continues. Use Quit in the tray menu.

**"That app exposed no readable controls"** — some apps only publish their
accessibility tree once focused, and a few draw everything as a single image.
Bring the window to the front and try again; if it still reports nothing, ask
Jarvis to look at the screen instead and it will fall back to vision.

**Jarvis can't see an app at all** — apps running as Administrator are
invisible to an unelevated process. That is the privilege boundary working, not
a bug, and Jarvis will not try to get around it.

**A saved app routine stopped working** — app updates move things. Ask Jarvis
to do it manually once, then save the routine again.

**Speech is slow the first time** — the Whisper model is downloading. Later
launches load it in under a second.

**An app won't open** — click *Re-scan* under Applications after installing
something new, or add a nickname to `app_aliases`.
