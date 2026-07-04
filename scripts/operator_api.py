#!/usr/bin/env python3
"""Thin local operator API for purple_halo. Runtime stays authoritative."""

from __future__ import annotations

import json
import subprocess
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "project_memory" / "runtime"
UI_DIR = ROOT / "operator_ui"
SCRIPTS = ROOT / "scripts"
HOST = "127.0.0.1"
PORT = 8765


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _run_py(args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    cmd = [sys.executable, *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**dict(__import__("os").environ), "PYTHONPATH": str(SCRIPTS), "MIMIR_ENDPOINT": ""},
        )
        stdout = (proc.stdout or "").strip()
        payload: Any = None
        if stdout.startswith("{") or stdout.startswith("["):
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                payload = None
        return {
            "ok": proc.returncode == 0,
            "command": " ".join(cmd),
            "returncode": proc.returncode,
            "stdout": stdout[-4000:],
            "stderr": (proc.stderr or "")[-2000:],
            "payload": payload,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": " ".join(cmd), "error": "timeout", "returncode": -1}
    except Exception as exc:
        return {"ok": False, "command": " ".join(cmd), "error": str(exc), "returncode": -1}


def _import_status() -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPTS))
    from loop_autonomous import autonomous_status, load_autonomous_history, load_schedule_config
    from loop_cost_policy import budget_status, load_accounting, load_policy, monthly_token_status
    from loop_goal_delivery import goal_delivery_status, refresh_ledger
    from loop_production_hold import ensure_production_hold_mode, evaluate_hold_run, hold_status_fields

    ensure_production_hold_mode()
    auto = autonomous_status()
    hold = hold_status_fields()
    hold_eval = evaluate_hold_run()
    goal = goal_delivery_status()
    schedule = load_schedule_config()
    policy = load_policy()
    accounting = load_accounting()
    monthly = monthly_token_status(ceiling=int(schedule.get("monthly_token_ceiling") or 500_000))
    history = load_autonomous_history()
    continuity = _load_json(RUNTIME / "continuity_state.json")
    open_gaps = _load_json(RUNTIME / "open_gaps_state.json")
    verification_brief = _load_json(RUNTIME / "verification_brief.json")
    goal_model = _load_json(RUNTIME / "goal_model.json")
    ledger = refresh_ledger()
    return {
        "auto": auto,
        "hold": hold,
        "hold_eval": hold_eval,
        "goal": goal,
        "schedule": schedule,
        "policy": policy,
        "accounting": accounting,
        "monthly": monthly,
        "history": history,
        "continuity": continuity,
        "open_gaps": open_gaps,
        "verification_brief": verification_brief,
        "goal_model": goal_model,
        "ledger": ledger,
        "budget": budget_status(),
    }


