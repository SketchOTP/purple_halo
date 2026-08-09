const ACTIONS = {
  "run-now": { label: "Run now", path: "/api/actions/run-now", confirm: "Start a run now?", danger: false },
  "run-due": { label: "Run if due", path: "/api/actions/run-due", confirm: "Only run if a scheduled time is due right now?", danger: false },
  pause: { label: "Pause auto-run", path: "/api/actions/pause", confirm: "Pause automatic runs? Nothing will run on a schedule until you turn it back on.", danger: true },
  resume: { label: "Turn auto-run on", path: "/api/actions/resume", confirm: "Turn automatic runs back on?", danger: true },
  verify: { label: "Check health", path: "/api/actions/verify", confirm: null, danger: false },
  "refresh-ledger": { label: "Reload goals", path: "/api/actions/refresh-ledger", confirm: null, danger: false },
  "self-check": { label: "Run health tests", path: "/api/actions/self-check", confirm: null, danger: false },
  "service-restart": {
    label: "Restart the app",
    path: "/api/actions/service-restart",
    confirm: "Restart the app? This screen may disconnect for a moment.",
    danger: true,
  },
  refresh: { label: "Refresh", path: null, confirm: null, danger: false },
};

const state = {
  route: "home",
  overview: null,
  runs: null,
  ledger: null,
  diagnostics: null,
  schedule: null,
  budget: null,
  actionState: Object.fromEntries(Object.keys(ACTIONS).map((k) => [k, { state: "idle", detail: "" }])),
  actionLog: loadLog(),
  busyCount: 0,
  pollTimer: null,
  runFilter: "all",
};

function loadLog() {
  try { return JSON.parse(sessionStorage.getItem("ph_action_log") || "[]"); } catch { return []; }
}
function saveLog() {
  sessionStorage.setItem("ph_action_log", JSON.stringify(state.actionLog.slice(0, 40)));
}

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3000);
}

let operatorApiToken = "";

async function api(path, opts = {}, retried = false) {
  const method = String(opts.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (operatorApiToken && method === "POST") headers.Authorization = `Bearer ${operatorApiToken}`;
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
    const err = new Error(data.error || data.summary || res.statusText);
    err.payload = data;
    throw err;
  }
  return data;
}

function confirmAction(title, body, danger = false) {
  return new Promise((resolve) => {
    const modal = document.getElementById("modal");
    document.getElementById("modal-title").textContent = title;
    document.getElementById("modal-body").textContent = body;
    const ok = document.getElementById("modal-ok");
    ok.className = danger ? "btn danger" : "btn primary";
    modal.classList.add("show");
    modal.querySelector(".modal").classList.toggle("danger", danger);
    const done = (v) => {
      modal.classList.remove("show");
      ok.onclick = null;
      document.getElementById("modal-cancel").onclick = null;
      resolve(v);
    };
    ok.onclick = () => done(true);
    document.getElementById("modal-cancel").onclick = () => done(false);
  });
}

function openDrawer(title, html) {
  document.getElementById("drawer-title").textContent = title;
  document.getElementById("drawer-body").innerHTML = html;
  document.getElementById("drawer-bg").classList.add("show");
}
function closeDrawer() {
  document.getElementById("drawer-bg").classList.remove("show");
}

function setActionState(name, st, detail = "") {
  state.actionState[name] = { state: st, detail };
}

function pushLog(entry) {
  state.actionLog.unshift(entry);
  state.actionLog = state.actionLog.slice(0, 40);
  saveLog();
}

function problemHint(ov) {
  const items = ov.reopened_criteria || ov.regressions || [];
  const first = items[0] || {};
  const id = first.id || first.criterion_id || "";
  const detail = String(first.detail || first.blocker_reason || ov.hold_why || "");
  const names = {
    continuity_state: "saved memory for the next run",
    cycle_inspect_decide: "the inspect-and-decide step",
    explicit_plan: "plan writing",
    agent_execution: "doing the work",
    verification_evidence: "checking results",
    autonomous_iteration: "automatic repeats",
  };
  const what = names[id] || (id ? id.replaceAll("_", " ") : "a goal");
  let why = "";
  if (detail.includes("missing") || detail.includes("corrupt") || detail.includes("unreadable")) {
    why = "Its saved memory file is missing or damaged.";
  } else if (detail.includes("partial") || detail.includes("awaiting_runtime")) {
    why = "It no longer has fresh proof that this still works.";
  } else if (detail.includes("cheap_default") || detail.includes("expensive")) {
    why = "Cost settings drifted away from low-cost mode.";
  } else if (detail) {
    why = detail.replaceAll("_", " ") + ".";
  }
  if (!items.length && !ov.hold_why) {
    return "Something needs a fix. Press Run now, or open Goals for details.";
  }
  const extra = items.length > 1 ? ` (${items.length} issues — see Goals)` : "";
  const whyBit = why ? ` ${why}` : "";
  return `Problem with ${what}.${whyBit} Press Run now to try a fix, or open Goals.${extra}`;
}

