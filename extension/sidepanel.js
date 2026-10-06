// Side panel: config + controls, renders the live transcript and the structured
// notes pushed from the backend over the WebSocket (relayed via the offscreen
// doc using chrome.runtime messaging).

const $ = (id) => document.getElementById(id);
const fields = ["backendUrl", "projectHint", "apiKey", "title"];
let capturing = false;
let finals = []; // finalized transcript segments
let calendarEvents = [];
let calendarRequest = 0;
let sessionStarted = null;
let timerInterval = null;
let noteFilter = "all";
// Keep the recorder visible while changing the working view.
const viewButtons = [...document.querySelectorAll("[data-view]")];
function selectView(id, focus = false) {
  viewButtons.forEach((button) => {
    const selected = button.dataset.view === id;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    $(button.dataset.view).hidden = !selected;
    if (selected && focus) button.focus();
  });
}
viewButtons.forEach((button, index) => {
  button.addEventListener("click", () => selectView(button.dataset.view));
  button.addEventListener("keydown", (event) => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % viewButtons.length;
    if (event.key === "ArrowLeft") next = (index + viewButtons.length - 1) % viewButtons.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = viewButtons.length - 1;
    if (next == null) return;
    event.preventDefault();
    selectView(viewButtons[next].dataset.view, true);
  });
});
$("openSettings").addEventListener("click", () => {
  selectView("connectors");
  $("connectionSettings").open = true;
  $("backendUrl").focus();
});
// An abstract session activity signal, not an audio amplitude measurement.
const signal = document.querySelector(".signal-bars");
for (let index = 0; index < 45; index++) {
  const bar = document.createElement("i");
  const envelope = Math.sin((index / 44) * Math.PI);
  bar.style.setProperty("--bar-height", `${5 + envelope * (10 + ((index * 7) % 21))}px`);
  bar.style.setProperty("--bar-delay", `${-(index % 9) * 0.16}s`);
  signal.appendChild(bar);
}
function updateTimer() {
  if (!sessionStarted) return;
  const seconds = Math.floor((Date.now() - sessionStarted) / 1000);
  $("timer").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
function finishCapture() {
  capturing = false;
  clearInterval(timerInterval);
  updateTimer();
  $("toggle").disabled = false;
  $("toggle").textContent = "Start capturing";
  $("toggle").classList.remove("stop");
  $("dot").classList.remove("live");
}
document.addEventListener("visibilitychange", () => {
  document.body.classList.toggle("page-hidden", document.hidden);
});
$("search").addEventListener("input", filterNotes);
document.querySelectorAll("[data-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    noteFilter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    filterNotes();
  });
});
$("copyTranscript").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(finals.join(" "));
    $("copyStatus").textContent = "Transcript copied.";
  } catch {
    $("copyStatus").textContent = "Could not copy. Select the transcript and copy it manually.";
  }
});
function filterNotes() {
  const query = $("search").value.trim().toLowerCase();
  let visible = 0;
  document.querySelectorAll(".note-group").forEach((group) => {
    const selected = noteFilter === "all" || group.dataset.group === noteFilter;
    group.hidden = !selected;
    group.querySelectorAll(".card").forEach((card) => {
      card.hidden = !selected || !card.textContent.toLowerCase().includes(query);
      if (!card.hidden) visible += 1;
    });
  });
  const total = document.querySelectorAll(".card").length;
  $("noteCount").textContent = `${total} note${total === 1 ? "" : "s"}`;
  $("navNoteCount").textContent = total;
  $("notesWelcome").hidden = total > 0 || Boolean(query) || noteFilter !== "all";
  $("noteGroups").hidden = total === 0;
  document.querySelectorAll(".note-group").forEach((group) => {
    if (!group.querySelector(".card:not([hidden])")) group.hidden = true;
  });
  $("resultSummary").hidden = !query && noteFilter === "all";
  $("resultSummary").textContent = visible ? `${visible} matching note${visible === 1 ? "" : "s"}` : "No matching notes. Try another filter or search.";
}

