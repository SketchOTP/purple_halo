/* purple_halo — primary operator product UI */

let operatorApiToken = "";

async function api(path, opts = {}, retried = false) {
  const method = String(opts.method || "GET").toUpperCase();
  const headers = {
    "Content-Type": "application/json",
    ...(opts.headers || {}),
  };
  if (operatorApiToken) headers.Authorization = `Bearer ${operatorApiToken}`;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401 && method === "POST" && !retried) {
    const entered = window.prompt("Enter the operator API token to continue:");
    if (entered && entered.trim()) {
      operatorApiToken = entered.trim();
      return api(path, opts, true);
    }
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || data.summary || res.statusText || "request failed");
    err.data = data;
    throw err;
  }
  return data;
}

function $(id) {
  return document.getElementById(id);
}

function flash(msg, isErr, isOk) {
  const el = $("flash");
  if (!el) return;
  el.hidden = !msg;
  el.textContent = msg || "";
  el.classList.toggle("err", !!isErr);
  el.classList.toggle("ok", !!isOk && !isErr);
}

function pulseBtn(id, label, ms) {
  const btn = $(id);
  if (!btn) return;
  const origTitle = btn.title;
  btn.classList.add("saved");
  btn.title = label;
  setTimeout(() => { btn.classList.remove("saved"); btn.title = origTitle; }, ms || 2000);
}

function setBusy(busy) {
  document.querySelectorAll("button").forEach((b) => {
    b.disabled = busy;
  });
}

function renderServiceLed(st) {
  const svcUp = /up|running/i.test(st.service_state || "");
  const svcLed = $("serviceLed");
  svcLed.className = "service-led " + (svcUp ? "up" : "down");
  svcLed.title = svcUp ? "Service running" : "Service not running";
}


function renderBrand(st) {
  const tag = $("repoTag");
  if (!tag) return;
  const name = (st.repo_name || "").trim();
  if (name && name !== "purple_halo") {
    tag.hidden = false;
    tag.textContent = `[${name}]`;
  } else {
    tag.hidden = true;
    tag.textContent = "";
  }
}

function renderMasterChrome(st) {
  const isMaster = st.is_master !== false;
  const install = $("stepRepo");
  const sched = $("stepFrequency");
  if (install) install.hidden = !isMaster;
  if (sched) sched.classList.toggle("span-2", !isMaster);
}


let scheduleDirty = false;
let goalDirty = false;

function markScheduleDirty() {
  scheduleDirty = true;
}

function scheduleKind() {
  const active = document.querySelector(".sch-toggle-opt[aria-pressed=\"true\"]");
  return active ? active.dataset.kind : "interval";
}

function setScheduleKind(kind) {
  document.querySelectorAll(".sch-toggle-opt").forEach((btn) => {
    btn.setAttribute("aria-pressed", btn.dataset.kind === kind ? "true" : "false");
  });
  toggleScheduleMode(kind);
}

function toggleScheduleMode(kind) {
  $("scheduleInterval").hidden = kind !== "interval";
  $("scheduleTimes").hidden = kind !== "times";
}

function createTimeSlotRow(at) {
  const row = document.createElement("div");
  row.className = "time-slot";
  row.innerHTML =
    "<input type=\"time\" class=\"slot-at\" value=\"" +
    (at || "09:00") +
    "\" />" +
    "<button type=\"button\" class=\"slot-remove\" aria-label=\"Remove time\" title=\"Remove\">×</button>";
  row.querySelector(".slot-remove").onclick = () => {
    markScheduleDirty();
    row.remove();
    if (!$("timeSlots").querySelector(".slot-at")) addTimeSlotRow("09:00");
  };
  return row;
}

function addTimeSlotRow(at) {
  markScheduleDirty();
  $("timeSlots").appendChild(createTimeSlotRow(at));
}

function selectedWeekdays() {
  const boxes = [...document.querySelectorAll(".wd-check input")];
  const checked = boxes.filter((cb) => cb.checked).map((cb) => Number(cb.dataset.wd));
  if (checked.length === 0 || checked.length === boxes.length) return null;
  return checked;
}

