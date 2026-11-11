// Side panel: config + controls, renders the live transcript and the structured
// notes pushed from the backend over the WebSocket (relayed via the offscreen
// doc using chrome.runtime messaging).

const $ = (id) => document.getElementById(id);
const fields = ["backendUrl", "dashboardUrl", "projectHint", "apiKey", "title", "listeningLanguage", "transcriptionTerms"];
let languageRequest = 0;
let listeningPreferenceSet = false;
let automaticListeningHelp = "Automatic recognition keeps the languages you speak. Coverage depends on your transcription provider.";
function listeningLanguageHelp() {
  const selected = $("listeningLanguage");
  $("listeningLanguageHelp").textContent = capturing
    ? `Listening: ${selected.selectedOptions[0]?.textContent || selected.value}. Change the language before your next capture.`
    : selected.value === "auto"
      ? automaticListeningHelp
      : "Use this for a meeting mainly in one language. Choose Auto-detect for mixed-language speech.";
}
async function loadListeningLanguages() {
  const request = ++languageRequest;
  try {
    const base = $("backendUrl").value.trim().replace(/\/+$/, "");
    const key = $("apiKey").value.trim();
    const response = await fetch(`${base}/api/live/languages`, {
      headers: key ? { "X-API-Key": key } : {}, signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error("Language options unavailable");
    const data = await response.json();
    if (request !== languageRequest || capturing || !Array.isArray(data.items)) return;
    if (typeof data.auto_note === "string") automaticListeningHelp = data.auto_note;
    const chosen = listeningPreferenceSet ? $("listeningLanguage").value : data.default || "auto";
    $("listeningLanguage").replaceChildren(...data.items.map(item => new Option(item.label, item.code)));
    $("listeningLanguage").value = data.items.some(item => item.code === chosen) ? chosen : "auto";
    listeningLanguageHelp();
  } catch {
    if (request !== languageRequest || capturing) return;
    $("listeningLanguageHelp").textContent = "Couldn’t load language choices. Check your backend connection. Automatic recognition remains available.";
  }
}
$("listeningLanguage").addEventListener("change", () => { listeningPreferenceSet = true; listeningLanguageHelp(); });
["backendUrl", "apiKey"].forEach(id => $(id).addEventListener("change", () => void loadListeningLanguages()));
let currentTranscriptId = null;
async function openDashboard(transcriptId) {
  try {
    const url = dashboardLink($("dashboardUrl").value || "http://localhost:5173", transcriptId);
    await chrome.tabs.create({ url });
    $("dashboardStatus").hidden = true;
  } catch {
    $("dashboardStatus").hidden = false;
    $("dashboardStatus").textContent = "Could not open the dashboard. Check Dashboard URL in connection settings and try again.";
    selectView("connectors");
    $("connectionSettings").open = true;
    $("dashboardUrl").focus();
  }
}
$("openDashboard").addEventListener("click", () => openDashboard());
$("viewMeeting").addEventListener("click", () => openDashboard(currentTranscriptId));
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
  $("listeningLanguage").disabled = false;
  listeningLanguageHelp();
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
  $("dashboardUrl").value = saved.dashboardUrl || "http://localhost:5173";
  $("projectHint").value = saved.projectHint || "";
  $("apiKey").value = saved.apiKey || "";
  $("title").value = saved.title || "";
  $("transcriptionTerms").value = saved.transcriptionTerms || "";
  listeningPreferenceSet = saved.listeningLanguage === "auto" || (typeof saved.listeningLanguage === "string" && /^[a-z]{2}$/.test(saved.listeningLanguage));
  if (typeof saved.listeningLanguage === "string" && /^[a-z]{2}$/.test(saved.listeningLanguage)) {
    const label = new Intl.DisplayNames(["en"], { type: "language" }).of(saved.listeningLanguage) || saved.listeningLanguage;
    $("listeningLanguage").add(new Option(label, saved.listeningLanguage));
    $("listeningLanguage").value = saved.listeningLanguage;
  }
  void loadListeningLanguages();
  void refreshConnectors();
});
fields.forEach((f) =>
  $(f).addEventListener("change", () => chrome.storage.local.set({ [f]: $(f).value }))
);

// ---- start / stop ----------------------------------------------------------
$("toggle").addEventListener("click", () => (capturing ? stop() : start()));

