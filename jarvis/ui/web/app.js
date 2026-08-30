/* Jarvis dashboard. Vanilla JS, no build step, no external requests. */

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  settings: null,
  tools: [],
  routines: [],
  apps: [],
  toolChips: new Map(),   // tool name -> chip element awaiting completion
  levelDecay: 0,
  face: null,
  inConversation: false,
};

/* ── API ─────────────────────────────────────────────────────── */

async function api(path, method = "GET", body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  return res.json();
}

const post = (path, body) => api(path, "POST", body);

/* ── Navigation ──────────────────────────────────────────────── */

$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b === btn));
    const view = btn.dataset.view;
    $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === view));
    if (view === "activity") { loadAudit(); $("#activity-badge").hidden = true; }
    if (view === "apps" && !state.apps.length) loadApps("");
  });
});

/* ── Conversation stream ─────────────────────────────────────── */

function scrollStream() {
  const stream = $("#stream");
  stream.scrollTop = stream.scrollHeight;
}

// Replies are meant to be plain prose, but when a stray **bold** or `code`
// slips through, render it rather than showing the raw characters. Escaping
// happens first, so this never injects markup from model output.
function renderInline(text) {
  return escapeHtml(text)
    .replace(/\*\*\*(\S(?:.*?\S)?)\*\*\*/g, "<strong><em>$1</em></strong>")
    .replace(/\*\*(\S(?:.*?\S)?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<![\w*])\*(\S(?:.*?\S)?)\*(?![\w*])/g, "<em>$1</em>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
}

function addMessage(role, text) {
  if (!text) return;
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = renderInline(text);
  $("#stream").appendChild(el);
  scrollStream();
  return el;
}

function addToolChip(tool, summary, risk) {
  const chip = document.createElement("div");
  chip.className = "tool-chip pending";
  chip.innerHTML = `<span class="tick">◌</span>
    <span class="risk ${risk}">${risk}</span>
    <span class="label"></span>`;
  chip.querySelector(".label").textContent = summary || tool;
  $("#stream").appendChild(chip);
  scrollStream();
  state.toolChips.set(tool, chip);
  return chip;
}

function finishToolChip(tool, ok, error) {
  const chip = state.toolChips.get(tool);
  if (!chip) return;
  state.toolChips.delete(tool);
  chip.classList.remove("pending");
  chip.classList.add(ok ? "ok" : "fail");
  chip.querySelector(".tick").textContent = ok ? "✓" : "✕";
  if (!ok && error) {
    chip.querySelector(".label").textContent += ` — ${error}`;
  }
  scrollStream();
}

/* ── State + orb ─────────────────────────────────────────────── */

function setState(s) {
  $("#state-pill").dataset.state = s;
  $("#state-label").textContent = s;
  $("#orb").dataset.state = s;
  if (state.face) state.face.setState(s);

  const hints = {
    idle: state.inConversation
      ? "Still listening — just carry on"
      : (state.wake?.enabled && state.wake?.available
          ? `Say <kbd>Hey Jarvis</kbd>, press <kbd>${hotkeyLabel()}</kbd>, or click the face`
          : `Press <kbd>${hotkeyLabel()}</kbd> or click the face to talk`),
    listening: state.inConversation
      ? "Go ahead…" : "Listening… stop talking when you're done",
    transcribing: "Working out what you said…",
    thinking: "Thinking…",
    speaking: "Speaking — click the face to interrupt",
  };
  $("#stage-hint").innerHTML = hints[s] || hints.idle;
}

function setConversation(active) {
  state.inConversation = active;
  $("#convo-pill").classList.toggle("on", active);
  setState($("#orb").dataset.state || "idle");
}

function hotkeyLabel() {
  const raw = state.settings?.ui?.hotkey || "<ctrl>+<alt>+j";
  return raw.replace(/[<>]/g, "").split("+")
            .map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join("+");
}

function showLevel(level) {
  if (state.face) state.face.setLevel(level);
  const halo = $("#orb-halo");
  if (halo) halo.style.transform = `scale(${1 + Math.min(level * 5, 0.35)})`;
  state.levelDecay = Date.now();
}

setInterval(() => {
  if (Date.now() - state.levelDecay > 220) {
    const halo = $("#orb-halo");
    if (halo) halo.style.transform = "scale(1)";
    if (state.face) state.face.setLevel(0);
  }
}, 250);

/* ── Permission prompts ──────────────────────────────────────── */

function showPermission(evt) {
  const card = document.createElement("div");
  card.className = `perm-card ${evt.risk}`;
  card.dataset.id = evt.id;

  const args = evt.args && Object.keys(evt.args).length
    ? `<div class="args">${escapeHtml(JSON.stringify(evt.args, null, 2))}</div>` : "";

  card.innerHTML = `
    <span class="risk-tag">${evt.risk} risk — approval needed</span>
    <h3>${escapeHtml(evt.summary)}</h3>
    <div class="tool-name">${escapeHtml(evt.tool)}</div>
    ${args}
    <div class="perm-actions">
      <button class="deny">Deny</button>
      <button class="approve">Approve</button>
    </div>
    <div class="perm-timer"><i style="width:100%"></i></div>`;

  const resolve = (approved) => {
    clearInterval(tick);
    card.remove();
    post("/api/permission", { id: evt.id, approved });
  };
  card.querySelector(".approve").addEventListener("click", () => resolve(true));
  card.querySelector(".deny").addEventListener("click", () => resolve(false));

  const total = evt.timeout || 45;
  let left = total;
  const bar = card.querySelector(".perm-timer i");
  const tick = setInterval(() => {
    left -= 1;
    bar.style.width = `${Math.max(0, (left / total) * 100)}%`;
    if (left <= 0) { clearInterval(tick); card.remove(); }
  }, 1000);

  $("#perm-layer").appendChild(card);
  card.querySelector(".approve").focus();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ── Wake word ───────────────────────────────────────────────── */

function renderWake(wake) {
  if (!wake) return;
  state.wake = wake;
  const chip = $("#wake-chip");
  chip.classList.toggle("off", !wake.enabled);
  chip.classList.toggle("unavailable", wake.enabled && !wake.available);

  if (!wake.available && wake.enabled) {
    $("#wake-title").textContent = "Wake word unavailable";
    $("#wake-sub").textContent = "check the log";
  } else if (wake.enabled) {
    $("#wake-title").textContent = "Listening";
    $("#wake-sub").textContent = "say “Hey Jarvis”";
  } else {
    $("#wake-title").textContent = "Not listening";
    $("#wake-sub").textContent = "click to enable";
  }
}

$("#wake-chip").addEventListener("click", async () => {
  const next = !(state.wake?.enabled ?? true);
  const res = await post("/api/wake", { enabled: next });
  renderWake({ enabled: next, listening: res.listening, available: res.available });
  toast(next ? "Listening for “Hey Jarvis”." : "Wake word off.");
});

/* ── Toasts ──────────────────────────────────────────────────── */

function toast(text, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  $("#toast-layer").appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

/* ── WebSocket ───────────────────────────────────────────────── */

let socket = null;

function connect() {
  socket = new WebSocket(`ws://${location.host}/ws`);

  socket.onmessage = (raw) => {
    const evt = JSON.parse(raw.data);
    if (evt.kind === "replay") { evt.events.forEach(handle); return; }
    handle(evt);
  };
  socket.onclose = () => setTimeout(connect, 1500);
  socket.onerror = () => socket.close();
}

function handle(evt) {
  switch (evt.kind) {
    case "state":        setState(evt.state); break;
    case "audio_level":  showLevel(evt.level); break;
    case "speak_level":  if (state.face) state.face.setSpeakLevel(evt.level); break;
    case "conversation": setConversation(!!evt.active); break;
    case "transcript":   break;  // the agent echoes it as a user message
    case "message":      addMessage(evt.role, evt.text); break;
    case "notice":       addMessage("notice", evt.text); break;
    case "error":        addMessage("error", evt.text); break;
    case "tool_start":   addToolChip(evt.tool, evt.summary, evt.risk); break;
    case "tool_end":     finishToolChip(evt.tool, evt.ok, evt.error); flagActivity(); break;
    case "permission_request":  showPermission(evt); break;
    case "permission_resolved": {
      const card = document.querySelector(`.perm-card[data-id="${evt.id}"]`);
      if (card) card.remove();
      break;
    }
    case "stats":        renderMeters(evt); break;
    case "wake_state":   renderWake({ ...state.wake, ...evt }); break;
    case "escalated": {
      const short = String(evt.model).split("/").pop();
      addMessage("notice", `Switching to ${short} — ${evt.reason}`);
      toast(`Escalated to ${short}`);
      break;
    }
    case "wake_detected": {
      const chip = $("#wake-chip");
      chip.classList.remove("fired");
      void chip.offsetWidth;          // restart the animation
      chip.classList.add("fired");
      break;
    }
    case "timer_fired":  toast(`⏱ ${evt.label}`); break;
    case "routines_changed": loadState(); break;
    case "history_cleared":  $("#stream").innerHTML = ""; break;
  }
}

function flagActivity() {
  const activeView = $(".view.active")?.dataset.view;
  if (activeView !== "activity") {
    const badge = $("#activity-badge");
    badge.hidden = false;
    badge.textContent = String((parseInt(badge.textContent, 10) || 0) + 1);
  } else {
    loadAudit();
  }
}

/* ── Rendering: meters ───────────────────────────────────────── */

function meter(name, value, percent) {
  const cls = percent > 88 ? "hot" : percent > 68 ? "warm" : "";
  return `<div class="meter">
      <div class="meter-top"><span class="name">${name}</span><span class="val">${value}</span></div>
      <div class="meter-bar"><i class="meter-fill ${cls}" style="width:${Math.min(percent, 100)}%"></i></div>
    </div>`;
}

function renderMeters(s) {
  const rows = [
    meter("CPU", `${s.cpu_percent}%`, s.cpu_percent),
    meter("Memory", `${s.memory.used_gb} / ${s.memory.total_gb} GB`, s.memory.percent),
    meter("Disk C:", `${s.disk_c.used_gb} / ${s.disk_c.total_gb} GB`, s.disk_c.percent),
  ];
  (s.gpus || []).forEach((g) => {
    rows.push(meter(
      g.name.replace(/NVIDIA GeForce /, ""),
      `${g.load_percent}% · ${g.temperature_c}°C`,
      g.load_percent ?? 0));
  });
  if (s.battery) {
    rows.push(meter(
      `Battery${s.battery.plugged_in ? " ⚡" : ""}`,
      `${s.battery.percent}%`, s.battery.percent));
  }
  rows.push(`<div class="meter-top" style="margin-top:12px">
      <span class="name">Uptime</span><span class="val">${s.uptime_hours}h</span></div>`);
  $("#meters").innerHTML = rows.join("");
}

/* ── Rendering: apps, routines, tools, audit ─────────────────── */

async function loadApps(query) {
  const data = await api(`/api/apps?q=${encodeURIComponent(query)}&limit=60`);
  state.apps = data.apps || [];
  $("#app-grid").innerHTML = state.apps.length
    ? state.apps.map((a) => `
        <button class="app-card" data-app="${escapeHtml(a.name)}">
          ${escapeHtml(a.name)}<span class="kind">${a.kind}</span>
        </button>`).join("")
    : `<p class="empty">Nothing matched.</p>`;

  $$("#app-grid .app-card").forEach((card) => {
    card.addEventListener("click", async () => {
      const name = card.dataset.app;
      toast(`Opening ${name}…`);
      await post("/api/tool", { name: "launch_app", args: { name } });
    });
  });
}

function renderRoutines() {
  const grid = $("#routine-grid");
  if (!state.routines.length) {
    grid.innerHTML = `<p class="empty">No routines yet. Say “save that as a routine” after Jarvis does something useful.</p>`;
    return;
  }
  grid.innerHTML = state.routines.map((r) => `
    <div class="routine-card">
      <h4>${escapeHtml(r.name)}</h4>
      <p>${escapeHtml(r.description || "No description.")}</p>
      <div class="steps">${r.steps.map((s) => `→ ${escapeHtml(s.tool)}`).join("<br>")}</div>
      <button data-routine="${escapeHtml(r.name)}">Run routine</button>
    </div>`).join("");

  $$("#routine-grid button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = btn.dataset.routine;
      btn.textContent = "Running…";
      await post("/api/tool", { name: "run_routine", args: { name } });
      btn.textContent = "Run routine";
    });
  });
}