function operatorState(ov) {
  const mode = ov.mode || {};
  const svc = ov.service || {};
  const kind = ov.hold_run_kind || "";
  let status = "All good";
  let statusClass = "ok";
  let runType = "On hold";
  let runClass = "purple";
  let next = "Nothing for you to do. Everything looks fine.";

  if (svc.state && svc.state !== "up") {
    status = "Needs help";
    statusClass = "bad";
    runType = "App is down";
    runClass = "bad";
    next = "The app is not running. Open Advanced and restart it.";
  } else if (!mode.ledger_intact || kind === "repair" || ov.health === "repair") {
    status = "Fixing a problem";
    statusClass = "warn";
    runType = "Fixing";
    runClass = "warn";
    next = problemHint(ov);
  } else if (!mode.repeated_operation_allowed || ov.health === "paused") {
    status = "Paused";
    statusClass = "bad";
    runType = "Auto-run off";
    runClass = "bad";
    next = "Automatic runs are off. Turn auto-run on when you want them again.";
  } else if (kind === "verify_only" || ov.health === "hold" || ov.health === "healthy") {
    status = "All good";
    statusClass = "ok";
    runType = "Checking only";
    runClass = "ok";
    next = "Goals are done. Runs only check that things still work — they will not build new work.";
  } else {
    status = "All good";
    statusClass = "ok";
    runType = "Working on goals";
    runClass = "purple";
    next = ov.goal_progress?.why_next_run || "Ready for the next automatic run.";
  }

  const autonomyOn = !!mode.repeated_operation_allowed;
  return { status, statusClass, runType, runClass, next, autonomyOn, mode, svc };
}

function statusLabel(st) {
  const m = { complete: "Done", partial: "In progress", blocked: "Stuck", unmet: "Not done", reopened: "Broke again" };
  return m[st] || st || "—";
}

function actionBtn(name, extra = "") {
  const meta = ACTIONS[name];
  const st = state.actionState[name] || { state: "idle" };
  const pending = st.state === "pending";
  return `<button class="btn ${extra} ${st.state}" data-action="${name}" ${pending ? "disabled" : ""}>${pending ? "Working… " : ""}${meta.label}</button>`;
}

function pauseResumeBtn() {
  const on = operatorState(state.overview || {}).autonomyOn;
  return on ? actionBtn("pause") : actionBtn("resume", "primary");
}

function renderTopStatus(ov) {
  const s = operatorState(ov || {});
  document.getElementById("top-status").innerHTML = `
    <span class="badge ${s.statusClass}">${s.status}</span>
    <span class="badge ${s.runClass}">${s.runType}</span>`;
  const dot = document.getElementById("live-dot");
  const svc = (ov || {}).service || {};
  dot.className = "live-dot" + (state.busyCount ? " busy" : svc.state && svc.state !== "up" ? " down" : "");
}