async function start() {
  setStatus("Starting…", "connecting");
  $("toggle").disabled = true;
  $("listeningLanguage").disabled = true;

  const config = {
    backendUrl: $("backendUrl").value.trim(),
    projectHint: $("projectHint").value.trim(),
    apiKey: $("apiKey").value.trim(),
    title: $("title").value.trim(),
    calendarEventId: $("calendarEvent").value || null,
    language: $("listeningLanguage").value || "auto",
    transcriptionTerms: $("transcriptionTerms").value,
  };
  const res = await chrome.runtime.sendMessage({ type: "START", config }).catch((e) => ({
    ok: false,
    error: String(e),
  }));
  $("toggle").disabled = false;
  if (!res || !res.ok) {
    $("listeningLanguage").disabled = false;
    setStatus("Error: " + (res && res.error ? res.error : "failed to start"), "error");
    return;
  }
  finals = [];
  currentTranscriptId = res.transcriptId || null;
  $("viewMeeting").hidden = !currentTranscriptId;
  renderTranscript("");
  renderNotes({});
  $("copyStatus").textContent = "";
  capturing = true;
  listeningLanguageHelp();
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
  if (data.transcript_id) {
    currentTranscriptId = data.transcript_id;
    $("viewMeeting").hidden = false;
  }
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
        <div class="title" dir="auto">${esc(t.title)}<span class="pill">${esc(t.status)}</span><span class="pill">${esc(t.priority)}</span></div>
        <div class="meta">owner: ${esc(t.owner || "unassigned")} · due: ${due} ${conf ? "· conf " + conf : ""}</div>
        ${t.source_quote ? `<details><summary>View source quote</summary><blockquote class="quote" dir="auto">“${esc(t.source_quote)}”</blockquote></details>` : ""}
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
        <div class="title" dir="auto">${esc(d.summary)}</div>
        ${by ? `<div class="meta">decided by: ${esc(by)}</div>` : ""}
        ${d.source_quote ? `<details><summary>View source quote</summary><blockquote class="quote" dir="auto">“${esc(d.source_quote)}”</blockquote></details>` : ""}
      </div>`;
    })
    .join("");
}

function renderRisksBlockers(risks, blockers) {
  $("riskCount").textContent = risks.length + blockers.length ? `(${risks.length + blockers.length})` : "";
  const parts = [];
  risks.forEach((r) => {
    parts.push(`<div class="card" data-kind="risk">
      <div class="title" dir="auto">${esc(r.title)}<span class="pill">${esc(r.severity)}</span></div>
      <div class="meta">risk · likelihood ${esc(r.likelihood)}</div>
    </div>`);
  });
  blockers.forEach((b) => {
    parts.push(`<div class="card" data-kind="blocker">
      <div class="title" dir="auto">${esc(b.summary)}<span class="pill">${esc(b.severity)}</span></div>
      <div class="meta">blocked: ${esc(b.blocked_party || "unknown")} · needs: ${esc(b.needs_from || "unknown")}</div>
    </div>`);
  });
  $("risksBlockers").innerHTML = parts.length
    ? parts.join("")
    : '<span class="empty">None yet.</span>';
}

// Provider sign-in runs in a browser tab; credentials stay encrypted on the backend.
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
$("connectorList").innerHTML = connectors.map((item) => `<details class="connector"><summary><span class="connector-icon"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true">${connectorIcons[item.id]}</svg></span><span class="connector-name">${esc(item.name)}</span><span class="connector-state" id="connector-${item.id}">Not checked</span><span class="disclosure-arrow"></span></summary><p>${esc(item.purpose)}</p>${item.id === "webhooks" ? `<p>${esc(item.help)}</p><p>Required backend settings:${item.keys.map((key) => `<code>${esc(key)}=…</code>`).join("")}</p>` : `<button type="button" data-connect="${item.id}">Connect ${esc(item.name)}</button><button type="button" class="secondary" data-disconnect="${item.id}" hidden>Disconnect</button><p class="connector-feedback" id="feedback-${item.id}" role="status" aria-live="polite" hidden></p><p class="connector-help" id="help-${item.id}">Sign in and approve access to connect this workspace.</p><form class="connector-destination" data-destination="${item.id}" hidden><label for="destination-${item.id}">Destination</label><select id="destination-${item.id}" required><option value="">Choose a destination</option></select><button type="submit">Use destination</button></form>`}</details>`).join("");
let connectorRequest = 0;
let connectorBusy = false;
let activeConnector = null;
let connectionPoll = null;
let authTabId = null;
let authGeneration = 0;
function connectorHeaders() {
  return $("apiKey").value.trim() ? { "X-API-Key": $("apiKey").value.trim() } : {};
}
async function connectorAPI(path, init = {}) {
  const base = $("backendUrl").value.trim().replace(/\/+$/, "");
  if (!/^https?:\/\//.test(base)) throw new Error("Enter your backend URL in Backend connection.");
  let response;
  try {
    response = await fetch(`${base}${path}`, { ...init, headers: { "Content-Type": "application/json", ...connectorHeaders(), ...init.headers }, signal: AbortSignal.timeout(15000) });
  } catch {
    $("connectionSettings").open = true;
    throw new Error(`Cannot reach the AOI backend at ${base}. Start the backend or update Backend connection, then try again.`);
  }
  if (!response.ok) {
    if (response.status === 404) throw new Error("This backend does not have connector sign-in enabled. Update and restart the AOI backend, then try again.");
    if ([401, 403].includes(response.status)) throw new Error("Your backend rejected this request. Check the API key in Backend connection and use a workspace administrator’s token.");
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `Backend returned HTTP ${response.status}. Check your backend connection and try again.`);
  }
  try { return await response.json(); }
  catch { throw new Error("The Backend URL did not return an AOI API response. Check Backend connection and try again."); }
}
function connectorFeedback(provider, message, error = false) {
  const feedback = $(`feedback-${provider}`);
  if (!feedback) return;
  feedback.hidden = !message;
  feedback.textContent = message;
  feedback.classList.toggle("is-error", error);
  feedback.setAttribute("role", error ? "alert" : "status");
}
function setConnectorBusy(value) {
  connectorBusy = value;
  if (!value && activeConnector) {
    const button = document.querySelector(`[data-connect="${activeConnector}"]`);
    if (button?.dataset.idleLabel) button.textContent = button.dataset.idleLabel;
    activeConnector = null;
  }
  document.querySelectorAll("[data-connect], [data-disconnect], .connector-destination button, .connector-destination select").forEach(button => { button.disabled = value; });
}
function stopConnectionPoll() {
  authGeneration++;
  clearTimeout(connectionPoll);
  connectionPoll = null;
  setConnectorBusy(false);
}
["backendUrl", "apiKey"].forEach((id) => $(id).addEventListener("input", () => {
  connectorRequest++;
  stopConnectionPoll();
  connectors.forEach((item) => {
    connectorFeedback(item.id, "");
    const state = $(`connector-${item.id}`);
    state.textContent = "Not checked";
    state.classList.remove("configured");
    const connect = document.querySelector(`[data-connect="${item.id}"]`);
    if (connect) {
      connect.dataset.idleLabel = `Connect ${item.name}`;
      connect.textContent = connect.dataset.idleLabel;
      $(`help-${item.id}`).textContent = "Check this backend’s connection status, then sign in to connect.";
    }
    const disconnect = document.querySelector(`[data-disconnect="${item.id}"]`);
    if (disconnect) disconnect.hidden = true;
    const form = document.querySelector(`[data-destination="${item.id}"]`);
    if (form) form.hidden = true;
  });
  $("refreshConnectors").disabled = false;
  $("connectorStatus").textContent = "Backend settings changed. Check status again.";
}));
async function refreshConnectors() {
  const request = ++connectorRequest;
  $("refreshConnectors").disabled = true;
  $("connectorStatus").textContent = "Checking connections…";
  try {
    const [data, catalog] = await Promise.all([connectorAPI("/api/integrations/status"), connectorAPI("/api/connectors")]);
    if (request !== connectorRequest) return;
    connectors.forEach((item) => {
      const state = $(`connector-${item.id}`);
      const entry = catalog[item.id];
      const connected = entry ? ["connected", "configured"].includes(entry.status) : data[item.id] === true;
      const pending = entry?.status === "select_destination";
      state.textContent = entry?.status === "connected" ? "Connected" : connected ? "Configured" : pending ? "Choose destination" : entry && !entry.oauth_available ? "Setup required" : "Not connected";
      state.classList.toggle("configured", connected);
      if (!entry) return;
      const connect = document.querySelector(`[data-connect="${item.id}"]`);
      connect.dataset.idleLabel = `${connected || pending ? "Reconnect" : "Connect"} ${item.name}`;
      if (activeConnector !== item.id) connect.textContent = connect.dataset.idleLabel;
      document.querySelector(`[data-disconnect="${item.id}"]`).hidden = !connected && !pending;
      $(`help-${item.id}`).textContent = entry.destination ? `Destination: ${entry.destination}` : entry.oauth_available ? "Sign in and approve access to connect this workspace." : entry.setup_error || "Your administrator needs to configure this provider’s OAuth app on the backend.";
      const form = document.querySelector(`[data-destination="${item.id}"]`);
      form.hidden = !pending;
      const select = $(`destination-${item.id}`);
      select.replaceChildren(new Option("Choose a destination", ""), ...entry.choices.map(c => new Option(c.label, c.id)));
    });
    $("connectorStatus").textContent = "Connections checked. Sign in to a tool to connect it to this workspace.";
  } catch (error) {
    if (request === connectorRequest) $("connectorStatus").textContent = error.message;
  } finally {
    if (request === connectorRequest) $("refreshConnectors").disabled = false;
  }
}
$("refreshConnectors").addEventListener("click", () => void refreshConnectors());
$("connectorList").addEventListener("click", async (event) => {
  const connect = event.target.closest("[data-connect]");
  const disconnect = event.target.closest("[data-disconnect]");
  if ((!connect && !disconnect) || connectorBusy) return;
  setConnectorBusy(true);
  const generation = ++authGeneration;
  const provider = (connect || disconnect).dataset[connect ? "connect" : "disconnect"];
  activeConnector = provider;
  if (connect) {
    connect.dataset.idleLabel = connect.textContent;
    connect.textContent = "Opening sign-in…";
  }
  connectorFeedback(provider, disconnect ? "Disconnecting…" : "Contacting the backend to open sign-in…");
  try {
    if (disconnect) {
      await connectorAPI(`/api/connectors/${provider}`, { method: "DELETE" });
      if (generation !== authGeneration) return;
      await refreshConnectors();
      connectorFeedback(provider, "Connector disconnected.");
      $("connectorStatus").textContent = "Connector disconnected.";
      setConnectorBusy(false);
      return;
    }
    const result = await connectorAPI(`/api/connectors/${provider}/start`, { method: "POST" });
    if (generation !== authGeneration) return;
    const tab = await chrome.tabs.create({ url: result.authorization_url });
    authTabId = tab.id;
    connect.textContent = "Waiting for sign-in…";
    connectorFeedback(provider, "Finish signing in in the authentication tab. This connection will update automatically.");
    $("connectorStatus").textContent = "Finish signing in in the authentication tab. This panel will update automatically.";
    const deadline = Date.now() + result.expires_in * 1000;
    async function poll() {
      if (generation !== authGeneration) return;
      if (Date.now() >= deadline) { connectorFeedback(provider, "Sign-in timed out. Click Connect to try again.", true); stopConnectionPoll(); $("connectorStatus").textContent = "Sign-in timed out. Click Connect to try again."; return; }
      try {
        const status = await connectorAPI(`/api/connectors/attempts/${result.ticket}`);
        if (generation !== authGeneration) return;
        if (status.status !== "pending") {
          if (status.status === "failed") $("connectorStatus").textContent = status.message;
          else { await refreshConnectors(); $("connectorStatus").textContent = status.message; }
          connectorFeedback(provider, status.message || "Sign-in complete.", status.status === "failed");
          const completedTab = authTabId;
          authTabId = null;
          stopConnectionPoll();
          if (completedTab) void chrome.tabs.remove(completedTab).catch(() => {});
          return;
        }
      } catch (error) {
        if (generation !== authGeneration) return;
        connectorFeedback(provider, error.message, true);
        $("connectorStatus").textContent = error.message;
      }
      connectionPoll = setTimeout(() => void poll(), 2000);
    }
    void poll();
  } catch (error) {
    if (generation !== authGeneration) return;
    connectorFeedback(provider, error.message, true);
    $("connectorStatus").textContent = error.message;
    stopConnectionPoll();
  }
});
$("connectorList").addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-destination]");
  if (!form) return;
  event.preventDefault();
  if (connectorBusy) return;
  const provider = form.dataset.destination;
  setConnectorBusy(true);
  connectorFeedback(provider, "Saving destination…");
  try {
    await connectorAPI(`/api/connectors/${provider}/destination`, { method: "POST", body: JSON.stringify({ id: $(`destination-${provider}`).value }) });
    await refreshConnectors();
    connectorFeedback(provider, "Destination saved.");
  } catch (error) { connectorFeedback(provider, error.message, true); $("connectorStatus").textContent = error.message; }
  finally { setConnectorBusy(false); }
});
window.addEventListener("focus", () => { if (!connectorBusy) void refreshConnectors(); });
chrome.tabs.onRemoved.addListener((id) => {
  if (id === authTabId && connectorBusy) {
    authTabId = null;
    connectorFeedback(activeConnector, "Sign-in tab closed. Checking whether the connection completed…");
    stopConnectionPoll();
    void refreshConnectors();
  }
});
// Loading saved configuration above discovers sign-ins completed while the panel was closed.