function renderTools() {
  $("#tool-list").innerHTML = state.tools.map((t) => `
    <div class="tool-row">
      <span class="name">${escapeHtml(t.name)}</span>
      <span class="desc">${escapeHtml(t.description)}</span>
      <span class="pill ${t.effective_action}">${t.effective_action}</span>
    </div>`).join("");
}

async function loadAudit() {
  const data = await api("/api/audit?limit=150");
  const rows = (data.entries || []).slice().reverse();
  $("#audit-log").innerHTML = rows.length ? rows.map((e) => {
    const time = new Date(e.ts * 1000).toLocaleTimeString();
    return `<div class="log-row ${e.status}">
        <span class="time">${time}</span>
        <span class="tool">${escapeHtml(e.tool)}</span>
        <span class="what">${escapeHtml(e.detail || "")}</span>
        <span class="status">${e.status}</span>
      </div>`;
  }).join("") : `<p class="empty">Nothing yet.</p>`;
}

function renderQuickLaunch() {
  const favourites = Object.values(state.settings?.app_aliases || {});
  const unique = [...new Set(favourites)].slice(0, 8);
  $("#quick").innerHTML = unique.map((name) =>
    `<button class="tile" data-app="${escapeHtml(name)}">${escapeHtml(name)}</button>`).join("");

  $$("#quick .tile").forEach((tile) => {
    tile.addEventListener("click", () => {
      toast(`Opening ${tile.dataset.app}…`);
      post("/api/tool", { name: "launch_app", args: { name: tile.dataset.app } });
    });
  });
}