def overview() -> dict[str, Any]:
    s = _import_status()
    auto, hold, goal, schedule, monthly = s["auto"], s["hold"], s["goal"], s["schedule"], s["monthly"]
    history = s["history"]
    seq = list(history.get("sequence") or [])
    hold_runs = list(history.get("hold_run_results") or [])
    last_hold = hold_runs[-1] if hold_runs else {}
    last = history.get("last_run") or last_hold or {}
    counts = (goal.get("goal_delivery_ledger") or {}).get("counts") or s["ledger"].get("counts") or {}
    complete = int(counts.get("complete") or 0)
    total = sum(int(counts.get(k) or 0) for k in ("complete", "partial", "unmet", "blocked")) or 12
    health = "healthy"
    if not hold.get("ledger_intact", True) or hold.get("hold_run_kind") == "repair":
        health = "repair"
    elif hold.get("production_hold_mode") and hold.get("hold_run_kind") in {"verify_only", ""}:
        health = "hold"
    elif not auto.get("repeated_operation_allowed", True):
        health = "paused"
    regressions = hold.get("hold_regressions") or hold.get("reopened_criteria") or []
    return {
        "mode": {
            "self_product_mode": str(schedule.get("mode") or "self_product_mode") == "self_product_mode",
            "production_candidate_operations": bool(auto.get("production_candidate") or history.get("production_candidate_operations")),
            "production_hold_mode": bool(hold.get("production_hold_mode")),
            "goal_delivery_mode": bool(goal.get("goal_delivery_mode") or history.get("goal_delivery_mode")),
            "live_soak_mode": bool(auto.get("live_soak_mode") or history.get("live_soak_mode")),
            "live_soak_passed": bool(auto.get("live_soak_passed") or history.get("live_soak_passed")),
            "mechanics_complete": bool(auto.get("mechanics_complete")),
            "operationally_realized": bool(auto.get("operationally_realized")),
            "goal_realized": bool(auto.get("goal_realized")),
            "goal_realized_justified": bool(goal.get("goal_realized_justified")),
            "ledger_intact": bool(hold.get("ledger_intact", True)),
            "repeated_operation_allowed": bool(auto.get("repeated_operation_allowed", True)),
            "long_run_mode": bool(auto.get("long_run_mode") or history.get("long_run_mode")),
            "ui_only_dogfood": bool(history.get("ui_only_dogfood")),
            "ui_operator_ready": bool(history.get("ui_operator_ready")),
            "service_soak_passed": bool(history.get("service_soak_passed")),
            "local_production_ready": bool(history.get("local_production_ready")),
            "production_freeze_mode": bool(history.get("production_freeze_mode")),
            "release_ready": bool(history.get("release_ready")) if "release_ready" in history else None,
        },
        "health": health,
        "hold_run_kind": hold.get("hold_run_kind") or hold.get("last_hold_run_kind") or "",
        "hold_why": hold.get("hold_why") or "",
        "reopened_criteria": hold.get("reopened_criteria") or [],
        "regressions": regressions,
        "schedule": {
            "enabled": bool(schedule.get("enabled")),
            "runs": schedule.get("runs") or [],
            "max_runs_per_day": int(schedule.get("max_runs_per_day") or 0),
            "timezone": schedule.get("timezone") or "UTC",
        },
        "budget": {
            "budget_mode": s["policy"].get("budget_mode") or "cheap_default",
            "allow_expensive_execution": bool(s["policy"].get("allow_expensive_execution")),
            "monthly_token_usage": monthly.get("monthly_token_usage"),
            "monthly_token_ceiling": monthly.get("monthly_token_ceiling"),
            "remaining": monthly.get("remaining"),
            "at_ceiling": monthly.get("at_ceiling"),
            "today_tokens": int(s["accounting"].get("estimated_token_cost") or 0),
            "worker_sessions_today": int(s["accounting"].get("worker_session_count") or 0),
        },
        "goal_progress": {
            "complete": complete,
            "total": total,
            "pct": round(100.0 * complete / max(1, total), 1),
            "top_unmet": goal.get("top_unmet_criterion") or {},
            "why_next_run": goal.get("why_next_run") or auto.get("next_planned_run_reason") or "",
            "remaining_partial": goal.get("remaining_partial") or [],
        },
        "last_run": {
            "classification": last.get("outcome_class") or last_hold.get("outcome_class") or last.get("classification") or "",
            "run_kind": last_hold.get("run_kind") or history.get("last_hold_run_kind") or "",
            "plan_id": last.get("plan_id") or last_hold.get("plan_id") or "",
            "selected_capability": last.get("selected_capability") or "",
            "success_criterion_id": last.get("success_criterion_id") or last_hold.get("success_criterion_id") or "",
            "meaningful_progress": bool(last.get("meaningful_product_progress") or last_hold.get("meaningful_progress")),
            "continuity_influenced": bool(last.get("continuity_influenced") or last_hold.get("continuity_influenced")),
            "worker_used": bool(last.get("worker_used") or last_hold.get("worker_used")),
            "cheap_default_respected": not bool(last.get("worker_used") or last_hold.get("worker_used")),
            "blocked_classification": last.get("blocked_classification") or "",
            "started_at": last.get("started_at") or last_hold.get("started_at") or "",
            "why_run": last.get("why_run") or "",
        },
        "sequence_count": len(seq),
        "stop_classification": history.get("stop_classification") or "",
        "stop_reason": history.get("stop_reason") or "",
    }