function renderHome(ov) {
  const s = operatorState(ov || {});
  const b = ov.budget || {};
  const g = ov.goal_progress || {};
  const last = ov.last_run || {};
  const sched = ov.schedule || {};
  const pct = Math.min(100, Number(g.pct || 0));
  const budgetPct = b.monthly_token_ceiling
    ? Math.min(100, Math.round((100 * (b.monthly_token_usage || 0)) / b.monthly_token_ceiling))
    : 0;
  const alerts = [];
  (ov.reopened_criteria || []).slice(0, 2).forEach((r) => alerts.push(`Broke again: ${r.id || r}`));
  if (ov.stop_reason) alerts.push(ov.stop_reason);
  if ((ov.service || {}).last_failure) alerts.push((ov.service || {}).last_failure);
  const nextRun = ((sched.runs || [])[0] || {}).at || "—";
  const lastKind = last.run_kind === "repair" ? "Fixed a problem" : last.run_kind === "verify_only" ? "Health check" : (last.classification || "—");

  return `
  <section class="hero">
    <div class="hero-top">
      <span class="badge ${s.statusClass}">${s.status}</span>
      <span class="badge ${s.runClass}">${s.runType}</span>
    </div>
    <div class="hero-hint">${s.next}</div>
    <div class="hero-actions">
      ${actionBtn("run-now", "primary")}
      ${pauseResumeBtn()}
      ${actionBtn("verify")}
      <button class="linkish" data-action="run-due">Run if due</button>
    </div>
    ${!last.started_at && !last.finished_at ? `<div class="muted" style="margin-top:12px">New here? Read the status above, then press <b>Run now</b>, or open <b>Settings</b> to set when it runs and how much it may spend.</div>` : ""}
  </section>

  <div class="grid cols-2">
    <div class="card">
      <h3>System</h3>
      <div class="row">
        <span class="chip ${(ov.service || {}).state === "up" ? "ok" : "bad"}">${(ov.service || {}).state === "up" ? "App is running" : "App is not running"}</span>
        <span class="chip ${s.autonomyOn ? "ok" : "bad"}">${s.autonomyOn ? "Auto-run is on" : "Auto-run is off"}</span>
      </div>
      <div style="margin-top:12px"><b>Last run:</b> ${lastKind}</div>
      <div class="muted">${last.plan_id || "No recent run"} · ${last.started_at || ""}</div>
      <div class="muted" style="margin-top:8px">Next automatic run: <b>${nextRun}</b> · max ${sched.max_runs_per_day || 0}/day</div>
    </div>
    <div class="card">
      <h3>Goals</h3>
      <div class="metric">${g.complete || 0}<span class="muted"> / ${g.total || 0}</span></div>
      <div class="bar green" style="margin-top:10px"><i style="width:${pct}%"></i></div>
      <div class="muted" style="margin-top:10px">Next: <b>${(g.top_unmet || {}).display_name || (g.top_unmet || {}).id || "None"}</b></div>
      <button class="linkish" style="margin-top:8px" data-go="goals">View goals</button>
    </div>
    <div class="card">
      <h3>Budget</h3>
      <div class="metric sm">${b.budget_mode === "cheap_default" ? "Keeping costs low" : (b.budget_mode || "—")}</div>
      <div class="muted">${b.monthly_token_usage || 0} / ${b.monthly_token_ceiling || 0} this month</div>
      <div class="bar ${budgetPct > 90 ? "red" : budgetPct > 70 ? "amber" : "green"}" style="margin-top:10px"><i style="width:${budgetPct}%"></i></div>
      <button class="linkish" style="margin-top:8px" data-go="settings">Edit budget</button>
    </div>
    <div class="card">
      <h3>Attention</h3>
      ${alerts.length
        ? `<div class="row">${alerts.slice(0, 2).map((a) => `<span class="chip warn">${a}</span>`).join("")}</div>
           <div class="muted" style="margin-top:10px">${alerts.length > 2 ? `+${alerts.length - 2} more in Advanced` : "Top issues only"}</div>`
        : `<div class="metric sm" style="color:var(--green)">Nothing needs you</div><div class="muted">No problems or pauses need your attention.</div>`}
    </div>
  </div>`;
}

function outcomeTone(r) {
  const oc = String(r.outcome_class || r.run_kind || "").toLowerCase();
  if (oc.includes("repair") || oc.includes("blocked") || oc.includes("fail")) return "bad";
  if (oc.includes("skip") || oc.includes("pause") || oc.includes("verify")) return "warn";
  if (r.meaningful_product_progress || r.meaningful_progress || oc.includes("progress") || oc === "verify_only") return "ok";
  return "";
}

function outcomeLabel(r) {
  const oc = r.outcome_class || "";
  const kind = r.run_kind || "";
  if (kind === "repair") return "Fixed a problem";
  if (kind === "verify_only" || oc === "verify_only_healthy") return "All good";
  if (oc.includes("skip") || oc.includes("slot") || oc.includes("no_due")) return "Skipped";
  if (oc.includes("block") || oc.includes("fail") || oc.includes("pause")) return "Hit a problem";
  if (r.meaningful_product_progress || r.meaningful_progress) return "Made progress";
  return oc || "Run";
}