function setWeekdaysFromRuns(runs) {
  const boxes = [...document.querySelectorAll(".wd-check input")];
  if (!runs || !runs.length) {
    boxes.forEach((cb) => { cb.checked = true; });
    return;
  }
  const days = new Set();
  for (const run of runs) {
    for (const d of run.days || []) {
      if (typeof d === "number") days.add(d);
      else if (typeof d === "string") {
        const key = d.trim().toLowerCase().slice(0, 3);
        const map = { mon: 0, tue: 1, wed: 2, thu: 3, fri: 4, sat: 5, sun: 6 };
        if (key in map) days.add(map[key]);
      }
    }
  }
  if (!days.size) {
    boxes.forEach((cb) => { cb.checked = true; });
    return;
  }
  boxes.forEach((cb) => { cb.checked = days.has(Number(cb.dataset.wd)); });
}

function renderTimeSlots(runs) {
  const host = $("timeSlots");
  host.innerHTML = "";
  const list = runs && runs.length ? runs : [{ at: "09:00" }];
  list.forEach((run) => host.appendChild(createTimeSlotRow(String(run.at || "09:00").slice(0, 5))));
}

function renderSchedule(st) {
  if (scheduleDirty) return;
  const kindRaw = st.schedule_kind || (st.every_hours != null ? "interval" : "times");
  const kind = kindRaw === "interval" ? "interval" : "times";
  setScheduleKind(kind);
  if (st.every_hours != null) $("everyHours").value = st.every_hours;
  if (st.for_days != null) {
    $("forDays").value = st.for_days;
    $("forDaysTimes").value = st.for_days;
  }
  $("everyWeeks").value = st.every_weeks != null ? st.every_weeks : 1;
  $("untilGoal").checked = !!st.until_goal_achieved;
  setWeekdaysFromRuns(st.runs || []);
  renderTimeSlots(st.runs || []);
}

function collectSchedulePayload() {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const until_goal = $("untilGoal").checked;
  if (scheduleKind() === "times") {
    const days = selectedWeekdays();
    const runs = [...document.querySelectorAll(".slot-at")]
      .map((inp) => {
        const slot = { at: inp.value };
        if (days) slot.days = days;
        return slot;
      })
      .filter((s) => s.at);
    const rawDays = Number($("forDaysTimes").value);
    return { kind: "times", runs, for_days: rawDays > 0 ? rawDays : null,
      every_weeks: Number($("everyWeeks").value) || 1, until_goal, timezone: tz };
  }
  const rawDays = Number($("forDays").value);
  return { kind: "interval", every: `${$("everyHours").value}h`, for_days: rawDays > 0 ? rawDays : null,
    until_goal, timezone: tz };
}

function setStepState(id, ready) {
  const el = $(id);
  if (!el) return;
  el.classList.toggle("ready", !!ready);
}