def runs_status() -> dict[str, Any]:
    s = _import_status()
    history = s["history"]
    sequence = list(history.get("sequence") or [])[-30:]
    hold_runs = list(history.get("hold_run_results") or [])[-30:]
    delivery = list(history.get("goal_delivery_results") or [])[-30:]
    # token trend from sequence / accounting cycles if present
    acct = s["accounting"]
    cycles = list(acct.get("cycles") or [])[-20:]
    token_trend = [
        {"cycle_id": c.get("cycle_id"), "tokens": int(c.get("estimated_token_cost") or 0)}
        for c in cycles
        if isinstance(c, dict)
    ]
    continuity_trend = [
        {
            "cycle_id": r.get("cycle_id"),
            "continuity_influenced": bool(r.get("continuity_influenced")),
            "progress": bool(r.get("meaningful_product_progress") or r.get("meaningful_progress")),
            "outcome": r.get("outcome_class") or "",
        }
        for r in sequence[-20:]
    ]
    outcomes = {}
    for r in sequence[-20:]:
        oc = str(r.get("outcome_class") or "unknown")
        outcomes[oc] = outcomes.get(oc, 0) + 1
    return {
        "sequence": sequence,
        "hold_runs": hold_runs,
        "delivery_results": delivery,
        "token_trend": token_trend,
        "continuity_trend": continuity_trend,
        "outcome_counts": outcomes,
        "monthly": s["monthly"],
    }


def goal_ledger_status() -> dict[str, Any]:
    s = _import_status()
    ledger = s["ledger"]
    hold = s["hold"]
    return {
        "counts": ledger.get("counts") or {},
        "criteria": ledger.get("criteria") or [],
        "remaining_partial": ledger.get("remaining_partial") or [],
        "top_unmet_criterion": ledger.get("top_unmet_criterion") or {},
        "why_next_run": ledger.get("why_next_run") or "",
        "all_criteria_complete": bool(ledger.get("all_criteria_complete")),
        "reopened_criteria": hold.get("reopened_criteria") or [],
        "ledger_intact": bool(hold.get("ledger_intact", True)),
        "core_focus_order": ledger.get("core_focus_order") or [],
        "goal_model": {
            "present": bool(s["goal_model"]),
            "keys": list(s["goal_model"].keys())[:12],
        },
        "open_gaps_count": len(s["open_gaps"].get("open_gaps") or []),
        "verification_brief_present": bool(s["verification_brief"]),
    }


def diagnostics_status() -> dict[str, Any]:
    s = _import_status()
    hold = s["hold"]
    continuity = s["continuity"]
    policy = s["policy"]
    history = s["history"]
    artifacts = {
        "goal_delivery_ledger": (RUNTIME / "goal_delivery_ledger.json").is_file(),
        "goal_model": (RUNTIME / "goal_model.json").is_file(),
        "verification_brief": (RUNTIME / "verification_brief.json").is_file(),
        "open_gaps_state": (RUNTIME / "open_gaps_state.json").is_file(),
        "continuity_state": (RUNTIME / "continuity_state.json").is_file(),
        "cost_policy": (RUNTIME / "cost_policy.json").is_file(),
        "cost_accounting": (RUNTIME / "cost_accounting.json").is_file(),
        "schedule": (RUNTIME / "schedule.json").is_file(),
        "schedule_run_history": (RUNTIME / "schedule_run_history.json").is_file(),
        "production_hold_state": (RUNTIME / "production_hold_state.json").is_file(),
    }
    return {
        "regression_health": {
            "ledger_intact": hold.get("ledger_intact", True),
            "reopened_criteria": hold.get("reopened_criteria") or [],
            "regressions": hold.get("hold_regressions") or [],
            "hold_run_kind": hold.get("hold_run_kind") or "",
            "hold_why": hold.get("hold_why") or "",
        },
        "continuity_health": {
            "present": bool(continuity),
            "freshness": continuity.get("freshness") or "",
            "active_gap_focus": continuity.get("active_gap_focus") or {},
            "resumed_prior_intent": continuity.get("resumed_prior_intent"),
        },
        "cheap_default": {
            "budget_mode": policy.get("budget_mode"),
            "allow_expensive_execution": bool(policy.get("allow_expensive_execution")),
            "worker_sessions_today": int(s["accounting"].get("worker_session_count") or 0),
            "compliant": str(policy.get("budget_mode")) == "cheap_default"
            and not policy.get("allow_expensive_execution"),
        },
        "last_blocker": {
            "stop_classification": history.get("stop_classification") or "",
            "stop_reason": history.get("stop_reason") or "",
            "auto_pause_reason": history.get("auto_pause_reason") or "",
        },
        "artifacts": artifacts,
        "monthly": s["monthly"],
        "schedule": s["schedule"],
        "ui_dogfood": {
            "ui_only_dogfood": bool(history.get("ui_only_dogfood")),
            "ui_operator_ready": bool(history.get("ui_operator_ready")),
            "sessions": history.get("ui_dogfood_sessions"),
            "failures": list(history.get("ui_dogfood_failures") or [])[-10:],
            "cli_fallbacks": list(history.get("ui_dogfood_cli_fallbacks") or [])[-10:],
            "completed_at": history.get("ui_dogfood_completed_at") or "",
        },
    }