async function loadRunning() {
  // Uses the quiet endpoint, not /api/tool — background polling must not show
  // up as assistant activity.
  const res = await api("/api/running");
  const seen = new Set();
  const items = (res.windows || [])
    .filter((w) => { const p = w.process || w.title; if (seen.has(p)) return false; seen.add(p); return true; })
    .slice(0, 9);
  $("#running").innerHTML = items.length
    ? items.map((w) => `<div class="item" title="${escapeHtml(w.title)}">${escapeHtml(w.process || w.title)}</div>`).join("")
    : `<p class="empty">Nothing open.</p>`;
}

/* ── Settings ────────────────────────────────────────────────── */

function renderSettings() {
  const s = state.settings;
  if (!s) return;

  const actionSelect = (id, value) => `
    <select data-setting="${id}">
      ${["allow", "confirm", "deny"].map((o) =>
        `<option value="${o}" ${o === value ? "selected" : ""}>${o}</option>`).join("")}
    </select>`;

  $("#settings").innerHTML = `
    <div class="set-group">
      <h4>SECURITY</h4>
      <div class="set-row">
        <label>Safe actions
          <small>Reading state, listing apps, taking a screenshot.</small></label>
        ${actionSelect("security.safe", s.security.safe)}
      </div>
      <div class="set-row">
        <label>Moderate actions
          <small>Opening apps, typing, clicking, changing volume.</small></label>
        ${actionSelect("security.moderate", s.security.moderate)}
      </div>
      <div class="set-row">
        <label>High-risk actions
          <small>Deleting files, force-quitting, shell commands, power.</small></label>
        ${actionSelect("security.high", s.security.high)}
      </div>
      <div class="set-row">
        <label>Approval timeout
          <small>Seconds before an unanswered prompt is denied.</small></label>
        <input type="number" min="5" max="300" data-setting="security.confirm_timeout_s"
               value="${s.security.confirm_timeout_s}">
      </div>
    </div>

    <div class="set-group">
      <h4>VOICE</h4>
      <div class="set-row">
        <label>Speech model
          <small>Larger is more accurate but slower to transcribe.</small></label>
        <select data-setting="voice.stt_model">
          ${["tiny.en", "base.en", "small.en", "medium.en"].map((m) =>
            `<option ${m === s.voice.stt_model ? "selected" : ""}>${m}</option>`).join("")}
        </select>
      </div>
      <div class="set-row">
        <label>Voice engine
          <small>Neural sounds better; offline works with no connection.</small></label>
        <select data-setting="voice.tts_engine">
          <option value="edge" ${s.voice.tts_engine === "edge" ? "selected" : ""}>Neural (online)</option>
          <option value="sapi" ${s.voice.tts_engine === "sapi" ? "selected" : ""}>Offline (SAPI)</option>
        </select>
      </div>
      <div class="set-row">
        <label>Voice</label>
        <select data-setting="voice.tts_voice" id="voice-select">
          <option>${escapeHtml(s.voice.tts_voice)}</option>
        </select>
      </div>
      <div class="set-row">
        <label>Speak replies aloud</label>
        <select data-setting="voice.tts_enabled">
          <option value="true"  ${s.voice.tts_enabled ? "selected" : ""}>on</option>
          <option value="false" ${!s.voice.tts_enabled ? "selected" : ""}>off</option>
        </select>
      </div>
      <div class="set-row">
        <label>Microphone
          <small>Matched by name. Jarvis will not fall back to another mic.</small></label>
        <select data-setting="voice.input_device_name" id="mic-select">
          <option>${escapeHtml(s.voice.input_device_name)}</option>
        </select>
      </div>
      <div class="set-row">
        <label>Test the microphone
          <small id="mic-test-result">Checks the selected mic is actually delivering audio.</small></label>
        <button class="ghost" id="btn-mic-test">Test mic</button>
      </div>
      <div class="set-row">
        <label>Always-on “Hey Jarvis”
          <small>Detected locally. Nothing is recorded until it fires.</small></label>
        <select data-setting="voice.wake_enabled">
          <option value="true"  ${s.voice.wake_enabled ? "selected" : ""}>on</option>
          <option value="false" ${!s.voice.wake_enabled ? "selected" : ""}>off</option>
        </select>
      </div>
      <div class="set-row">
        <label>Conversation mode
          <small>Keep listening after a reply, so follow-ups don't need
            "Hey Jarvis" again.</small></label>
        <select data-setting="voice.conversation_mode">
          <option value="true"  ${s.voice.conversation_mode ? "selected" : ""}>on</option>
          <option value="false" ${!s.voice.conversation_mode ? "selected" : ""}>off</option>
        </select>
      </div>
      <div class="set-row">
        <label>Conversation timeout
          <small>Seconds of silence before the conversation closes.</small></label>
        <input type="number" step="1" min="3" max="60"
               data-setting="voice.conversation_timeout_s"
               value="${s.voice.conversation_timeout_s}">
      </div>
      <div class="set-row">
        <label>Wake sensitivity
          <small>Lower triggers more easily; raise it if noise sets it off.</small></label>
        <input type="number" step="0.05" min="0.1" max="0.95"
               data-setting="voice.wake_threshold" value="${s.voice.wake_threshold}">
      </div>
      <div class="set-row">
        <label>Mic sensitivity
          <small>Lower detects quieter speech but may trigger on noise.</small></label>
        <input type="number" step="0.002" min="0.002" max="0.1"
               data-setting="voice.silence_threshold" value="${s.voice.silence_threshold}">
      </div>
    </div>

    <div class="set-group">
      <h4>MODEL</h4>
      <div class="set-row">
        <label>Assistant model
          <small>Handles conversation and decides which tools to use.</small></label>
        <input type="text" data-setting="models.agent" value="${escapeHtml(s.models.agent)}">
      </div>
      <div class="set-row">
        <label>Vision model
          <small>Used when Jarvis looks at your screens.</small></label>
        <input type="text" data-setting="models.vision" value="${escapeHtml(s.models.vision)}">
      </div>
      <div class="set-row">
        <label>Fallback model
          <small>Takes over only when the assistant stalls or the primary
            model is unavailable.</small></label>
        <input type="text" data-setting="models.fallback" value="${escapeHtml(s.models.fallback)}">
      </div>
      <div class="set-row">
        <label>Escalate when stuck
          <small>Hand a stalled task to the fallback instead of giving up.</small></label>
        <select data-setting="models.escalate_on_stuck">
          <option value="true"  ${s.models.escalate_on_stuck ? "selected" : ""}>on</option>
          <option value="false" ${!s.models.escalate_on_stuck ? "selected" : ""}>off</option>
        </select>
      </div>
    </div>

    <div class="set-group">
      <h4>ASSISTANT</h4>
      <div class="set-row">
        <label>What Jarvis calls you</label>
        <input type="text" data-setting="user_name" value="${escapeHtml(s.user_name)}">
      </div>
      <div class="set-row">
        <label>Activation hotkey
          <small>pynput format, e.g. &lt;ctrl&gt;+&lt;alt&gt;+j. Restart to apply.</small></label>
        <input type="text" data-setting="ui.hotkey" value="${escapeHtml(s.ui.hotkey)}">
      </div>
    </div>`;

  $$("[data-setting]").forEach((input) => {
    input.addEventListener("change", async () => {
      const path = input.dataset.setting.split(".");
      let value = input.value;
      if (value === "true") value = true;
      else if (value === "false") value = false;
      else if (input.type === "number") value = parseFloat(value);

      const patch = {};
      let node = patch;
      path.forEach((key, i) => {
        if (i === path.length - 1) node[key] = value;
        else { node[key] = {}; node = node[key]; }
      });
      const res = await post("/api/settings", { patch });
      if (res.ok) { state.settings = res.settings; toast("Saved."); }
      else toast(res.error || "Could not save.", "error");
    });
  });

  loadVoices();
  loadMics();

  $("#btn-mic-test").addEventListener("click", async (e) => {
    const out = $("#mic-test-result");
    e.target.disabled = true;
    out.textContent = "Listening for 2.5 seconds — say something…";
    const r = await post("/api/mic-test");
    e.target.disabled = false;

    if (!r.ok) {
      out.innerHTML = `<span style="color:var(--high)">${escapeHtml(r.error)}</span>`;
      return;
    }
    if (r.silent) {
      out.innerHTML = `<span style="color:var(--high)">${escapeHtml(r.device)} is connected but
        producing silence (peak ${r.peak}). If it's a wireless mic, check the transmitter is
        powered on and paired.</span>`;
    } else if (r.speech_detected) {
      out.innerHTML = `<span style="color:var(--safe)">Working — heard you clearly
        (peak ${r.peak}) on ${escapeHtml(r.device)}.</span>`;
    } else {
      out.innerHTML = `<span style="color:var(--moderate)">${escapeHtml(r.device)} is live but quiet
        (peak ${r.peak}, below the ${r.threshold} threshold). Speak up, or lower mic sensitivity.</span>`;
    }
  });
}

