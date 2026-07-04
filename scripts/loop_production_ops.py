#!/usr/bin/env python3
"""Production-candidate daily operations for purple_halo self_product_mode."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "project_memory" / "runtime" / "schedule_run_history.json"
SCHEDULE_PATH = ROOT / "project_memory" / "runtime" / "schedule.json"
SCHEDULE_DEFAULT = ROOT / "project_memory" / "runtime" / "schedule.default.json"

REGRESSION_REPEAT = 2
REGRESSION_WINDOW = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_history() -> dict[str, Any]:
    from loop_autonomous import load_autonomous_history
    return load_autonomous_history()


def _save_history(history: dict[str, Any]) -> None:
    from loop_autonomous import save_autonomous_history
    save_autonomous_history(history)


def live_soak_active(history: dict[str, Any] | None = None) -> bool:
    history = history if history is not None else _load_history()
    return bool(history.get("live_soak_mode") and not history.get("live_soak_passed"))


def production_ops_active(history: dict[str, Any] | None = None) -> bool:
    history = history if history is not None else _load_history()
    return bool(
        history.get("production_candidate_operations")
        and history.get("production_candidate")
        and history.get("live_soak_passed")
        and not live_soak_active(history)
    )


def load_schedule_config() -> dict[str, Any]:
    from loop_autonomous import load_schedule_config as _load
    return _load()


def write_production_schedule() -> dict[str, Any]:
    cfg = load_schedule_config()
    cfg.update({
        "enabled": True,
        "timezone": cfg.get("timezone") or "UTC",
        "mode": "self_product_mode",
        "cheap_default": True,
        "max_runs_per_day": int(cfg.get("max_runs_per_day") or 2),
        "monthly_token_ceiling": int(cfg.get("monthly_token_ceiling") or 500_000),
        "production_candidate_operations": True,
        "architecture_freeze": True,
        "auto_pause_conditions": list(cfg.get("auto_pause_conditions") or [
            "repeated_regression",
            "monthly_token_ceiling",
            "operator_pause",
            "budget_guard",
        ]),
        "operator_review_trigger": cfg.get("operator_review_trigger") or "regression_auto_pause",
    })
    if not cfg.get("runs"):
        cfg["runs"] = [{"at": "09:00", "label": "daily self-improvement"}]
    SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_PATH.write_text(json.dumps(cfg, indent=2) + chr(10), encoding="utf-8")
    return cfg


def start_production_candidate_operations() -> dict[str, Any]:
    from loop_autonomous import ensure_long_run_mode
    history = ensure_long_run_mode()
    history["production_candidate_operations"] = True
    history["production_candidate"] = True
    history["live_soak_passed"] = True
    history["architecture_freeze"] = True
    history["feature_freeze"] = True
    history.setdefault("production_results", [])
    history["regression_failures"] = []
    history["regression_blockers"] = []
    history["auto_pause_reason"] = ""
    history["operator_review_needed"] = False
    history["goal_delivery_mode"] = True
    history["production_started_at"] = history.get("production_started_at") or _now_iso()
    if history.get("stop_classification") in {
        "goal_realized", "product_complete", "live_soak_passed", "sequence_complete_for_review",
    }:
        history["autonomous_allowed"] = True
        history["stop_classification"] = ""
        history["stop_reason"] = ""
    history["updated_at"] = _now_iso()
    _save_history(history)
    write_production_schedule()
    try:
        from loop_cost_policy import load_policy, save_policy
        policy = load_policy()
        policy["budget_mode"] = "cheap_default"
        policy["allow_expensive_execution"] = False
        save_policy(policy)
    except Exception:
        pass
    try:
        _seed_self_improvement_backlog()
    except Exception:
        pass
    return history


def _seed_self_improvement_backlog() -> None:
    from loop_backlog import load_backlog, save_backlog
    backlog = load_backlog()
    items = list(backlog.get("product_work_items") or [])
    by_id = {str(i.get("work_id") or ""): i for i in items}
    for spec in self_improvement_specs():
        wid = str(spec.get("work_id") or "")
        if not wid:
            continue
        row = {k: v for k, v in spec.items() if k != "detect_open" and not callable(v)}
        row["status"] = "open"
        row["local_only"] = True
        if wid in by_id and by_id[wid].get("status") in {"verified", "rejected"}:
            row["status"] = "open"
        by_id[wid] = {**by_id.get(wid, {}), **row, "status": "open"}
    backlog["product_work_items"] = sorted(by_id.values(), key=lambda i: int(i.get("priority") or 99))
    save_backlog(backlog)


def ensure_production_candidate_operations() -> dict[str, Any]:
    from loop_autonomous import ensure_long_run_mode
    history = ensure_long_run_mode()
    if (
        history.get("live_soak_passed")
        and history.get("production_candidate")
        and not history.get("production_candidate_operations")
    ):
        return start_production_candidate_operations()
    if production_ops_active(history):
        cfg = load_schedule_config()
        if not cfg.get("enabled") or not cfg.get("production_candidate_operations"):
            write_production_schedule()
        if history.get("stop_classification") in {"goal_realized", "product_complete", "live_soak_passed"}:
            history["autonomous_allowed"] = True
            history["stop_classification"] = ""
            history["stop_reason"] = ""
            history["updated_at"] = _now_iso()
            _save_history(history)
    return history


def entry_from_sequence(entry: dict[str, Any]) -> dict[str, Any]:
    from loop_autonomous import HONEST_BLOCKER_OUTCOMES
    plan_id = str(entry.get("plan_id") or "")
    progress = bool(entry.get("meaningful_product_progress"))
    blocked = str(entry.get("blocked_classification") or "")
    outcome = str(entry.get("outcome_class") or "")
    honest = progress or blocked in HONEST_BLOCKER_OUTCOMES
    return {
        "cycle_id": entry.get("cycle_id"),
        "started_at": entry.get("started_at"),
        "selected_capability": entry.get("selected_capability") or entry.get("goal_capability") or "",
        "plan_id": plan_id,
        "why_selected": entry.get("why_selected") or "",
        "meaningful_progress": progress,
        "honest_blocker": bool(blocked) and not progress,
        "continuity_influenced": bool(entry.get("continuity_influenced")),
        "verification_truthful": honest,
        "cheap_default_respected": not bool(entry.get("worker_used") or entry.get("expensive_execution")),
        "worker_used": bool(entry.get("worker_used") or entry.get("expensive_execution")),
        "outcome_class": outcome or blocked or "unknown",
        "token_cost_outcome": "cheap_default" if not (entry.get("worker_used") or entry.get("expensive_execution")) else "expensive",
    }


def detect_regressions(results: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    if not results:
        return failures
    window = results[-REGRESSION_WINDOW:]
    plan_counts: dict[str, int] = {}
    cap_counts: dict[str, int] = {}
    for r in window:
        pid = str(r.get("plan_id") or "")
        cap = str(r.get("selected_capability") or "")
        low_value = (not r.get("meaningful_progress")) and (not r.get("honest_blocker"))
        if low_value and pid:
            plan_counts[pid] = plan_counts.get(pid, 0) + 1
        if low_value and cap and not r.get("continuity_influenced"):
            cap_counts[cap] = cap_counts.get(cap, 0) + 1
        if r.get("worker_used") or not r.get("cheap_default_respected", True):
            failures.append("token_cost_regression")
        if r.get("meaningful_progress") and not r.get("verification_truthful", True):
            failures.append("verification_dishonesty")
        if r.get("meaningful_progress") and (
            pid.startswith("product_gap_")
            or pid == "product_cycle_closure"
            or pid.startswith("operational_")
            or pid.startswith("target_")
            or pid.startswith("proof_")
        ):
            failures.append("weak_meaningful_progress:" + pid)
        if pid.startswith("target_") or pid.startswith("proof_"):
            failures.append("proof_or_target_work_in_production")
    if plan_counts:
        top_plan, top_count = max(plan_counts.items(), key=lambda kv: kv[1])
        if top_count >= 3:
            failures.append("repeated_low_value_work:" + top_plan)
    if cap_counts:
        top_cap, top_count = max(cap_counts.items(), key=lambda kv: kv[1])
        if top_count >= 3:
            failures.append("repeated_same_capability_churn:" + top_cap)
    progress = [r for r in window if r.get("meaningful_progress")]
    if len(progress) >= 3:
        cont = sum(1 for r in progress if r.get("continuity_influenced"))
        if cont / len(progress) < 0.5:
            failures.append("continuity_drift:" + str(cont) + "/" + str(len(progress)))
    out: list[str] = []
    for f in failures:
        if f not in out:
            out.append(f)
    return out


def regression_health(history: dict[str, Any] | None = None) -> dict[str, Any]:
    history = history if history is not None else _load_history()
    results = list(history.get("production_results") or [])
    failures = detect_regressions(results)
    return {
        "active": production_ops_active(history),
        "run_count": len(results),
        "failures": failures,
        "blockers": list(history.get("regression_blockers") or failures),
        "auto_pause_reason": history.get("auto_pause_reason") or "",
        "operator_review_needed": bool(history.get("operator_review_needed")),
        "architecture_freeze": bool(history.get("architecture_freeze")),
        "recent": results[-5:],
    }


def apply_production_entry(entry: dict[str, Any]) -> dict[str, Any]:
    history = _load_history()
    if not production_ops_active(history):
        return history
    prod_entry = entry_from_sequence(entry)
    history.setdefault("production_results", []).append(prod_entry)
    history["production_results"] = history["production_results"][-50:]
    failures = detect_regressions(history.get("production_results") or [])
    history["regression_failures"] = failures
    prior = list(history.get("regression_blockers") or [])
    for f in failures:
        cls = f.split(":", 1)[0]
        prior_count = sum(1 for p in prior if str(p).split(":", 1)[0] == cls)
        if prior_count + 1 >= REGRESSION_REPEAT:
            history["autonomous_allowed"] = False
            history["stop_classification"] = "repeated_regression"
            history["stop_reason"] = "production regression repeated: " + f
            history["auto_pause_reason"] = history["stop_reason"]
            history["regression_blockers"] = failures
            history["operator_review_needed"] = True
            history["updated_at"] = _now_iso()
            _save_history(history)
            return history
    history["regression_blockers"] = failures
    history["auto_pause_reason"] = ""
    history["updated_at"] = _now_iso()
    _save_history(history)
    return history


def daily_schedule_status() -> dict[str, Any]:
    cfg = load_schedule_config()
    return {
        "enabled": bool(cfg.get("enabled")),
        "timezone": cfg.get("timezone") or "UTC",
        "runs": list(cfg.get("runs") or []),
        "max_runs_per_day": int(cfg.get("max_runs_per_day") or 2),
        "cheap_default": bool(cfg.get("cheap_default", True)),
        "mode": cfg.get("mode") or "self_product_mode",
        "monthly_token_ceiling": int(cfg.get("monthly_token_ceiling") or 500_000),
        "auto_pause_conditions": list(cfg.get("auto_pause_conditions") or []),
        "operator_review_trigger": cfg.get("operator_review_trigger") or "",
    }


def monthly_token_for_status() -> dict[str, Any]:
    from loop_cost_policy import monthly_token_status
    cfg = load_schedule_config()
    return monthly_token_status(ceiling=int(cfg.get("monthly_token_ceiling") or 500_000))


def production_status_fields(history: dict[str, Any] | None = None) -> dict[str, Any]:
    history = history if history is not None else _load_history()
    fields = {
        "production_candidate_operations": production_ops_active(history),
        "daily_schedule": daily_schedule_status(),
        "monthly_token": monthly_token_for_status(),
        "regression_health": regression_health(history),
        "auto_pause_reason": history.get("auto_pause_reason")
        or (history.get("stop_reason") if not history.get("autonomous_allowed", True) else ""),
        "architecture_freeze": bool(history.get("architecture_freeze")),
        "operator_review_needed": bool(history.get("operator_review_needed")),
        "goal_delivery_mode": bool(history.get("goal_delivery_mode")),
    }
    try:
        from loop_goal_delivery import goal_delivery_status
        fields.update(goal_delivery_status())
    except Exception:
        fields.setdefault("goal_delivery_ledger", {})
        fields.setdefault("top_unmet_criterion", {})
        fields.setdefault("why_next_run", "")
    try:
        from loop_production_hold import ensure_production_hold_mode, hold_status_fields
        ensure_production_hold_mode()
        fields.update(hold_status_fields())
    except Exception:
        fields.setdefault("production_hold_mode", False)
    return fields


def self_improvement_specs() -> list[dict[str, Any]]:
    """Production backlog: criterion-linked delivery first, linked improve_* only as unblockers."""
    try:
        from loop_goal_delivery import delivery_work_specs, ensure_goal_delivery_mode, linked_improve_specs
        ensure_goal_delivery_mode()
        return delivery_work_specs() + linked_improve_specs()
    except Exception:
        return []


def self_check() -> None:
    bad = detect_regressions([
        {"plan_id": "product_gap_x", "meaningful_progress": True, "verification_truthful": True,
         "cheap_default_respected": True, "worker_used": False},
        {"plan_id": "y", "meaningful_progress": False, "honest_blocker": False,
         "continuity_influenced": False, "selected_capability": "plan_generation",
         "cheap_default_respected": True},
        {"plan_id": "y", "meaningful_progress": False, "honest_blocker": False,
         "continuity_influenced": False, "selected_capability": "plan_generation",
         "cheap_default_respected": True},
        {"plan_id": "y", "meaningful_progress": False, "honest_blocker": False,
         "continuity_influenced": False, "selected_capability": "plan_generation",
         "cheap_default_respected": True},
    ])
    assert any(f.startswith("weak_meaningful_progress") for f in bad)
    assert any(f.startswith("repeated_low_value_work") for f in bad)
    cost = detect_regressions([{
        "plan_id": "improve_token_efficiency", "worker_used": True,
        "cheap_default_respected": False, "meaningful_progress": True, "verification_truthful": True,
    }])
    assert "token_cost_regression" in cost
    sched = daily_schedule_status()
    assert "enabled" in sched and "max_runs_per_day" in sched
    print("loop-production-ops: PASS")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="purple_halo production-candidate operations")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.start:
        print(json.dumps(start_production_candidate_operations(), indent=2))
        return 0
    if args.status:
        print(json.dumps(production_status_fields(), indent=2))
        return 0
    parser.error("specify --self-check, --start, or --status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