def action_run_now() -> dict[str, Any]:
    result = _run_py(["scripts/loop_schedule.py", "--run-now"], timeout=180)
    return {"success": result["ok"], "command": result["command"], "summary": result.get("stdout", "")[:500], "result": result, "overview": overview()}


def action_run_due() -> dict[str, Any]:
    result = _run_py(["scripts/loop_schedule.py", "--run-due"], timeout=180)
    return {"success": result["ok"], "command": result["command"], "summary": result.get("stdout", "")[:500], "result": result, "overview": overview()}


def action_pause() -> dict[str, Any]:
    history = _load_json(RUNTIME / "schedule_run_history.json")
    history["autonomous_allowed"] = False
    history["stop_classification"] = "operator_pause"
    history["stop_reason"] = "paused from operator console"
    _save_json(RUNTIME / "schedule_run_history.json", history)
    return {"success": True, "command": "history.autonomous_allowed=false", "summary": "autonomy paused", "overview": overview()}


def action_resume() -> dict[str, Any]:
    history = _load_json(RUNTIME / "schedule_run_history.json")
    history["autonomous_allowed"] = True
    if history.get("stop_classification") == "operator_pause":
        history["stop_classification"] = ""
        history["stop_reason"] = ""
    _save_json(RUNTIME / "schedule_run_history.json", history)
    return {"success": True, "command": "history.autonomous_allowed=true", "summary": "autonomy resumed", "overview": overview()}


def action_verify() -> dict[str, Any]:
    result = _run_py(["scripts/loop_production_hold.py", "--evaluate"])
    return {"success": result["ok"], "command": result["command"], "summary": result.get("stdout", "")[:800], "result": result, "overview": overview()}


def action_refresh_ledger() -> dict[str, Any]:
    result = _run_py(["scripts/loop_goal_delivery.py", "--refresh"])
    return {"success": result["ok"], "command": result["command"], "summary": "ledger refreshed", "result": result, "overview": overview()}


def action_self_check() -> dict[str, Any]:
    checks = [
        ["scripts/loop_production_hold.py", "--self-check"],
        ["scripts/loop_goal_delivery.py", "--self-check"],
        ["scripts/loop_autonomous.py", "--self-check"],
        ["scripts/loop_schedule.py", "--self-check"],
        ["scripts/loop_cost_policy.py", "--self-check"],
    ]
    results = []
    all_ok = True
    for args in checks:
        r = _run_py(args, timeout=60)
        results.append({"script": args[0], "ok": r["ok"], "stdout": r.get("stdout", "")[-200:]})
        all_ok = all_ok and r["ok"]
    return {"success": all_ok, "command": "self-check suite", "summary": "pass" if all_ok else "fail", "results": results, "overview": overview()}


