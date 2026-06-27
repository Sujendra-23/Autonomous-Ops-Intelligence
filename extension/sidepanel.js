// Side panel: config + controls, renders the live transcript and the structured
// notes pushed from the backend over the WebSocket (relayed via the offscreen
// doc using chrome.runtime messaging).

const $ = (id) => document.getElementById(id);
const fields = ["backendUrl", "projectHint", "apiKey", "title"];
let capturing = false;
let finals = []; // finalized transcript segments

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
  setStatus("Starting…");
  $("toggle").disabled = true;
  finals = [];
  renderTranscript("");
  const config = {
    backendUrl: $("backendUrl").value.trim(),
    projectHint: $("projectHint").value.trim(),
    apiKey: $("apiKey").value.trim(),
    title: $("title").value.trim(),
  };
  const res = await chrome.runtime.sendMessage({ type: "START", config }).catch((e) => ({
    ok: false,
    error: String(e),
  }));
  $("toggle").disabled = false;
  if (!res || !res.ok) {
    setStatus("Error: " + (res && res.error ? res.error : "failed to start"), false);
    return;
  }
  capturing = true;
  $("toggle").textContent = "Stop & finalize";
  $("toggle").classList.add("stop");
  $("dot").classList.add("live");
  setStatus(res.sttEnabled ? "Listening…" : "Connected — but STT is not configured on the backend.");
}

async function stop() {
  setStatus("Finalizing…");
  await chrome.runtime.sendMessage({ type: "STOP" }).catch(() => {});
  capturing = false;
  $("toggle").textContent = "Start capturing";
  $("toggle").classList.remove("stop");
  $("dot").classList.remove("live");
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
      setStatus("Listening…");
      break;
    case "done":
      setStatus("Meeting finalized ✓");
      break;
    case "closed":
      if (capturing) setStatus("Connection closed.");
      break;
    case "error":
      setStatus("Error: " + (data.detail || "unknown"), false);
      break;
  }
});

// ---- rendering -------------------------------------------------------------
function setStatus(text) {
  $("statusText").textContent = text;
}

function renderTranscript(interim) {
  const el = $("transcript");
  if (!finals.length && !interim) {
    el.innerHTML = '<span class="empty">Transcript will appear here…</span>';
    return;
  }
  el.textContent = finals.join(" ");
  if (interim) {
    const span = document.createElement("span");
    span.className = "interim";
    span.textContent = " " + interim;
    el.appendChild(span);
  }
  el.scrollTop = el.scrollHeight;
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
      return `<div class="card">
        <div class="title">${esc(t.title)}<span class="pill">${esc(t.status)}</span><span class="pill">${esc(t.priority)}</span></div>
        <div class="meta">owner: ${esc(t.owner || "unassigned")} · due: ${due} ${conf ? "· conf " + conf : ""}</div>
        ${t.source_quote ? `<div class="quote">“${esc(t.source_quote)}”</div>` : ""}
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
      return `<div class="card">
        <div class="title">${esc(d.summary)}</div>
        ${by ? `<div class="meta">decided by: ${esc(by)}</div>` : ""}
        ${d.source_quote ? `<div class="quote">“${esc(d.source_quote)}”</div>` : ""}
      </div>`;
    })
    .join("");
}

function renderRisksBlockers(risks, blockers) {
  const parts = [];
  risks.forEach((r) => {
    parts.push(`<div class="card">
      <div class="title">⚠️ ${esc(r.title)}<span class="pill">${esc(r.severity)}</span></div>
      <div class="meta">risk · likelihood ${esc(r.likelihood)}</div>
    </div>`);
  });
  blockers.forEach((b) => {
    parts.push(`<div class="card">
      <div class="title">🚧 ${esc(b.summary)}<span class="pill">${esc(b.severity)}</span></div>
      <div class="meta">blocked: ${esc(b.blocked_party || "unknown")} · needs: ${esc(b.needs_from || "unknown")}</div>
    </div>`);
  });
  $("risksBlockers").innerHTML = parts.length
    ? parts.join("")
    : '<span class="empty">None yet.</span>';
}