function renderRuns(data) {
  const hold = (data.hold_runs || []).slice().reverse();
  const seq = (data.sequence || []).slice().reverse();
  const items = (hold.length ? hold : seq).map((r) => ({
    ...r,
    _label: outcomeLabel(r),
    _tone: outcomeTone(r),
  }));
  const filter = state.runFilter;
  const filtered = items.filter((r) => {
    if (filter === "all") return true;
    if (filter === "healthy") return r._label === "All good" || r._label === "Made progress";
    if (filter === "repair") return r._label === "Fixed a problem";
    if (filter === "blocked") return r._label === "Hit a problem";
    if (filter === "skipped") return r._label === "Skipped";
    return true;
  });
  const trend = data.continuity_trend || [];
  const tokens = data.token_trend || [];
  const maxT = Math.max(1, ...tokens.map((t) => t.tokens || 0));

  return `
  <div class="grid cols-2">
    <div class="card">
      <h3>Does it remember past work?</h3>
      <div class="spark">${trend.map((t) => `<span class="${t.continuity_influenced ? "ok" : ""}" style="height:${t.progress ? 80 : 28}%"></span>`).join("") || '<div class="empty">No continuity data yet.</div>'}</div>
    </div>
    <div class="card">
      <h3>Spending over time</h3>
      <div class="spark">${tokens.map((t) => `<span class="on" style="height:${Math.max(8, Math.round(100 * (t.tokens || 0) / maxT))}%"></span>`).join("") || '<div class="empty">No token data yet.</div>'}</div>
      <div class="muted" style="margin-top:8px">Month ${data.monthly?.monthly_token_usage || 0} / ${data.monthly?.monthly_token_ceiling || 0}</div>
    </div>
  </div>
  <div class="card" style="margin-top:14px">
    <h3>What happened recently</h3>
    <div class="row" style="margin-bottom:12px">
      ${[["all","All"],["healthy","All good"],["repair","Fixed"],["blocked","Problems"],["skipped","Skipped"]].map(([f, label]) =>
        `<button class="btn ${state.runFilter === f ? "primary" : ""}" data-filter="${f}">${label}</button>`
      ).join("")}
    </div>
    ${!items.length ? `<div class="empty">No runs yet.<div class="muted" style="margin-top:8px">Use <b>Run Now</b> on Home to start the first cycle. History and trends appear here after that.</div></div>` : filtered.length ? `<div class="timeline">${filtered.slice(0, 20).map((r) => `
      <div class="timeline-item">
        <div class="dot ${r._tone}"></div>
        <div class="timeline-card">
          <div class="row">
            <span class="chip ${r._tone || "purple"}">${r._label}</span>
            <span class="muted">${r.started_at || ""}</span>
          </div>
          <div style="margin-top:6px"><b>${r.plan_id || "No plan"}</b></div>
          <div class="muted">${r.success_criterion_id || r.selected_capability || r.outcome_class || "—"}</div>
        </div>
      </div>`).join("")}</div>` : `<div class="empty">No runs match this filter.</div>`}
  </div>`;
}

function renderGoals(data) {
  const criteria = data.criteria || [];
  const reopened = data.reopened_criteria || [];
  const top = data.top_unmet_criterion || {};
  const groups = {
    "Broke again": reopened.map((r) => ({
      id: r.id,
      display_name: r.id,
      status: "reopened",
      blocker_reason: r.detail || r.blocker_reason || r.work_class,
      next_required_capability_step: "Repair",
      evidence: [],
      text: r.regression_class || "",
    })),
    "In progress": criteria.filter((c) => c.status === "partial"),
    Stuck: criteria.filter((c) => c.status === "blocked" || c.status === "unmet"),
    Done: criteria.filter((c) => c.status === "complete"),
  };

  const pin = top.id
    ? `<div class="goal-card pinned">
        <div class="row"><span class="chip purple">Next</span><span class="chip warn">${statusLabel(top.status || "partial")}</span></div>
        <div style="margin-top:8px;font-size:18px;font-weight:700">${top.display_name || top.id}</div>
        <div class="muted" style="margin-top:6px">${top.unblock_condition || data.why_next_run || ""}</div>
      </div>`
    : `<div class="empty">All goals are done — nothing needs work right now.<div class="muted" style="margin-top:8px">Runs will only check that things still work, unless something breaks.</div></div>`;

  return `
  <div class="grid cols-2" style="margin-bottom:14px">
    <div class="card"><h3>Done</h3><div class="metric" style="color:var(--green)">${data.counts?.complete ?? 0}</div></div>
    <div class="card"><h3>Goals list is OK</h3><div class="metric sm" style="color:${data.ledger_intact ? "var(--green)" : "var(--red)"}">${data.ledger_intact ? "Yes" : "No"}</div></div>
  </div>
  ${pin}
  ${Object.entries(groups).map(([name, list]) => list.length ? `
    <div class="group">
      <h2>${name} (${list.length})</h2>
      ${list.map((c) => `
        <div class="goal-card">
          <div class="row">
            <b>${c.display_name || c.id}</b>
            <span class="chip ${c.status === "complete" ? "ok" : c.status === "reopened" || c.status === "blocked" || c.status === "unmet" ? "bad" : "warn"}">${statusLabel(c.status)}</span>
          </div>
          <div class="muted" style="margin-top:6px">${(c.text || "").slice(0, 110)}</div>
          ${c.blocker_reason ? `<div class="muted" style="margin-top:4px">Why it is stuck: ${c.blocker_reason}</div>` : ""}
          <div class="muted" style="margin-top:4px">What to do next: ${c.next_required_capability_step || "—"}</div>
          <button class="linkish" style="margin-top:6px" data-detail='${JSON.stringify({ title: c.display_name || c.id, evidence: c.evidence || [], text: c.text || "", unblock: c.unblock_condition || "" }).replace(/'/g, "&#39;")}'>More detail</button>
        </div>`).join("")}
    </div>` : "").join("") || `<div class="empty">No goal criteria loaded yet.<div class="muted" style="margin-top:8px">Open Advanced and use Refresh Goals, or check that project_goals.md is present.</div></div>`}`;
}