function renderRunChart(report) {
  const chart = $("runChart");
  chart.innerHTML = "";
  const lines = (report || "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .slice(-20);
  if (!lines.length) {
    chart.classList.add("empty");
    return;
  }
  chart.classList.remove("empty");
  lines.forEach((line, i) => {
    const bar = document.createElement("div");
    const fail = /fail|error|block/i.test(line);
    bar.className = "run-bar" + (fail ? " fail" : "");
    bar.style.height = `${Math.min(24 + (i + 1) * 8, 100)}%`;
    bar.title = line;
    chart.appendChild(bar);
  });
}

function renderInstallResult(result) {
  const box = $("installNote");
  const summary = $("installSummary");
  const details = $("installDetails");
  if (!result || !result.ok) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  summary.textContent = result.message || "Installed.";
  const install = result.install || {};
  const lines = [];
  if (install.ui_url) lines.push(install.ui_url);
  if (install.port) lines.push(`port ${install.port}`);
  if (result.stdout) lines.push("", result.stdout.trim());
  details.textContent = lines.join("\n") || "";
}


function renderProgress(st) {
  const sum = document.getElementById("progressSummary");
  const det = document.getElementById("progressDetail");
  if (!sum || !det) return;
  const p = st.cycle_progress || {};
  const lr = st.last_run || {};
  const playing = st.playing ? "Playing" : "Paused";
  const mode = st.is_master ? "master" : "project";
  let headline = playing + " · " + mode + " mode";
  if (p.cycle_id) headline += " · cycle " + p.cycle_id;
  if (p.status) headline += " · " + p.status;
  sum.textContent = headline;
  const parts = [];
  if (p.summary) parts.push(p.summary);
  else if (lr.error) parts.push(String(lr.error));
  else if (lr.status) parts.push("Last scheduler: " + lr.status);
  if (p.blocked_classification) parts.push("Blocker: " + p.blocked_classification);
  if (p.outcome_reason) parts.push(p.outcome_reason);
  if (p.next_focus) parts.push("Next: " + p.next_focus);
  det.textContent = parts.filter(Boolean).join(" · ") || (st.playing ? "Waiting for next scheduled run." : "Press Play to start.");
}

function render(st) {
  renderSchedule(st);
  if ($("goalEditor") && !goalDirty) $("goalEditor").value = st.goal_preview || "";
  renderBrand(st);
  renderMasterChrome(st);
  renderServiceLed(st);
  renderProgress(st);
  const runCount = st.run_count || 0;
  $("reportMeta").textContent = runCount ? `${runCount} run${runCount === 1 ? "" : "s"}` : "";
  renderRunChart(st.report);
  $("report").textContent = st.report || "";
  $("report").scrollTop = $("report").scrollHeight;
  setStepState("stepRepo", !!st.repo);
  setStepState("stepGoal", !!st.goal_ready);
  setStepState("stepFrequency", !!st.schedule_saved);
  setStepState("stepPlay", !!st.playing);
}

async function refresh() {
  const st = await api("/api/simple/status");
  render(st);
  return st;
}

async function withAction(fn) {
  flash("");
  setBusy(true);
  try {
    const result = await fn();
    if (result && result.status) render(result.status);
    else await refresh();
    if (result && result.ok === false) flash(result.error || result.message || "Failed", true);
    else if (result && result.message) flash(result.message, false, true);
    if (result && result.install !== undefined) renderInstallResult(result);
    return result;
  } catch (err) {
    flash(err.message || String(err), true);
    try { await refresh(); } catch (_) {}
  } finally { setBusy(false); }
}


async function waitForInstance(url) {
  const base = String(url || "").replace(/\/?$/, "/");
  const statusUrl = base + "api/simple/status";
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch(statusUrl, { cache: "no-store" });
      if (res.ok) return true;
    } catch (_) {}
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function bind() {
  document.querySelectorAll(".sch-toggle-opt").forEach((btn) => {
    btn.onclick = () => { markScheduleDirty(); setScheduleKind(btn.dataset.kind); };
  });
  $("stepFrequency").addEventListener("input", markScheduleDirty);
  $("stepFrequency").addEventListener("change", markScheduleDirty);
  if ($("goalEditor")) $("goalEditor").addEventListener("input", () => { goalDirty = true; });
  $("btnAddSlot").onclick = () => addTimeSlotRow("12:00");
  $("btnFrequency").onclick = () => withAction(async () => {
    const result = await api("/api/simple/frequency", { method: "POST", body: JSON.stringify(collectSchedulePayload()) });
    if (result.ok !== false) { scheduleDirty = false; pulseBtn("btnFrequency", "Saved!"); }
    return result;
  });
  $("btnGoal").onclick = () => withAction(async () => {
    const result = await api("/api/simple/goal", { method: "POST", body: JSON.stringify({ content: $("goalEditor").value }) });
    if (result.ok !== false) { goalDirty = false; pulseBtn("btnGoal", "Saved!"); }
    return result;
  });
  $("btnPlay").onclick = () => withAction(() => api("/api/simple/play", { method: "POST", body: "{}" }));
  $("btnPause").onclick = () => withAction(() => api("/api/simple/pause", { method: "POST", body: "{}" }));
  $("btnRunOnce").onclick = () => withAction(() => api("/api/simple/run-now", { method: "POST", body: "{}" }));
  $("btnInstall").onclick = () => withAction(async () => {
    const repo = $("installRepo").value.trim();
    if (!repo) throw new Error("Enter a repo path to install into");
    flash("Installing…", false, true);
    const result = await api("/api/simple/install", { method: "POST", body: JSON.stringify({ repo }) });
    renderInstallResult(result);
    const url = result.install && result.install.ui_url;
    if (result.ok && url) {
      if (!result.install.ui_ready) { flash("Waiting for new instance to start…", false, true); await waitForInstance(url); }
      const opened = window.open(url, "_blank", "noopener,noreferrer");
      if (!opened) result.message = (result.message || "Installed.") + " Pop-up blocked — use the URL below.";
    }
    return result;
  });
}

bind();
refresh().catch((err) => flash(err.message || String(err), true));
setInterval(() => {
  refresh().catch(() => {});
}, 15000);