def get_schedule() -> dict[str, Any]:
    path = RUNTIME / "schedule.json"
    data = _load_json(path) or _load_json(RUNTIME / "schedule.default.json")
    return data


def _validate_hhmm(value: str) -> bool:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        return False
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= hh <= 23 and 0 <= mm <= 59


def post_schedule(body: dict[str, Any]) -> dict[str, Any]:
    path = RUNTIME / "schedule.json"
    current = get_schedule()
    errors: list[str] = []
    if "enabled" in body and not isinstance(body["enabled"], bool):
        if str(body["enabled"]).lower() in {"true", "1", "yes"}:
            body["enabled"] = True
        elif str(body["enabled"]).lower() in {"false", "0", "no"}:
            body["enabled"] = False
        else:
            errors.append("enabled must be boolean")
    if "max_runs_per_day" in body:
        try:
            mr = int(body["max_runs_per_day"])
            if mr < 0 or mr > 48:
                errors.append("max_runs_per_day must be 0..48")
            body["max_runs_per_day"] = mr
        except (TypeError, ValueError):
            errors.append("max_runs_per_day must be an integer")
    if "monthly_token_ceiling" in body:
        try:
            ceil = int(body["monthly_token_ceiling"])
            if ceil < 1000:
                errors.append("monthly_token_ceiling must be >= 1000")
            body["monthly_token_ceiling"] = ceil
        except (TypeError, ValueError):
            errors.append("monthly_token_ceiling must be an integer")
    if "runs" in body:
        runs = body["runs"]
        if not isinstance(runs, list):
            errors.append("runs must be a list")
        else:
            cleaned = []
            for item in runs:
                if isinstance(item, str):
                    at = item.strip()
                    label = "run"
                elif isinstance(item, dict):
                    at = str(item.get("at") or "").strip()
                    label = str(item.get("label") or "run")
                else:
                    errors.append("invalid run entry")
                    continue
                if not _validate_hhmm(at):
                    errors.append("invalid run time: " + at)
                else:
                    cleaned.append({"at": at, "label": label})
            body["runs"] = cleaned
    if "mode" in body and str(body["mode"]) != "self_product_mode":
        errors.append("mode must remain self_product_mode")
    if errors:
        return {
            "success": False,
            "command": "validate schedule.json",
            "summary": "validation failed",
            "errors": errors,
            "schedule": current,
        }
    allowed = {
        "enabled", "timezone", "runs", "max_runs_per_day", "mode", "cheap_default",
        "monthly_token_ceiling", "auto_pause_conditions", "operator_review_trigger",
        "production_candidate_operations", "architecture_freeze", "goal_delivery_mode",
    }
    for k, v in body.items():
        if k in allowed:
            current[k] = v
    current.setdefault("mode", "self_product_mode")
    _save_json(path, current)
    applied = get_schedule()
    return {
        "success": True,
        "command": "write schedule.json",
        "summary": "schedule updated",
        "schedule": applied,
        "applied": applied,
        "overview": overview(),
    }


def get_budget() -> dict[str, Any]:
    policy = _load_json(RUNTIME / "cost_policy.json")
    accounting = _load_json(RUNTIME / "cost_accounting.json")
    schedule = get_schedule()
    return {
        "policy": policy or {"budget_mode": "cheap_default", "allow_expensive_execution": False},
        "accounting": accounting,
        "monthly_token_ceiling": schedule.get("monthly_token_ceiling"),
    }