function renderSettings(schedule, budget, ov) {
  const runs = (schedule.runs || []).map((r) => r.at).join(", ");
  const policy = budget.policy || {};
  const s = operatorState(ov || {});
  return `
  <div class="card" style="margin-bottom:14px">
    <h3>Automatic runs</h3>
    <div class="hero-hint" style="margin:0 0 12px;font-size:16px">${s.autonomyOn ? "Automatic runs are on." : "Automatic runs are off."}</div>
    <div class="row">${pauseResumeBtn()} ${actionBtn("run-due")}</div>
  </div>
  <div class="grid cols-2">
    <div class="card">
      <h3>Schedule</h3>
      <div class="form" id="schedule-form">
        <div class="field"><label>Enabled</label>
          <select name="enabled"><option value="true" ${schedule.enabled ? "selected" : ""}>On</option><option value="false" ${!schedule.enabled ? "selected" : ""}>Off</option></select>
        </div>
        <div class="field"><label>Run times (HH:MM, comma-separated)</label><input name="runs" value="${runs}" /></div>
        <div class="field"><label>Max runs / day</label><input name="max_runs_per_day" type="number" min="0" max="48" value="${schedule.max_runs_per_day || 4}" /></div>
        <div class="field"><label>Monthly spending limit</label><input name="monthly_token_ceiling" type="number" min="1000" value="${schedule.monthly_token_ceiling || 500000}" /></div>
        <div class="err" id="schedule-errors"></div>
        <div class="applied" id="schedule-applied" hidden></div>
        <button class="btn primary" id="save-schedule">Save Schedule</button>
      </div>
    </div>
    <div class="card">
      <h3>Budget</h3>
      <div class="form" id="budget-form">
        <div class="field"><label>Cost mode</label>
          <select name="budget_mode">
            <option value="cheap_default" ${policy.budget_mode === "cheap_default" ? "selected" : ""}>Keeping costs low</option>
            <option value="balanced" ${policy.budget_mode === "balanced" ? "selected" : ""}>Balanced</option>
            <option value="aggressive" ${policy.budget_mode === "aggressive" ? "selected" : ""}>Aggressive</option>
          </select>
        </div>
        <div class="field"><label>Monthly spending limit</label><input name="monthly_token_ceiling" type="number" min="1000" value="${budget.monthly_token_ceiling || schedule.monthly_token_ceiling || 500000}" /></div>
        <div class="danger-box">
          <div class="field"><label>Allow expensive execution</label>
            <select name="allow_expensive_execution">
              <option value="false" ${!policy.allow_expensive_execution ? "selected" : ""}>Off (safe)</option>
              <option value="true" ${policy.allow_expensive_execution ? "selected" : ""}>On (dangerous)</option>
            </select>
          </div>
          <div class="muted" style="margin-top:6px">Turning this on can spend a lot more money.</div>
        </div>
        <div class="err" id="budget-errors"></div>
        <div class="applied" id="budget-applied" hidden></div>
        <button class="btn primary" id="save-budget">Save Budget</button>
      </div>
    </div>
  </div>`;
}