function resetCalendar() {
  calendarRequest += 1;
  calendarEvents = [];
  $("calendarEvent").replaceChildren(new Option("Use meeting details above", ""));
  $("calendarEvent").disabled = true;
  $("title").readOnly = false;
  $("calendarHelp").textContent = "Load meetings to use their title, attendees, and date.";
  $("loadMeetings").disabled = false;
  $("loadMeetings").textContent = "Load Google Calendar meetings";
}

["backendUrl", "apiKey"].forEach((id) => $(id).addEventListener("input", resetCalendar));
$("loadMeetings").addEventListener("click", async () => {
  resetCalendar();
  const request = calendarRequest;
  $("loadMeetings").disabled = true;
  $("loadMeetings").textContent = "Loading meetings…";
  $("calendarHelp").textContent = "Checking the next seven days…";
  try {
    const base = $("backendUrl").value.trim().replace(/\/+$/, "");
    const headers = $("apiKey").value.trim() ? { "X-API-Key": $("apiKey").value.trim() } : {};
    const response = await fetch(`${base}/api/integrations/calendar/events`, {
      headers, signal: AbortSignal.timeout(30000),
    });
    const data = await response.json().catch(() => {
      throw new Error("The backend returned an unreadable response");
    });
    if (request !== calendarRequest) return;
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not load meetings");
    calendarEvents = data.items || [];
    calendarEvents.forEach((event) => {
      const when = event.all_day ? `${event.starts_at} · All day` : new Date(event.starts_at).toLocaleString();
      $("calendarEvent").add(new Option(`${when} — ${event.title}`, event.id));
    });
    $("calendarEvent").disabled = calendarEvents.length === 0;
    $("calendarHelp").textContent = calendarEvents.length
      ? `Choose a meeting to fill its details.${data.next_page_token ? " Showing the first 100 meetings." : ""}`
      : "No meetings in the next seven days. You can enter a title above.";
  } catch (error) {
    if (request !== calendarRequest) return;
    $("calendarHelp").textContent = `${error.message}. Check the backend settings and try again.`;
  } finally {
    if (request === calendarRequest) {
      $("loadMeetings").disabled = false;
      $("loadMeetings").textContent = "Load Google Calendar meetings";
    }
  }
});
$("calendarEvent").addEventListener("change", () => {
  const event = calendarEvents.find((item) => item.id === $("calendarEvent").value);
  $("title").readOnly = Boolean(event);
  if (event) {
    $("title").value = event.title;
    $("calendarHelp").textContent = `${event.participants.length} attendees. Meeting details will be saved when capture starts.`;
  } else {
    $("calendarHelp").textContent = "Enter a meeting title above, or use the active tab title.";
  }
});

// ---- config persistence ----------------------------------------------------
chrome.storage.local.get(fields).then((saved) => {
  $("backendUrl").value = saved.backendUrl || "http://localhost:8000";
  $("projectHint").value = saved.projectHint || "";
  $("apiKey").value = saved.apiKey || "";
  $("title").value = saved.title || "";
});
fields.forEach((f) =>
  $(f).addEventListener("change", () => chrome.storage.local.set({ [f]: $(f).value }))
);

// ---- start / stop ----------------------------------------------------------
$("toggle").addEventListener("click", () => (capturing ? stop() : start()));

