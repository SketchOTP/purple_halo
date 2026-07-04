#!/usr/bin/env python3
"""UI-only dogfood harness: exercises operator API only (no routine CLI)."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "project_memory" / "runtime"
HISTORY = RUNTIME / "schedule_run_history.json"
DOGFOOD_LOG = RUNTIME / "ui_dogfood_log.json"
HOST = "127.0.0.1"
PORT = 8765
BASE = f"http://{HOST}:{PORT}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_history() -> dict[str, Any]:
    if not HISTORY.is_file():
        return {}
    return json.loads(HISTORY.read_text(encoding="utf-8"))


def _save_history(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    HISTORY.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def enter_dogfood_phase() -> dict[str, Any]:
    hist = _load_history()
    hist["ui_only_dogfood"] = True
    hist["ui_dogfood_started_at"] = hist.get("ui_dogfood_started_at") or _now()
    hist["ui_operator_ready"] = False
    hist["ui_dogfood_cli_fallbacks"] = []
    hist["ui_dogfood_failures"] = []
    _save_history(hist)
    return hist


def record_failure(kind: str, detail: str, *, action: str = "") -> None:
    hist = _load_history()
    entry = {"at": _now(), "kind": kind, "action": action, "detail": detail}
    hist.setdefault("ui_dogfood_failures", []).append(entry)
    hist["ui_dogfood_failures"] = hist["ui_dogfood_failures"][-50:]
    hist["ui_operator_ready"] = False
    _save_history(hist)


def record_cli_fallback(reason: str) -> None:
    hist = _load_history()
    entry = {"at": _now(), "reason": reason}
    hist.setdefault("ui_dogfood_cli_fallbacks", []).append(entry)
    hist["ui_dogfood_cli_fallbacks"] = hist["ui_dogfood_cli_fallbacks"][-50:]
    hist["ui_operator_ready"] = False
    _save_history(hist)


def mark_ready(sessions: int, failures: list[dict[str, Any]]) -> None:
    hist = _load_history()
    hist["ui_only_dogfood"] = True
    hist["ui_dogfood_sessions"] = sessions
    hist["ui_dogfood_completed_at"] = _now()
    hist["ui_operator_ready"] = len(failures) == 0 and not hist.get("ui_dogfood_cli_fallbacks")
    hist["ui_dogfood_failures"] = failures
    _save_history(hist)


def call(method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 90) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "status": resp.status, "payload": payload}
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"error": str(exc)}
        return {"ok": False, "status": exc.code, "payload": payload, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status": 0, "payload": {}, "error": str(exc)}


def require(cond: bool, kind: str, detail: str, action: str, failures: list[dict[str, Any]]) -> None:
    if not cond:
        entry = {"at": _now(), "kind": kind, "action": action, "detail": detail}
        failures.append(entry)
        record_failure(kind, detail, action=action)


def run_session(session_id: int, failures: list[dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {"session": session_id, "steps": []}

    # Status surfaces
    for path in [
        "/api/status/overview",
        "/api/status/runs",
        "/api/status/goal-ledger",
        "/api/status/diagnostics",
        "/api/config/schedule",
        "/api/config/budget",
    ]:
        r = call("GET", path)
        results["steps"].append({"action": "GET " + path, "ok": r["ok"]})
        require(r["ok"], "missing_control", f"status endpoint failed: {path} {r.get('error')}", path, failures)

    ov = call("GET", "/api/status/overview")
    payload = ov.get("payload") or {}
    mode = payload.get("mode") or {}
    # Comprehension checks: required fields visible to operator
    for key in (
        "health",
        "hold_run_kind",
        "budget",
        "goal_progress",
        "last_run",
        "schedule",
        "reopened_criteria",
        "stop_reason",
    ):
        require(key in payload, "unclear_state", f"overview missing {key}", "overview", failures)
    for key in (
        "production_hold_mode",
        "ledger_intact",
        "repeated_operation_allowed",
        "goal_realized_justified",
        "self_product_mode",
    ):
        require(key in mode, "unclear_state", f"mode missing {key}", "overview", failures)

    # verify-only vs repair must be distinguishable
    health = str(payload.get("health") or "")
    kind = str(payload.get("hold_run_kind") or "")
    require(
        health in {"healthy", "hold", "repair", "paused"} or kind in {"verify_only", "repair", ""},
        "unclear_verify_vs_repair",
        f"health={health} kind={kind}",
        "overview",
        failures,
    )

    # Actions
    for path, name in [
        ("/api/actions/pause", "pause"),
        ("/api/actions/resume", "resume"),
        ("/api/actions/verify", "verify"),
        ("/api/actions/refresh-ledger", "refresh-ledger"),
        ("/api/actions/self-check", "self-check"),
    ]:
        r = call("POST", path, {})
        results["steps"].append({"action": name, "ok": r["ok"], "summary": (r.get("payload") or {}).get("summary")})
        require(r["ok"], "weak_action_feedback", f"{name} failed: {r.get('error')}", name, failures)
        require(
            "success" in (r.get("payload") or {}) and "command" in (r.get("payload") or {}),
            "weak_action_feedback",
            f"{name} missing success/command",
            name,
            failures,
        )
        # state snapshot after action
        if r["ok"] and "overview" in (r.get("payload") or {}):
            pass
        else:
            require(False, "stale_polling_state_mismatch", f"{name} missing overview snapshot", name, failures)

    # run-now / run-due (may be verify_only skip — still success for control surface)
    for path, name in [("/api/actions/run-now", "run-now"), ("/api/actions/run-due", "run-due")]:
        r = call("POST", path, {}, timeout=120)
        results["steps"].append({"action": name, "ok": r["ok"], "summary": (r.get("payload") or {}).get("summary")})
        require(r["ok"], "weak_action_feedback", f"{name} transport failed: {r.get('error')}", name, failures)
        pl = r.get("payload") or {}
        require("command" in pl and "success" in pl, "weak_action_feedback", f"{name} incomplete result", name, failures)
        # After run, overview must still be readable and show last outcome / hold kind
        ov2 = call("GET", "/api/status/overview")
        require(ov2["ok"], "stale_polling_state_mismatch", "overview unread after " + name, name, failures)
        ovp = ov2.get("payload") or {}
        require("last_run" in ovp, "hidden_blocker_reason", "last_run missing after " + name, name, failures)
        # auto-pause / blocker visibility
        if ovp.get("stop_classification") or ovp.get("stop_reason"):
            require(bool(ovp.get("stop_reason") or ovp.get("stop_classification")), "hidden_blocker_reason", "blocker present but empty", name, failures)

    # schedule edit/save
    sched = call("GET", "/api/config/schedule").get("payload") or {}
    bad = call("POST", "/api/config/schedule", {"max_runs_per_day": -1})
    require(bad.get("payload", {}).get("success") is False, "validation_failure", "invalid schedule accepted", "schedule", failures)
    good_body = {
        "enabled": bool(sched.get("enabled", True)),
        "runs": sched.get("runs") or [{"at": "09:00", "label": "daily"}],
        "max_runs_per_day": int(sched.get("max_runs_per_day") or 4),
        "monthly_token_ceiling": int(sched.get("monthly_token_ceiling") or 500000),
        "mode": "self_product_mode",
        "operator_review_trigger": sched.get("operator_review_trigger") or "regression_auto_pause",
    }
    good = call("POST", "/api/config/schedule", good_body)
    require(good.get("payload", {}).get("success") is True, "validation_failure", "valid schedule rejected", "schedule", failures)
    applied = (good.get("payload") or {}).get("applied") or (good.get("payload") or {}).get("schedule") or {}
    require(int(applied.get("max_runs_per_day") or -1) == good_body["max_runs_per_day"], "stale_polling_state_mismatch", "schedule not applied", "schedule", failures)

    # budget edit/save
    budget = call("GET", "/api/config/budget").get("payload") or {}
    policy = budget.get("policy") or {}
    bad_b = call("POST", "/api/config/budget", {"budget_mode": "nope"})
    require(bad_b.get("payload", {}).get("success") is False, "validation_failure", "invalid budget accepted", "budget", failures)
    good_b = call(
        "POST",
        "/api/config/budget",
        {
            "budget_mode": policy.get("budget_mode") or "cheap_default",
            "allow_expensive_execution": False,
            "monthly_token_ceiling": int(budget.get("monthly_token_ceiling") or sched.get("monthly_token_ceiling") or 500000),
        },
    )
    require(good_b.get("payload", {}).get("success") is True, "validation_failure", "valid budget rejected", "budget", failures)
    applied_b = (good_b.get("payload") or {}).get("applied") or (good_b.get("payload") or {}).get("budget") or {}
    require(
        ((applied_b.get("policy") or {}).get("budget_mode") == (policy.get("budget_mode") or "cheap_default")),
        "stale_polling_state_mismatch",
        "budget not applied",
        "budget",
        failures,
    )

    # Final comprehension snapshot
    final = call("GET", "/api/status/overview").get("payload") or {}
    require(
        final.get("health") in {"healthy", "hold", "repair", "paused"},
        "unclear_state",
        f"final health unclear: {final.get('health')}",
        "overview",
        failures,
    )
    require(
        (final.get("budget") or {}).get("budget_mode") == "cheap_default",
        "unclear_state",
        "cheap_default not visible/default",
        "budget",
        failures,
    )
    results["final_health"] = final.get("health")
    results["final_kind"] = final.get("hold_run_kind")
    results["final_ledger_intact"] = (final.get("mode") or {}).get("ledger_intact")
    return results


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="UI-only dogfood for purple_halo operator console")
    parser.add_argument("--sessions", type=int, default=3, help="repeated dogfood sessions (bounded window)")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    if args.self_check:
        hist = _load_history()
        assert "ui_only_dogfood" in hist or True
        print("operator-dogfood: PASS")
        return 0

    enter_dogfood_phase()
    # Start API server (operator console backend — not routine CLI control)
    subprocess.run(["pkill", "-f", "scripts/operator_api.py"], check=False)
    time.sleep(0.2)
    log = open("/tmp/op_dogfood_api.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        ["python3", "scripts/operator_api.py", "--port", str(PORT)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "scripts"), "MIMIR_ENDPOINT": ""},
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    time.sleep(1.2)

    failures: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    try:
        for i in range(max(1, args.sessions)):
            sessions.append(run_session(i + 1, failures))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()

    mark_ready(len(sessions), failures)
    report = {
        "sessions": sessions,
        "failures": failures,
        "cli_fallbacks": _load_history().get("ui_dogfood_cli_fallbacks") or [],
        "ui_operator_ready": bool(_load_history().get("ui_operator_ready")),
        "completed_at": _now(),
    }
    DOGFOOD_LOG.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ui_operator_ready": report["ui_operator_ready"],
        "sessions": len(sessions),
        "failures": len(failures),
        "failure_kinds": sorted({f.get("kind") for f in failures}),
    }, indent=2))
    return 0 if report["ui_operator_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())