function renderAdvanced(data) {
  const gate = (state.overview || {}).release_gate || {};
  const r = data.regression_health || {};
  const cheap = data.cheap_default || {};
  const blocker = data.last_blocker || {};
  const arts = data.artifacts || {};
  const dog = data.ui_dogfood || {};
  const svc = (state.overview || {}).service || {};
  return `
  <div class="card" style="margin-bottom:14px">
    <h3>App status</h3>
    <div class="row">
      <span class="chip ${svc.state === "up" ? "ok" : "bad"}">${svc.state || "unknown"}</span>
      <span class="muted">${svc.ui_url || "http://127.0.0.1:8765/"}</span>
    </div>
    <div class="muted" style="margin-top:8px">Last problem: ${svc.last_failure || "none"}</div>
    <div class="row" style="margin-top:10px">${actionBtn("service-restart", "danger")} ${actionBtn("refresh")}</div>
  </div>
  <div class="grid cols-2">
    <div class="card">
      <h3>Ready checklist</h3>
      <div class="metric sm" style="color:${gate.release_ready ? "var(--green)" : "var(--amber)"}">${gate.release_ready ? "Ready" : "Not ready"}</div>
      <div class="row" style="margin-top:8px">${Object.entries(gate.gates || {}).map(([k, v]) => `<span class="chip ${v ? "ok" : "bad"}">${k}</span>`).join("") || '<span class="muted">—</span>'}</div>
    </div>
    <div class="card">
      <h3>Keeping costs low</h3>
      <div class="metric sm" style="color:${cheap.compliant ? "var(--green)" : "var(--red)"}">${cheap.compliant ? "On track" : "Off track"}</div>
      <div class="muted">${cheap.budget_mode === "cheap_default" ? "Keeping costs low" : cheap.budget_mode} · expensive jobs today ${cheap.worker_sessions_today || 0}</div>
    </div>
  </div>
  <div class="card" style="margin-top:14px">
    <h3>Technical details</h3>
    <div class="muted">Goals list is OK: <b>${r.ledger_intact ? "Yes" : "No"}</b></div>
    <div class="muted">Stopped because: <b>${blocker.stop_classification || "none"}</b> ${blocker.stop_reason || ""}</div>
    <div class="row" style="margin-top:10px">${actionBtn("self-check")} ${actionBtn("refresh-ledger")}</div>
  </div>
  <div class="card" style="margin-top:14px">
    <h3>Saved files</h3>
    <div class="row">${Object.entries(arts).map(([k, v]) => `<span class="chip ${v ? "ok" : "bad"}">${k}</span>`).join("")}</div>
    <div class="muted" style="margin-top:8px">UI ready: ${dog.ui_operator_ready ? "yes" : "no"} · sessions ${dog.sessions ?? "—"}</div>
  </div>
  <div class="card" style="margin-top:14px">
    <h3>What you did</h3>
    <div class="action-log" id="action-log"></div>
  </div>`;
}

function render() {
  const main = document.getElementById("main");
  if (state.route === "home") main.innerHTML = renderHome(state.overview || {});
  if (state.route === "runs") main.innerHTML = renderRuns(state.runs || {});
  if (state.route === "goals") main.innerHTML = renderGoals(state.ledger || {});
  if (state.route === "settings") main.innerHTML = renderSettings(state.schedule || {}, state.budget || {}, state.overview || {});
  if (state.route === "advanced") {
    main.innerHTML = renderAdvanced(state.diagnostics || {});
    renderActionLog();
  }
  bindDynamic();
}

function renderActionLog() {
  const el = document.getElementById("action-log");
  if (!el) return;
  if (!state.actionLog.length) {
    el.innerHTML = '<div class="empty">No actions yet. Primary controls live on Home.</div>';
    return;
  }
  el.innerHTML = state.actionLog.map((item) => `
    <div class="item ${item.result}">
      <div><b>${item.action}</b> · ${item.result}</div>
      <div class="muted">${item.ts}</div>
      <div class="muted">${item.command || "—"}</div>
      <div class="muted">${item.detail || ""}</div>
    </div>`).join("");
}

function validateScheduleForm(form) {
  const errors = [];
  const runs = form.runs.value.split(",").map((s) => s.trim()).filter(Boolean);
  for (const at of runs) {
    const m = /^(\d{1,2}):(\d{2})$/.exec(at);
    if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) errors.push("Invalid time: " + at);
  }
  const maxRuns = Number(form.max_runs_per_day.value);
  if (!Number.isFinite(maxRuns) || maxRuns < 0 || maxRuns > 48) errors.push("Max runs/day must be 0–48");
  const ceil = Number(form.monthly_token_ceiling.value);
  if (!Number.isFinite(ceil) || ceil < 1000) errors.push("Monthly ceiling must be ≥ 1000");
  return errors;
}

function validateBudgetForm(form) {
  const errors = [];
  if (!["cheap_default", "balanced", "aggressive"].includes(form.budget_mode.value)) errors.push("Invalid cost mode");
  const ceil = Number(form.monthly_token_ceiling.value);
  if (!Number.isFinite(ceil) || ceil < 1000) errors.push("Monthly ceiling must be ≥ 1000");
  return errors;
}