async function start() {
  setStatus("Starting…", "connecting");
  $("toggle").disabled = true;

  const config = {
    backendUrl: $("backendUrl").value.trim(),
    projectHint: $("projectHint").value.trim(),
    apiKey: $("apiKey").value.trim(),
    title: $("title").value.trim(),
    calendarEventId: $("calendarEvent").value || null,
  };
  const res = await chrome.runtime.sendMessage({ type: "START", config }).catch((e) => ({
    ok: false,
    error: String(e),
  }));
  $("toggle").disabled = false;
  if (!res || !res.ok) {
    setStatus("Error: " + (res && res.error ? res.error : "failed to start"), "error");
    return;
  }
  finals = [];
  renderTranscript("");
  renderNotes({});
  $("copyStatus").textContent = "";
  capturing = true;
  sessionStarted = Date.now();
  updateTimer();
  clearInterval(timerInterval);
  timerInterval = setInterval(updateTimer, 1000);
  $("toggle").textContent = "Stop & finalize";
  $("toggle").classList.add("stop");
  $("dot").classList.add("live");
  setStatus(res.sttEnabled ? "Listening…" : "Connected — but STT is not configured on the backend.", res.sttEnabled ? "listening" : "connected");
}

async function stop() {
  setStatus("Finalizing…", "finalizing");
  $("toggle").disabled = true;
  try {
    const response = await chrome.runtime.sendMessage({ type: "STOP" });
    if (!response?.ok) throw new Error(response?.error || "Could not stop capture. Try again.");
    finishCapture();
  } catch (error) {
    $("toggle").disabled = false;
    setStatus(error.message, "error");
  }
}

// ---- incoming messages from offscreen/backend ------------------------------
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type !== "WS_MESSAGE") return;
  const data = msg.data || {};
  switch (data.type) {
    case "transcript":
      if (data.is_final) {
        finals.push(data.text);
        renderTranscript("");
      } else {
        renderTranscript(data.text);
      }
      break;
    case "notes":
      renderNotes(data);
      break;
    case "capturing":
      setStatus("Listening…", "listening");
      break;
    case "reconnecting":
      setStatus(`Connection dropped — reconnecting (try ${data.attempt})…`, "connecting");
      break;
    case "reconnected":
      setStatus("Reconnected — listening…", "listening");
      break;
    case "done":
      finishCapture();
      setStatus("Meeting finalized", "complete");
      break;
    case "closed":
      if (capturing) { finishCapture(); setStatus("Connection closed. Start a new capture to reconnect.", "error"); }
      break;
    case "error":
      setStatus("Error: " + (data.detail || "unknown"), "error");
      break;
  }
});

// ---- rendering -------------------------------------------------------------
function setStatus(text, state) {
  $("statusText").textContent = text;
  if (state) {
    document.body.dataset.state = state;
    $("stageMode").textContent = {
      idle: "Standby", listening: "Live", connecting: "Connecting", connected: "Connected",
      finalizing: "Finishing", complete: "Saved", error: "Needs attention",
    }[state] || "Standby";
  }
}