def post_budget(body: dict[str, Any]) -> dict[str, Any]:
    policy_path = RUNTIME / "cost_policy.json"
    policy = _load_json(policy_path) or {"budget_mode": "cheap_default", "allow_expensive_execution": False}
    errors: list[str] = []
    if "budget_mode" in body:
        mode = str(body["budget_mode"])
        if mode not in {"cheap_default", "balanced", "aggressive"}:
            errors.append("budget_mode must be cheap_default|balanced|aggressive")
        else:
            policy["budget_mode"] = mode
    if "allow_expensive_execution" in body:
        val = body["allow_expensive_execution"]
        if isinstance(val, str):
            val = val.lower() in {"true", "1", "yes"}
        if not isinstance(val, bool):
            errors.append("allow_expensive_execution must be boolean")
        else:
            policy["allow_expensive_execution"] = val
    if "monthly_token_ceiling" in body:
        try:
            ceil = int(body["monthly_token_ceiling"])
            if ceil < 1000:
                errors.append("monthly_token_ceiling must be >= 1000")
            body["monthly_token_ceiling"] = ceil
        except (TypeError, ValueError):
            errors.append("monthly_token_ceiling must be an integer")
    if errors:
        return {
            "success": False,
            "command": "validate budget",
            "summary": "validation failed",
            "errors": errors,
            "budget": get_budget(),
        }
    _save_json(policy_path, policy)
    if "monthly_token_ceiling" in body:
        schedule = get_schedule()
        schedule["monthly_token_ceiling"] = int(body["monthly_token_ceiling"])
        _save_json(RUNTIME / "schedule.json", schedule)
    applied = get_budget()
    return {
        "success": True,
        "command": "write cost_policy/schedule",
        "summary": "budget updated",
        "budget": applied,
        "applied": applied,
        "overview": overview(),
    }



def _release_gate_safe() -> dict[str, Any]:
    try:
        from production_freeze import ensure_production_freeze_mode, release_gate

        ensure_production_freeze_mode()
        return release_gate()
    except Exception as exc:
        return {"release_ready": False, "error": str(exc)[:200]}


def service_status() -> dict[str, Any]:
    from operator_runtime import read_service_status, service_unit_for_repo, startup_health_checks

    status = read_service_status()
    # Live probe of API process is implicit (this handler is running).
    status["api_healthy"] = True
    status["health"] = status.get("health") or startup_health_checks()
    status["ui_url"] = status.get("listen") or f"http://{HOST}:{PORT}/"
    return status


def action_service_restart() -> dict[str, Any]:
    """One-step operator recovery via systemd --user."""
    from operator_runtime import write_service_status

    write_service_status(state="restarting", last_failure="")
    cmd = ["systemctl", "--user", "restart", "purple-halo-operator.service"]
    try:
        # Detach so this request can complete before process dies.
        subprocess.Popen(cmd, cwd=ROOT, start_new_session=True)
        return {
            "success": True,
            "command": " ".join(cmd),
            "summary": "restart requested",
            "service": {"state": "restarting"},
        }
    except Exception as exc:
        write_service_status(state="failed", last_failure=str(exc)[:300])
        return {
            "success": False,
            "command": " ".join(cmd),
            "summary": "restart failed",
            "error": str(exc),
            "service": service_status(),
        }


def _parse_install_output(stdout: str) -> dict[str, Any]:
    """Best-effort structured fields from install_to_repo.sh output."""
    details: dict[str, Any] = {"unit": "", "port": None, "ui_url": ""}
    for line in stdout.splitlines():
        raw = line.strip()
        if raw.startswith("service:") and "port" in raw:
            details["unit"] = raw.split("(", 1)[0].replace("service:", "").strip()
            try:
                port = int(raw.rsplit("port", 1)[-1].strip().rstrip(")"))
            except ValueError:
                port = None
            if port:
                details["port"] = port
                details["ui_url"] = f"http://127.0.0.1:{port}/"
    return details