function bindDynamic() {
  document.querySelectorAll("[data-go]").forEach((btn) => {
    btn.onclick = () => go(btn.dataset.go);
  });
  document.querySelectorAll("[data-filter]").forEach((btn) => {
    btn.onclick = () => {
      state.runFilter = btn.dataset.filter;
      render();
    };
  });
  document.querySelectorAll("[data-detail]").forEach((btn) => {
    btn.onclick = () => {
      try {
        const d = JSON.parse(btn.dataset.detail);
        openDrawer(d.title || "Details", `
          <p>${d.text || ""}</p>
          <p class="muted">${d.unblock || ""}</p>
          <h4>Evidence</h4>
          <div class="row">${(d.evidence || []).map((e) => `<span class="chip">${e}</span>`).join("") || '<span class="muted">None</span>'}</div>`);
      } catch {}
    };
  });

  const saveSchedule = document.getElementById("save-schedule");
  if (saveSchedule) {
    saveSchedule.onclick = async () => {
      const form = document.getElementById("schedule-form");
      const errEl = document.getElementById("schedule-errors");
      const appliedEl = document.getElementById("schedule-applied");
      appliedEl.hidden = true;
      const errors = validateScheduleForm(form);
      if (errors.length) {
        errEl.textContent = errors.join(" · ");
        pushLog({ ts: new Date().toISOString(), action: "Save Schedule", result: "blocked", command: "validate", detail: errors.join("; ") });
        return;
      }
      errEl.textContent = "";
      const ok = await confirmAction("Save schedule?", "This changes when purple_halo may run.", true);
      if (!ok) return;
      const body = {
        enabled: form.enabled.value === "true",
        runs: form.runs.value.split(",").map((s) => s.trim()).filter(Boolean).map((at) => ({ at, label: "run" })),
        max_runs_per_day: Number(form.max_runs_per_day.value || 4),
        monthly_token_ceiling: Number(form.monthly_token_ceiling.value || 500000),
        mode: "self_product_mode",
      };
      await runConfigSave("Save Schedule", "/api/config/schedule", body, (res) => {
        const s = res.applied || res.schedule || {};
        appliedEl.textContent = `Applied: ${s.enabled ? "On" : "Off"}, max ${s.max_runs_per_day}/day, ${(s.runs || []).map((r) => r.at).join(", ")}`;
        appliedEl.hidden = false;
      });
    };
  }

  const saveBudget = document.getElementById("save-budget");
  if (saveBudget) {
    saveBudget.onclick = async () => {
      const form = document.getElementById("budget-form");
      const errEl = document.getElementById("budget-errors");
      const appliedEl = document.getElementById("budget-applied");
      appliedEl.hidden = true;
      const errors = validateBudgetForm(form);
      if (errors.length) {
        errEl.textContent = errors.join(" · ");
        pushLog({ ts: new Date().toISOString(), action: "Save Budget", result: "blocked", command: "validate", detail: errors.join("; ") });
        return;
      }
      errEl.textContent = "";
      const allowExp = form.allow_expensive_execution.value === "true";
      const mode = form.budget_mode.value;
      let msg = `Keep spending mode as “${mode === "cheap_default" ? "Keeping costs low" : mode}” with a monthly limit of ${form.monthly_token_ceiling.value}?`;
      if (allowExp) msg += " WARNING: expensive work will be allowed.";
      if (mode !== "cheap_default") msg += " WARNING: leaving Keeping costs low.";
      const ok = await confirmAction("Save budget?", msg, true);
      if (!ok) return;
      await runConfigSave("Save Budget", "/api/config/budget", {
        budget_mode: mode,
        allow_expensive_execution: allowExp,
        monthly_token_ceiling: Number(form.monthly_token_ceiling.value || 500000),
      }, (res) => {
        const p = (res.applied || res.budget || {}).policy || {};
        const ceil = (res.applied || res.budget || {}).monthly_token_ceiling;
        appliedEl.textContent = `Applied: ${p.budget_mode === "cheap_default" ? "Keeping costs low" : p.budget_mode}, expensive work=${p.allow_expensive_execution ? "on" : "off"}, monthly limit=${ceil}`;
        appliedEl.hidden = false;
      });
    };
  }
}

