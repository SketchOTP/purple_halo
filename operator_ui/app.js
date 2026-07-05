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

function renderServiceLed(st) {
  const svcUp = /up|running/i.test(st.service_state || "");
  const svcLed = $("serviceLed");
  svcLed.className = "service-led " + (svcUp ? "up" : "down");
  svcLed.title = svcUp ? "Service running" : "Service not running";
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
  lines.forEach((line) => {
    const bar = document.createElement("div");
    const fail = /fail|error|block/i.test(line);
    bar.className = "run-bar" + (fail ? " fail" : "");
    bar.style.height = `${Math.min(24 + line.length * 0.5, 100)}%`;
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

function render(st) {
  const repo = st.repo || "";

  if (st.every_hours != null) $("everyHours").value = st.every_hours;
  if (st.for_days != null) $("forDays").value = st.for_days;
  $("untilGoal").checked = !!st.until_goal_achieved;

  if (st.goal_source) $("goalPath").value = st.goal_source;
  $("goalPreview").textContent = st.goal_preview || "";

  renderServiceLed(st);

  const runCount = st.run_count || 0;
  $("reportMeta").textContent = runCount ? `${runCount} runs` : "";
  renderRunChart(st.report);
  $("report").textContent = st.report || "";
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
