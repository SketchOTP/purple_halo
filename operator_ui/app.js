/* purple_halo — primary operator product UI */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
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

function flash(msg, isErr) {
  const el = $("flash");
  el.hidden = !msg;
  el.textContent = msg || "";
  el.classList.toggle("err", !!isErr);
}

function setBusy(busy) {
  document.querySelectorAll("button").forEach((b) => {
    b.disabled = busy;
  });
}

function serviceLabel(st) {
  const state = (st.service_state || "unknown").toLowerCase();
  const unit = st.service_unit || "purple-halo-operator.service";
  const labels = { up: "Running", running: "Running", down: "Stopped", failed: "Failed", restarting: "Restarting" };
  return `${labels[state] || state} · ${unit}`;
}

function formatLastRun(last) {
  if (!last || typeof last !== "object" || !Object.keys(last).length) return "";
  const when = last.finished_at || last.started_at || "";
  const status = last.status || last.result || "";
  const err = last.error || "";
  return `Last run: ${[when, status, err].filter(Boolean).join(" · ")}`;
}

function setStepState(id, ready) {
  const el = $(id);
  if (!el) return;
  el.classList.toggle("ready", !!ready);
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
  if (install.repo) lines.push(`repo: ${install.repo}`);
  if (install.ui_url) lines.push(`ui: ${install.ui_url}`);
  if (install.port) lines.push(`port: ${install.port}`);
  if (install.unit) lines.push(`service: ${install.unit}`);
  if (result.stdout) lines.push("", result.stdout.trim());
  details.textContent = lines.join("\n") || "(no install details)";
}

function render(st) {
  const repo = st.repo || "";
  $("thisRepo").value = repo;
  $("instanceRepo").textContent = st.repo_name ? `${st.repo_name} — ${repo}` : repo || "(unknown)";
  const uiUrl = st.ui_url || window.location.origin + "/";
  const urlEl = $("instanceUrl");
  urlEl.href = uiUrl;
  urlEl.textContent = uiUrl;
  $("instanceService").textContent = serviceLabel(st);
  $("instanceService").className = "instance-value " + ((st.service_state || "").match(/up|running/i) ? "ok" : "warn");
  $("instanceReport").textContent = st.report_path || "RUN_REPORT.md";

  $("badge").textContent = st.playing ? "Playing" : st.campaign_stop_reason ? "Stopped" : "Paused";
  $("badge").className = "badge " + (st.playing ? "on" : st.campaign_stop_reason ? "stopped" : "off");

  if (st.every_hours != null) $("everyHours").value = st.every_hours;
  if (st.for_days != null) $("forDays").value = st.for_days;
  $("untilGoal").checked = !!st.until_goal_achieved;

  if (st.goal_source) $("goalPath").value = st.goal_source;
  $("goalPreview").textContent = st.goal_preview || "(no goal file yet — add project_goals.md or import a goal)";

  const bits = [];
  bits.push(st.playing ? "Auto-run is on" : "Auto-run is off");
  if (st.every_hours) bits.push(`every ${st.every_hours}h`);
  if (st.for_days != null) bits.push(`for ${st.for_days} days`);
  if (st.until_goal_achieved) bits.push("or until goal");
  if (st.campaign_started_at) bits.push(`started ${st.campaign_started_at}`);
  if (st.campaign_stop_reason) bits.push(`stopped: ${st.campaign_stop_reason}`);
  $("statusLine").textContent = bits.join(" · ");
  $("lastRunLine").textContent = formatLastRun(st.last_run);

  const runCount = st.run_count || 0;
  $("reportMeta").textContent = runCount
    ? `${runCount} run${runCount === 1 ? "" : "s"} recorded`
    : "No runs recorded yet";
  $("report").textContent = st.report || "(no report yet — press Play or Run once now)";
  $("report").scrollTop = $("report").scrollHeight;

  setStepState("stepRepo", !!repo);
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
    if (result && result.message) flash(result.message, !result.ok && result.ok !== undefined);
    if (result && result.ok === false) flash(result.error || result.summary || "Failed", true);
    if (result && result.install) renderInstallResult(result);
    return result;
  } catch (err) {
    flash(err.message || String(err), true);
    try {
      await refresh();
    } catch (_) {
      /* ignore */
    }
  } finally {
    setBusy(false);
  }
}

function bind() {
  $("btnFrequency").onclick = () =>
    withAction(() =>
      api("/api/simple/frequency", {
        method: "POST",
        body: JSON.stringify({
          every: `${$("everyHours").value}h`,
          for_days: Number($("forDays").value),
          until_goal: $("untilGoal").checked,
        }),
      })
    );

  $("btnGoal").onclick = () =>
    withAction(() =>
      api("/api/simple/goal", {
        method: "POST",
        body: JSON.stringify({ path: $("goalPath").value.trim() }),
      })
    );

  $("btnPlay").onclick = () => withAction(() => api("/api/simple/play", { method: "POST", body: "{}" }));
  $("btnPause").onclick = () => withAction(() => api("/api/simple/pause", { method: "POST", body: "{}" }));
  $("btnRunOnce").onclick = () => withAction(() => api("/api/simple/run-now", { method: "POST", body: "{}" }));

  $("btnInstall").onclick = () =>
    withAction(async () => {
      const repo = $("installRepo").value.trim();
      const goal = $("goalPath").value.trim();
      const result = await api("/api/simple/install", {
        method: "POST",
        body: JSON.stringify({ repo, goal: goal || undefined }),
      });
      renderInstallResult(result);
      return result;
    });
}

bind();
refresh().catch((err) => flash(err.message || String(err), true));
setInterval(() => {
  refresh().catch(() => {});
}, 15000);