async function runConfigSave(actionName, path, body, onOk) {
  pushLog({ ts: new Date().toISOString(), action: actionName, result: "pending", command: path, detail: "saving" });
  try {
    const res = await api(path, { method: "POST", body: JSON.stringify(body) });
    if (res.success === false) {
      const detail = (res.errors || [res.summary || "failed"]).join("; ");
      pushLog({ ts: new Date().toISOString(), action: actionName, result: "failure", command: res.command || path, detail });
      toast(detail);
      return;
    }
    pushLog({ ts: new Date().toISOString(), action: actionName, result: "success", command: res.command || path, detail: res.summary || "saved" });
    toast(res.summary || "saved");
    if (onOk) onOk(res);
    await loadAll();
  } catch (err) {
    pushLog({ ts: new Date().toISOString(), action: actionName, result: "failure", command: path, detail: err.message || String(err) });
    toast(err.message || String(err));
  }
}

async function doAction(name) {
  const meta = ACTIONS[name];
  if (!meta) return;
  if ((state.actionState[name] || {}).state === "pending") return;

  if (name === "refresh") {
    setActionState(name, "pending");
    pushLog({ ts: new Date().toISOString(), action: meta.label, result: "pending", command: "GET status/*", detail: "" });
    try {
      await loadAll();
      setActionState(name, "success", "ok");
      pushLog({ ts: new Date().toISOString(), action: meta.label, result: "success", command: "GET status/*", detail: "refreshed" });
      toast("Refreshed");
    } catch (err) {
      setActionState(name, "failure", err.message || "error");
      pushLog({ ts: new Date().toISOString(), action: meta.label, result: "failure", command: "GET status/*", detail: err.message || String(err) });
      toast(err.message || String(err));
    }
    render();
    return;
  }

  if (meta.confirm) {
    setActionState(name, "confirmation-required");
    render();
    const ok = await confirmAction(meta.label, meta.confirm, meta.danger);
    if (!ok) {
      setActionState(name, "blocked", "cancelled");
      pushLog({ ts: new Date().toISOString(), action: meta.label, result: "blocked", command: meta.path, detail: "cancelled" });
      render();
      return;
    }
  }

  state.busyCount += 1;
  setActionState(name, "pending", "running");
  render();
  schedulePoll(true);
  pushLog({ ts: new Date().toISOString(), action: meta.label, result: "pending", command: meta.path, detail: "invoked" });
  try {
    const res = await api(meta.path, { method: "POST", body: "{}" });
    const success = res.success !== false;
    const detail = res.summary || (success ? "ok" : "failed");
    const command = res.command || meta.path;
    setActionState(name, success ? "success" : "failure", String(detail).slice(0, 48));
    pushLog({ ts: new Date().toISOString(), action: meta.label, result: success ? "success" : "failure", command, detail });
    toast(detail);
    await loadAll();
  } catch (err) {
    setActionState(name, "failure", err.message || "error");
    pushLog({ ts: new Date().toISOString(), action: meta.label, result: "failure", command: meta.path, detail: err.message || String(err) });
    toast(err.message || String(err));
  } finally {
    state.busyCount = Math.max(0, state.busyCount - 1);
    schedulePoll(false);
  }
}

async function loadAll() {
  const [overview, runs, ledger, diagnostics, schedule, budget] = await Promise.all([
    api("/api/status/overview"),
    api("/api/status/runs"),
    api("/api/status/goal-ledger"),
    api("/api/status/diagnostics"),
    api("/api/config/schedule"),
    api("/api/config/budget"),
  ]);
  state.overview = overview;
  state.runs = runs;
  state.ledger = ledger;
  state.diagnostics = diagnostics;
  state.schedule = schedule;
  state.budget = budget;
  renderTopStatus(overview);
  render();
}

function go(route) {
  state.route = route;
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.route === route));
  render();
}

function schedulePoll(fast) {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(() => {
    if (state.busyCount) return;
    loadAll().catch(() => {});
  }, fast || state.busyCount ? 2500 : 8000);
}

function bindGlobal() {
  document.querySelectorAll("#nav button").forEach((btn) => {
    btn.onclick = () => go(btn.dataset.route);
  });
  document.body.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-action]");
    if (btn) doAction(btn.dataset.action);
  });
  document.getElementById("drawer-close").onclick = closeDrawer;
  document.getElementById("drawer-bg").onclick = (ev) => {
    if (ev.target.id === "drawer-bg") closeDrawer();
  };
}

bindGlobal();
loadAll().catch((err) => {
  document.getElementById("main").innerHTML = `<div class="empty">Can't reach the app. Start it, then refresh this page.<div class="muted" style="margin-top:8px">${err.message || err}</div></div>`;
  document.getElementById("live-dot").className = "live-dot down";
});
schedulePoll(false);