function renderTranscript(interim) {
  const el = $("transcript");
  $("copyTranscript").disabled = finals.length === 0;
  const words = finals.join(" ").trim().split(/\s+/).filter(Boolean).length;
  $("wordCount").textContent = `${words} word${words === 1 ? "" : "s"}`;
  const follow = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  if (!finals.length && !interim) {
    el.innerHTML = '<span class="empty">Every next step starts with a conversation. Start capturing to see yours here.</span>';
    return;
  }
  el.textContent = finals.join(" ");
  if (interim) {
    const span = document.createElement("span");
    span.className = "interim";
    span.textContent = " " + interim;
    el.appendChild(span);
  }
  if (follow) el.scrollTop = el.scrollHeight;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

function renderNotes(data) {
  renderTasks(data.tasks || []);
  renderDecisions(data.decisions || []);
  renderRisksBlockers(data.risks || [], data.blockers || []);
  filterNotes();
}

function renderTasks(tasks) {
  $("taskCount").textContent = tasks.length ? `(${tasks.length})` : "";
  if (!tasks.length) {
    $("tasks").innerHTML = '<span class="empty">No tasks yet.</span>';
    return;
  }
  $("tasks").innerHTML = tasks
    .map((t) => {
      const due = t.due_date ? esc(t.due_date.slice(0, 10)) : "no due date";
      const conf = t.confidence != null ? Math.round(t.confidence * 100) + "%" : "";
      return `<div class="card" data-kind="task">
        <div class="title">${esc(t.title)}<span class="pill">${esc(t.status)}</span><span class="pill">${esc(t.priority)}</span></div>
        <div class="meta">owner: ${esc(t.owner || "unassigned")} · due: ${due} ${conf ? "· conf " + conf : ""}</div>
        ${t.source_quote ? `<details><summary>View source quote</summary><blockquote class="quote">“${esc(t.source_quote)}”</blockquote></details>` : ""}
      </div>`;
    })
    .join("");
}

function renderDecisions(decisions) {
  $("decisionCount").textContent = decisions.length ? `(${decisions.length})` : "";
  if (!decisions.length) {
    $("decisions").innerHTML = '<span class="empty">No decisions yet.</span>';
    return;
  }
  $("decisions").innerHTML = decisions
    .map((d) => {
      const by = (d.decided_by || []).join(", ");
      return `<div class="card" data-kind="decision">
        <div class="title">${esc(d.summary)}</div>
        ${by ? `<div class="meta">decided by: ${esc(by)}</div>` : ""}
        ${d.source_quote ? `<details><summary>View source quote</summary><blockquote class="quote">“${esc(d.source_quote)}”</blockquote></details>` : ""}
      </div>`;
    })
    .join("");
}

function renderRisksBlockers(risks, blockers) {
  $("riskCount").textContent = risks.length + blockers.length ? `(${risks.length + blockers.length})` : "";
  const parts = [];
  risks.forEach((r) => {
    parts.push(`<div class="card" data-kind="risk">
      <div class="title">${esc(r.title)}<span class="pill">${esc(r.severity)}</span></div>
      <div class="meta">risk · likelihood ${esc(r.likelihood)}</div>
    </div>`);
  });
  blockers.forEach((b) => {
    parts.push(`<div class="card" data-kind="blocker">
      <div class="title">${esc(b.summary)}<span class="pill">${esc(b.severity)}</span></div>
      <div class="meta">blocked: ${esc(b.blocked_party || "unknown")} · needs: ${esc(b.needs_from || "unknown")}</div>
    </div>`);
  });
  $("risksBlockers").innerHTML = parts.length
    ? parts.join("")
    : '<span class="empty">None yet.</span>';
}

// Configuration status is reported by the backend, not an OAuth connection claim.
const connectors = [
  { id: "google_calendar", name: "Google Calendar", purpose: "Use meeting titles, attendees, and dates.", keys: ["GOOGLE_CALENDAR_CLIENT_ID", "GOOGLE_CALENDAR_CLIENT_SECRET", "GOOGLE_CALENDAR_REFRESH_TOKEN"], help: "Use credentials and a refresh token from your Google OAuth app. GOOGLE_CALENDAR_ID defaults to primary." },
  { id: "linear", name: "Linear", purpose: "Send extracted tasks to your team’s issue tracker.", keys: ["LINEAR_API_KEY", "LINEAR_TEAM_ID"], help: "Set TASK_SYNC_ENABLED=true to enable recurring task sync." },
  { id: "jira", name: "Jira", purpose: "Create issues in your project.", keys: ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"], help: "Set TASK_SYNC_ENABLED=true to enable recurring task sync." },
  { id: "notion", name: "Notion", purpose: "Publish meeting notes to your workspace.", keys: ["NOTION_API_KEY", "NOTION_PARENT_PAGE_ID"], help: "Give your Notion integration access to the parent page." },
  { id: "slack", name: "Slack", purpose: "Deliver operational notifications to a channel.", keys: ["SLACK_BOT_TOKEN", "SLACK_DEFAULT_CHANNEL"], help: "Invite your Slack bot to the destination channel." },
  { id: "discord", name: "Discord", purpose: "Post meeting summaries and next steps to a Discord channel.", keys: ["DISCORD_WEBHOOK_URL"], help: "Create a channel webhook under Channel settings → Integrations → Webhooks, then copy its URL to the backend setting. Automatic mentions are disabled." },
  { id: "teams", name: "Microsoft Teams", purpose: "Post meeting summaries as Adaptive Cards to a Teams channel.", keys: ["TEAMS_WEBHOOK_URL"], help: "Create a Teams Workflow with the Teams webhook request trigger and a step posting the received Adaptive Card to your channel. Choose Anyone for callers; this connector does not send an OAuth token. Keep the generated URL on the backend." },
  { id: "webhooks", name: "Webhooks", purpose: "Forward meeting and task events to your own workflows.", keys: ["WEBHOOK_URL", "WEBHOOK_SECRET"], help: "Use an HTTPS destination and a secret of at least 32 characters." },
];
const connectorIcons = {
  google_calendar: '<rect x="4" y="6" width="16" height="15" rx="2"/><path d="M8 3v6m8-6v6M4 12h16M8 16h3"/>',
  linear: '<circle cx="12" cy="12" r="8"/><path d="m6 6 12 12M4 11l9 9m-8-4 3 3"/>',
  jira: '<path d="m12 3 9 9-9 9-9-9Z"/><path d="m12 8 4 4-4 4-4-4Z"/>',
  notion: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 16V8l8 8V8"/>',
  slack: '<path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>',
  discord: '<path d="M7 6a18 18 0 0 1 10 0l3 11-4 2-2-2h-4l-2 2-4-2Z"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/>',
  teams: '<rect x="3" y="8" width="11" height="11" rx="2"/><path d="M6 11h5m-2.5 0v5M16 9h5v6a3 3 0 0 1-5 2"/><circle cx="17" cy="5" r="2"/>',
  webhooks: '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="17" r="2.5"/><circle cx="19" cy="17" r="2.5"/><path d="m11 7-5 8m2 2h8m2-2-5-8"/>',
};
$("connectorList").innerHTML = connectors.map((item) => `<details class="connector"><summary><span class="connector-icon"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true">${connectorIcons[item.id]}</svg></span><span class="connector-name">${esc(item.name)}</span><span class="connector-state" id="connector-${item.id}">Not checked</span><span class="disclosure-arrow"></span></summary><p>${esc(item.purpose)}</p><p>Required backend settings:${item.keys.map((key) => `<code>${esc(key)}=…</code>`).join("")}</p><p>${esc(item.help)}</p></details>`).join("");
let connectorRequest = 0;
["backendUrl", "apiKey"].forEach((id) => $(id).addEventListener("input", () => {
  connectorRequest += 1;
  connectors.forEach((item) => {
    const state = $(`connector-${item.id}`);
    state.textContent = "Not checked";
    state.classList.remove("configured");
  });
  $("refreshConnectors").disabled = false;
  $("connectorStatus").textContent = "Backend settings changed. Check status again.";
}));
$("refreshConnectors").addEventListener("click", async () => {
  const request = ++connectorRequest;
  $("refreshConnectors").disabled = true;
  $("connectorStatus").textContent = "Checking backend configuration…";
  try {
    const base = $("backendUrl").value.trim().replace(/\/+$/, "");
    const headers = $("apiKey").value.trim() ? { "X-API-Key": $("apiKey").value.trim() } : {};
    const response = await fetch(`${base}/api/integrations/status`, { headers, signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);
    const data = await response.json();
    if (request !== connectorRequest) return;
    connectors.forEach((item) => {
      const state = $(`connector-${item.id}`);
      state.textContent = data[item.id] === true ? "Configured" : data[item.id] === false ? "Set up" : "Status unavailable";
      state.classList.toggle("configured", data[item.id] === true);
    });
    $("connectorStatus").textContent = `Configuration checked. Task sync is ${data.task_sync ? "enabled" : "disabled"}. This checks settings; it does not verify provider access.`;
  } catch (error) {
    if (request === connectorRequest) $("connectorStatus").textContent = `${error.message}. Open Backend connection, check your backend URL and API key, and retry.`;
  } finally {
    if (request === connectorRequest) $("refreshConnectors").disabled = false;
  }
});