def simple_status() -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPTS))
    from ph_cli import REPORT_PATH
    from operator_runtime import read_service_status, service_unit_for_repo

    schedule = _load_json(RUNTIME / "schedule.json")
    history = _load_json(RUNTIME / "schedule_run_history.json")
    playing = bool(schedule.get("enabled")) and bool(history.get("autonomous_allowed", True))
    goal_path = ROOT / "project_goals.md"
    goal_preview = ""
    if goal_path.is_file():
        goal_preview = goal_path.read_text(encoding="utf-8")[:800]
    report = ""
    if REPORT_PATH.is_file():
        report = REPORT_PATH.read_text(encoding="utf-8")
    svc = read_service_status()
    ui_url = svc.get("listen") or f"http://{HOST}:{PORT}/"
    attempts = history.get("attempts") or []
    last_run: dict[str, Any] = {}
    if attempts and isinstance(attempts[-1], dict):
        last_run = attempts[-1]
    schedule_saved = schedule.get("every_hours") is not None
    return {
        "repo": str(ROOT),
        "repo_name": ROOT.name,
        "playing": playing,
        "every_hours": schedule.get("every_hours"),
        "for_days": schedule.get("for_days"),
        "until_goal_achieved": bool(schedule.get("until_goal_achieved")),
        "campaign_started_at": schedule.get("campaign_started_at"),
        "campaign_stop_reason": schedule.get("campaign_stop_reason") or "",
        "goal_file": "project_goals.md" if goal_path.is_file() else "",
        "goal_ready": goal_path.is_file(),
        "goal_source": schedule.get("goal_file_source") or "",
        "goal_preview": goal_preview,
        "schedule_saved": schedule_saved,
        "report": report,
        "report_path": str(REPORT_PATH),
        "ui_url": ui_url,
        "service_state": svc.get("state") or "unknown",
        "service_unit": svc.get("unit") or service_unit_for_repo(ROOT),
        "run_count": len(attempts),
        "last_run": last_run,
    }


def simple_frequency(body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    sys.path.insert(0, str(SCRIPTS))
    from ph_cli import set_frequency

    every = str(body.get("every") or "2h")
    for_days = body.get("for_days")
    if for_days is not None and for_days != "":
        for_days = float(for_days)
    else:
        for_days = None
    until_goal = bool(body.get("until_goal", True))
    set_frequency(every=every, for_days=for_days, until_goal=until_goal)
    return {"ok": True, "message": "Schedule saved", "status": simple_status()}


def simple_play(body: dict[str, Any] | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPTS))
    from ph_cli import play

    play()
    return {"ok": True, "message": "Playing", "status": simple_status()}


def simple_pause(body: dict[str, Any] | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPTS))
    from ph_cli import pause

    pause()
    return {"ok": True, "message": "Paused", "status": simple_status()}