async function loadMics() {
  const select = $("#mic-select");
  if (!select) return;
  const data = await api("/api/devices");
  const current = state.settings.voice.input_device_name;
  select.innerHTML = (data.devices || []).map((d) =>
    `<option value="${escapeHtml(d.name)}" ${d.selected ? "selected" : ""}>
       ${escapeHtml(d.name)}</option>`).join("");

  if (data.error) {
    $("#mic-test-result").innerHTML =
      `<span style="color:var(--high)">${escapeHtml(data.error)}</span>`;
  } else if (data.active) {
    $("#mic-test-result").textContent = `Using ${data.active}`;
  }
}

async function loadVoices() {
  const select = $("#voice-select");
  if (!select) return;
  const data = await api("/api/voices");
  if (!data.voices?.length) return;
  const current = state.settings.voice.tts_voice;
  select.innerHTML = data.voices.map((v) =>
    `<option value="${v.name}" ${v.name === current ? "selected" : ""}>${v.name} (${v.gender})</option>`
  ).join("");
}

/* ── Boot ────────────────────────────────────────────────────── */

async function loadState() {
  const data = await api("/api/state");
  state.settings = data.settings;
  state.tools = data.tools;
  state.routines = data.routines;

  // Privilege state first: it is the one thing that must always render, even
  // if something further down throws.
  if (data.elevated) {
    const chip = $("#secure-chip");
    chip.classList.add("warn");
    chip.querySelector("strong").textContent = "Administrator";
    chip.querySelector("small").textContent = "elevated — not recommended";
  }

  // Note: setState() rewrites #stage-hint, so nothing may hold a reference to
  // elements inside it across this call.
  renderWake(data.wake);
  setState(data.state);
  renderMeters(data.stats);
  renderRoutines();
  renderTools();
  renderQuickLaunch();
  renderSettings();
  $("#tokens").textContent = (data.tokens_used || 0).toLocaleString();

  // Restore the conversation after a reload.
  (data.history || []).forEach((m) => {
    if (m.role === "user") addMessage("user", m.content);
    else if (m.role === "assistant" && m.content) addMessage("assistant", m.content);
  });
}

async function loadCredit() {
  const data = await api("/api/key");
  $("#credit").textContent = data.ok && data.remaining != null
    ? `$${data.remaining.toFixed(2)}` : "—";
}

/* Events */

$("#orb").addEventListener("click", () => post("/api/activate"));
$("#btn-mic").addEventListener("click", () => post("/api/activate"));
$("#btn-panic").addEventListener("click", () => post("/api/cancel"));
$("#btn-reset").addEventListener("click", async () => {
  await post("/api/reset");
  $("#stream").innerHTML = "";
});
$("#btn-reindex").addEventListener("click", async () => {
  toast("Re-scanning installed apps…");
  const res = await post("/api/tool", { name: "refresh_app_index", args: {} });
  toast(`Indexed ${res.indexed} apps.`);
  loadApps($("#app-search").value);
});

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#composer-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  post("/api/message", { text });
});

let searchTimer;
$("#app-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadApps(e.target.value), 180);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") post("/api/cancel");
  if (e.key === " " && e.ctrlKey) { e.preventDefault(); post("/api/activate"); }
});

state.face = new JarvisFace($("#face"));

connect();
loadState();
loadCredit();
loadRunning();
setInterval(loadRunning, 12000);
setInterval(loadCredit, 120000);