def simple_goal(body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    path = str(body.get("path") or "").strip()
    if not path:
        return {"ok": False, "error": "goal path required", "status": simple_status()}
    sys.path.insert(0, str(SCRIPTS))
    from ph_cli import set_goal

    try:
        dest = set_goal(path)
    except SystemExit as exc:
        return {"ok": False, "error": str(exc), "status": simple_status()}
    return {"ok": True, "message": f"Goal set: {dest}", "status": simple_status()}


def _run_now_message(result: dict[str, Any]) -> str:
    payload = result.get("payload")
    if isinstance(payload, dict):
        reason = payload.get("reason") or (payload.get("record") or {}).get("error")
        why = (payload.get("run_decision") or {}).get("why_run") or (payload.get("record") or {}).get("why_run")
        if reason:
            msg = f"Run skipped ({str(reason).replace('_', ' ')})"
            if why:
                msg += f": {why}"
            return msg
    return (result.get("stderr") or result.get("stdout") or "run failed")[:300]


def simple_run_now(body: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _run_py(["scripts/loop_schedule.py", "--run-now"], timeout=300)
    return {
        "ok": result["ok"],
        "message": "Run finished" if result["ok"] else _run_now_message(result),
        "result": result,
        "status": simple_status(),
    }


def simple_install(body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    repo = str(body.get("repo") or "").strip()
    if not repo:
        return {"ok": False, "error": "repo path required", "status": simple_status()}
    goal = str(body.get("goal") or "").strip()
    cmd = ["bash", str(SCRIPTS / "install_to_repo.sh"), repo]
    if goal:
        cmd.extend(["--goal", goal])
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "status": simple_status()}
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    install_details = _parse_install_output(out)
    install_details["repo"] = repo
    message = "Installed."
    if proc.returncode == 0:
        message = f"Installed into {repo}."
        if install_details.get("ui_url"):
            message += f" Open {install_details['ui_url']} to set frequency and press Play."
        else:
            message += " Open that repo's purple_halo UI to set frequency and press Play."
    else:
        message = out[-500:] or "install failed"
    return {
        "ok": proc.returncode == 0,
        "message": message,
        "stdout": out[-2000:],
        "install": install_details,
        "status": simple_status(),
    }


ROUTES_GET = {
    "/api/status/overview": overview,
    "/api/status/runs": runs_status,
    "/api/status/goal-ledger": goal_ledger_status,
    "/api/status/diagnostics": diagnostics_status,
    "/api/status/service": service_status,
    "/api/status/release-gate": _release_gate_safe,
    "/api/config/schedule": get_schedule,
    "/api/config/budget": get_budget,
    "/api/simple/status": simple_status,
}

ROUTES_POST = {
    "/api/actions/run-now": action_run_now,
    "/api/actions/run-due": action_run_due,
    "/api/actions/pause": action_pause,
    "/api/actions/resume": action_resume,
    "/api/actions/verify": action_verify,
    "/api/actions/refresh-ledger": action_refresh_ledger,
    "/api/actions/self-check": action_self_check,
    "/api/actions/service-restart": action_service_restart,
    "/api/config/schedule": post_schedule,
    "/api/config/budget": post_budget,
    "/api/simple/frequency": simple_frequency,
    "/api/simple/play": simple_play,
    "/api/simple/pause": simple_pause,
    "/api/simple/goal": simple_goal,
    "/api/simple/run-now": simple_run_now,
    "/api/simple/install": simple_install,
}

BODY_POST_ROUTES = {
    "/api/config/schedule",
    "/api/config/budget",
    "/api/simple/frequency",
    "/api/simple/goal",
    "/api/simple/install",
    "/api/simple/play",
    "/api/simple/pause",
    "/api/simple/run-now",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "purple_halo_operator/1.0"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload: Any) -> None:
        raw = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def _static(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = "text/plain"
        if path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif path.suffix == ".svg":
            ctype = "image/svg+xml"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ROUTES_GET:
            try:
                self._json(200, ROUTES_GET[path]())
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc), "trace": traceback.format_exc()[-1500:]})
            return
        if path in {"/", "/index.html"}:
            self._static(UI_DIR / "index.html")
            return
        if path.startswith("/"):
            candidate = UI_DIR / path.lstrip("/")
            if candidate.is_file() and UI_DIR in candidate.resolve().parents:
                self._static(candidate)
                return
        self._json(404, {"ok": False, "error": "not found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        body: dict[str, Any] = {}
        if length:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "invalid json"})
                return
        if path not in ROUTES_POST:
            self._json(404, {"ok": False, "error": "not found", "path": path})
            return
        try:
            fn = ROUTES_POST[path]
            if path in BODY_POST_ROUTES:
                payload = fn(body)
            else:
                payload = fn()
            self._json(200, payload)
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc), "trace": traceback.format_exc()[-1500:]})

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def self_check() -> None:
    simple = simple_status()
    assert "repo" in simple and "playing" in simple and "report" in simple
    assert "ui_url" in simple and "report_path" in simple and "service_state" in simple
    ov = overview()
    assert "mode" in ov and "health" in ov
    assert "budget" in ov and "goal_progress" in ov
    runs = runs_status()
    assert "sequence" in runs
    ledger = goal_ledger_status()
    assert "criteria" in ledger
    diag = diagnostics_status()
    assert "cheap_default" in diag
    assert "ui_dogfood" in diag
    assert "ui_operator_ready" in ov["mode"]
    svc = service_status()
    assert "state" in svc
    gate = _release_gate_safe()
    assert "gates" in gate or "error" in gate
    assert "production_freeze_mode" in ov["mode"] or "release_gate" in ov
    bad = post_schedule({"max_runs_per_day": -1})
    assert bad.get("success") is False
    bad2 = post_budget({"budget_mode": "nope"})
    assert bad2.get("success") is False
    print("operator-api: PASS")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="purple_halo operator API")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"purple_halo operator console: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())