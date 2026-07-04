#!/usr/bin/env python3
"""Runtime cost policy and budget accounting for purple_halo loop. Stdlib only."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "project_memory" / "runtime"
POLICY_PATH = RUNTIME / "cost_policy.json"
ACCOUNTING_PATH = RUNTIME / "cost_accounting.json"

BUDGET_MODES = frozenset({"cheap_default", "balanced", "aggressive"})

MODE_LIMITS: dict[str, dict[str, int]] = {
    "cheap_default": {
        "max_tokens_per_cycle": 25_000,
        "max_worker_sessions_per_day": 0,
        "max_research_calls_per_day": 1,
        "max_code_implementation_attempts_per_day": 0,
    },
    "balanced": {
        "max_tokens_per_cycle": 100_000,
        "max_worker_sessions_per_day": 3,
        "max_research_calls_per_day": 5,
        "max_code_implementation_attempts_per_day": 2,
    },
    "aggressive": {
        "max_tokens_per_cycle": 500_000,
        "max_worker_sessions_per_day": 10,
        "max_research_calls_per_day": 20,
        "max_code_implementation_attempts_per_day": 5,
    },
}

RUN_PROFILES = frozenset({
    "cheap_default_shadow",
    "controlled_expensive_single_cycle",
    "operator_override",
})

_current_run_profile: str | None = None

DEFAULT_POLICY: dict[str, Any] = {
    "budget_mode": "cheap_default",
    "allow_expensive_execution": False,
    "pin_expensive_execution": False,
    "worker_health_ttl_seconds": 3600,
}

ACTION_TOKEN_ESTIMATES: dict[str, int] = {
    "cycle_base": 1_000,
    "research_fetch": 2_000,
    "worker_session": 15_000,
    "code_implementation": 20_000,
    "docs_update": 500,
    "repo_analysis": 500,
    "verification_hardening_local": 1_000,
}

CHEAP_TASK_TYPES = frozenset({"docs_update", "repo_analysis"})
LOCAL_VERIFY_TYPES = frozenset({"verification_hardening"})


def _today() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_accounting() -> dict[str, Any]:
    return {
        "day": _today(),
        "month": _today()[:7],
        "worker_session_count": 0,
        "research_call_count": 0,
        "code_implementation_attempt_count": 0,
        "estimated_token_cost": 0,
        "monthly_estimated_token_cost": 0,
        "actual_token_cost": None,
        "cycles": [],
    }


def load_policy() -> dict[str, Any]:
    merged = dict(DEFAULT_POLICY)
    if POLICY_PATH.is_file():
        try:
            merged.update(json.loads(POLICY_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    mode = str(merged.get("budget_mode") or "cheap_default")
    if mode not in BUDGET_MODES:
        mode = "cheap_default"
    merged["budget_mode"] = mode
    merged.update(MODE_LIMITS[mode])
    merged["allow_expensive_execution"] = bool(merged.get("allow_expensive_execution"))
    merged["pin_expensive_execution"] = bool(merged.get("pin_expensive_execution"))
    return merged


def get_run_profile() -> str | None:
    import os

    if _current_run_profile:
        return _current_run_profile
    env = os.environ.get("PURPLE_HALO_RUN_PROFILE", "").strip()
    return env or None


def set_run_profile(profile: str | None) -> None:
    global _current_run_profile
    if profile and profile not in RUN_PROFILES:
        raise ValueError(f"unknown run_profile: {profile}")
    _current_run_profile = profile


def clear_run_profile() -> None:
    global _current_run_profile
    _current_run_profile = None


def save_policy(policy: dict[str, Any]) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    payload = {
        "budget_mode": policy.get("budget_mode", "cheap_default"),
        "allow_expensive_execution": bool(policy.get("allow_expensive_execution")),
        "pin_expensive_execution": bool(policy.get("pin_expensive_execution")),
        "worker_health_ttl_seconds": int(policy.get("worker_health_ttl_seconds") or 3600),
    }
    POLICY_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_accounting() -> dict[str, Any]:
    if not ACCOUNTING_PATH.is_file():
        return _empty_accounting()
    try:
        data = json.loads(ACCOUNTING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_accounting()
    month = _today()[:7]
    monthly = int(data.get("monthly_estimated_token_cost") or 0)
    if data.get("month") != month:
        monthly = 0
    if data.get("day") != _today():
        fresh = _empty_accounting()
        fresh["month"] = month
        fresh["monthly_estimated_token_cost"] = monthly
        return fresh
    data.setdefault("month", month)
    data.setdefault("monthly_estimated_token_cost", monthly)
    return data


def save_accounting(data: dict[str, Any]) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    ACCOUNTING_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def expensive_execution_allowed(policy: dict[str, Any] | None = None) -> bool:
    policy = policy or load_policy()
    if policy.get("allow_expensive_execution"):
        return True
    return str(policy.get("budget_mode")) != "cheap_default"


def budget_status(*, state: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = load_policy()
    acct = load_accounting()
    state = state or {}
    return {
        "budget_mode": policy["budget_mode"],
        "allow_expensive_execution": bool(policy.get("allow_expensive_execution")),
        "limits": {k: policy[k] for k in MODE_LIMITS["cheap_default"]},
        "today": {
            "worker_sessions": int(acct.get("worker_session_count") or 0),
            "research_calls": int(acct.get("research_call_count") or 0),
            "code_implementation_attempts": int(acct.get("code_implementation_attempt_count") or 0),
            "estimated_token_cost": int(acct.get("estimated_token_cost") or 0),
            "actual_token_cost": acct.get("actual_token_cost"),
        },
        "budget_blocked": bool(state.get("budget_blocked")),
        "budget_blocked_reason": str(state.get("budget_blocked_reason") or ""),
        "retry_blocked": bool(state.get("retry_blocked")),
        "retry_blocked_reason": str(state.get("retry_blocked_reason") or ""),
        "run_profile": get_run_profile(),
        "pin_expensive_execution": bool(policy.get("pin_expensive_execution")),
        "monthly": monthly_token_status(),
    }


def monthly_token_status(*, ceiling: int | None = None) -> dict[str, Any]:
    acct = load_accounting()
    if ceiling is None:
        ceiling = 500_000
        for candidate in (RUNTIME / "schedule.json", RUNTIME / "schedule.default.json"):
            if candidate.is_file():
                try:
                    cfg = json.loads(candidate.read_text(encoding="utf-8"))
                    ceiling = int(cfg.get("monthly_token_ceiling") or ceiling)
                    break
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    pass
    usage = int(acct.get("monthly_estimated_token_cost") or 0)
    ceiling = int(ceiling)
    return {
        "month": acct.get("month") or _today()[:7],
        "monthly_token_usage": usage,
        "monthly_token_ceiling": ceiling,
        "remaining": max(0, ceiling - usage),
        "at_ceiling": usage >= ceiling,
    }


def _at_cap(accounting: dict[str, Any], policy: dict[str, Any], key: str, limit_key: str) -> bool:
    return int(accounting.get(key) or 0) >= int(policy.get(limit_key) or 0)


def check_cycle_stop(
    *,
    state: dict[str, Any],
    backlog: dict[str, Any],
    health: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if state.get("budget_blocked"):
        return {"reason": "budget_blocked", "detail": state.get("budget_blocked_reason") or "budget_blocked"}
    if state.get("retry_blocked"):
        return {"reason": "retry_blocked", "detail": state.get("retry_blocked_reason") or "retry_blocked"}
    empty_reason = str(backlog.get("empty_reason") or (health or {}).get("empty_reason") or "")
    if empty_reason == "product_complete":
        return {"reason": "product_complete", "detail": empty_reason}
    open_count = int((health or {}).get("open_count") or 0)
    has_executable = bool((health or {}).get("has_executable_open"))
    if open_count == 0 or (not has_executable and empty_reason in {"no_executable_product_work", "product_complete"}):
        return {"reason": "no_executable_work", "detail": empty_reason or "backlog_empty"}
    return None


def scheduler_execution_allowed(*, state: dict[str, Any] | None = None) -> tuple[bool, str]:
    from loop_backlog import backlog_health, load_backlog

    state = state or {}
    if state.get("budget_blocked"):
        return False, "budget_blocked"
    backlog = load_backlog()
    health = backlog_health(backlog)
    stop = check_cycle_stop(state=state, backlog=backlog, health=health)
    if stop:
        return False, str(stop["reason"])
    return True, ""


def allow_research_call(*, policy: dict[str, Any] | None = None, accounting: dict[str, Any] | None = None) -> tuple[bool, str]:
    policy = policy or load_policy()
    accounting = accounting or load_accounting()
    if _at_cap(accounting, policy, "research_call_count", "max_research_calls_per_day"):
        return False, "research_daily_cap"
    return True, ""


def allow_worker_session(*, policy: dict[str, Any] | None = None, accounting: dict[str, Any] | None = None) -> tuple[bool, str]:
    policy = policy or load_policy()
    accounting = accounting or load_accounting()
    if not expensive_execution_allowed(policy):
        return False, "expensive_execution_disabled"
    if _at_cap(accounting, policy, "worker_session_count", "max_worker_sessions_per_day"):
        return False, "worker_session_daily_cap"
    return True, ""


def allow_code_implementation(*, policy: dict[str, Any] | None = None, accounting: dict[str, Any] | None = None) -> tuple[bool, str]:
    policy = policy or load_policy()
    accounting = accounting or load_accounting()
    if not expensive_execution_allowed(policy):
        return False, "expensive_execution_disabled"
    if _at_cap(accounting, policy, "code_implementation_attempt_count", "max_code_implementation_attempts_per_day"):
        return False, "code_implementation_daily_cap"
    ok, reason = allow_worker_session(policy=policy, accounting=accounting)
    if not ok:
        return ok, reason
    return True, ""


def allow_task_execution(task_type: str, *, plan: dict[str, Any] | None = None) -> tuple[bool, str]:
    plan = plan or {}
    if plan.get("budget_bypass"):
        return True, "budget_bypass"
    policy = load_policy()
    if get_run_profile() == "controlled_expensive_single_cycle" and expensive_execution_allowed(policy):
        return True, "controlled_expensive_authorized"
    task_type = str(task_type or plan.get("task_type") or "")
    accounting = load_accounting()
    if task_type == "code_implementation":
        return allow_code_implementation(policy=policy, accounting=accounting)
    if task_type in CHEAP_TASK_TYPES:
        return True, "cheap_task_allowed"
    if task_type in LOCAL_VERIFY_TYPES:
        wp = plan.get("work_package") or {}
        if wp.get("dispatch_target") or plan.get("worker_routed"):
            return allow_worker_session(policy=policy, accounting=accounting)
        return True, "local_verification_allowed"
    if should_use_worker_for_plan(plan):
        return allow_code_implementation(policy=policy, accounting=accounting)
    return True, "allowed"


def should_use_worker_for_plan(plan: dict[str, Any]) -> bool:
    from loop_worker_bridge import should_use_worker_bridge

    wp = plan.get("work_package") or {}
    return should_use_worker_bridge(plan, wp)


def begin_cycle_accounting(cycle_id: int) -> dict[str, Any]:
    acct = load_accounting()
    profile = get_run_profile()
    record = {
        "cycle_id": cycle_id,
        "run_profile": profile,
        "started_at": _now_iso(),
        "estimated_token_cost": ACTION_TOKEN_ESTIMATES["cycle_base"],
        "actual_token_cost": None,
        "worker_session_count": 0,
        "research_call_count": 0,
        "expensive_actions_taken": [],
        "budget_decision_reason": "",
    }
    acct.setdefault("cycles", []).append(record)
    delta = int(record["estimated_token_cost"])
    acct["estimated_token_cost"] = int(acct.get("estimated_token_cost") or 0) + delta
    acct["monthly_estimated_token_cost"] = int(acct.get("monthly_estimated_token_cost") or 0) + delta
    save_accounting(acct)
    return record


def _current_cycle_record(acct: dict[str, Any], cycle_id: int) -> dict[str, Any] | None:
    for rec in reversed(acct.get("cycles") or []):
        if int(rec.get("cycle_id") or -1) == cycle_id:
            return rec
    return None


def record_expensive_action(action: str, *, cycle_id: int | None = None, reason: str = "") -> None:
    policy = load_policy()
    acct = load_accounting()
    estimate = int(ACTION_TOKEN_ESTIMATES.get(action, 0))
    acct["estimated_token_cost"] = int(acct.get("estimated_token_cost") or 0) + estimate
    acct["monthly_estimated_token_cost"] = int(acct.get("monthly_estimated_token_cost") or 0) + estimate
    if action == "research_fetch":
        acct["research_call_count"] = int(acct.get("research_call_count") or 0) + 1
    elif action == "worker_session":
        acct["worker_session_count"] = int(acct.get("worker_session_count") or 0) + 1
    elif action == "code_implementation":
        acct["code_implementation_attempt_count"] = int(acct.get("code_implementation_attempt_count") or 0) + 1
    if cycle_id is not None:
        rec = _current_cycle_record(acct, cycle_id)
        if rec is not None:
            rec["estimated_token_cost"] = int(rec.get("estimated_token_cost") or 0) + estimate
            rec.setdefault("expensive_actions_taken", []).append(action)
            if reason:
                rec["budget_decision_reason"] = reason
            if action == "research_fetch":
                rec["research_call_count"] = int(rec.get("research_call_count") or 0) + 1
            elif action == "worker_session":
                rec["worker_session_count"] = int(rec.get("worker_session_count") or 0) + 1
    save_accounting(acct)


def finalize_cycle_accounting(
    cycle_id: int,
    *,
    budget_decision_reason: str = "",
    actual_token_cost: int | None = None,
) -> dict[str, Any]:
    policy = load_policy()
    acct = load_accounting()
    rec = _current_cycle_record(acct, cycle_id) or {}
    if budget_decision_reason:
        rec["budget_decision_reason"] = budget_decision_reason
    if actual_token_cost is not None:
        rec["actual_token_cost"] = actual_token_cost
        acct["actual_token_cost"] = actual_token_cost
    profile = rec.get("run_profile") or get_run_profile()
    over_cap = False
    if profile != "controlled_expensive_single_cycle":
        over_cap = int(rec.get("estimated_token_cost") or 0) > int(policy.get("max_tokens_per_cycle") or 0)
    save_accounting(acct)
    artifact = {
        "cycle_id": cycle_id,
        "run_profile": rec.get("run_profile") or get_run_profile(),
        "estimated_token_cost": rec.get("estimated_token_cost", 0),
        "actual_token_cost": rec.get("actual_token_cost"),
        "worker_session_count": rec.get("worker_session_count", 0),
        "research_call_count": rec.get("research_call_count", 0),
        "expensive_actions_taken": list(rec.get("expensive_actions_taken") or []),
        "budget_decision_reason": rec.get("budget_decision_reason") or budget_decision_reason,
        "over_cycle_token_cap": over_cap,
        "finalized_at": _now_iso(),
    }
    return artifact


def apply_budget_block(state: dict[str, Any], reason: str) -> dict[str, Any]:
    updated = dict(state)
    updated["budget_blocked"] = True
    updated["budget_blocked_reason"] = reason
    updated["status"] = "blocked"
    return updated


def apply_retry_block(state: dict[str, Any], work_id: str, reason: str) -> dict[str, Any]:
    from loop_open_gaps_state import load_open_gaps_state

    top = load_open_gaps_state().get("top_gap") or {}
    gap_tag = f" top_gap={top.get('id')}" if top.get("id") else ""
    updated = dict(state)
    updated["retry_blocked"] = True
    updated["retry_blocked_reason"] = f"{work_id}: {reason}{gap_tag}"
    updated["status"] = "blocked"
    return updated


def track_item_failure(state: dict[str, Any], work_id: str, failure_reason: str) -> dict[str, Any]:
    key = str(work_id or "unknown")
    # Goal-delivery confirmation items must not latch retry_blocked.
    if key.startswith("deliver_") or key.startswith("improve_"):
        return dict(state)
    updated = dict(state)
    counts = dict(updated.get("item_failure_counts") or {})
    counts[key] = int(counts.get(key) or 0) + 1
    updated["item_failure_counts"] = counts
    if counts[key] >= 2:
        updated = apply_retry_block(updated, key, failure_reason or "repeated_failure")
    return updated


def clear_budget_blocks(state: dict[str, Any]) -> dict[str, Any]:
    updated = dict(state)
    for key in ("budget_blocked", "budget_blocked_reason", "retry_blocked", "retry_blocked_reason"):
        updated.pop(key, None)
    return updated


def authorize_expensive_cycle(*, max_worker_sessions: int = 1) -> dict[str, Any]:
    policy = load_policy()
    prior_allow = bool(policy.get("allow_expensive_execution"))
    acct = load_accounting()
    policy["allow_expensive_execution"] = True
    needed = int(acct.get("worker_session_count") or 0) + max_worker_sessions
    policy["max_worker_sessions_per_day"] = max(int(policy.get("max_worker_sessions_per_day") or 0), needed)
    needed_impl = int(acct.get("code_implementation_attempt_count") or 0) + 1
    policy["max_code_implementation_attempts_per_day"] = max(
        int(policy.get("max_code_implementation_attempts_per_day") or 0), needed_impl
    )
    policy["max_tokens_per_cycle"] = max(int(policy.get("max_tokens_per_cycle") or 0), 100_000)
    save_policy(policy)
    return {
        "prior_allow_expensive_execution": prior_allow,
        "authorized": True,
        "max_worker_sessions_per_day": policy["max_worker_sessions_per_day"],
        "max_code_implementation_attempts_per_day": policy["max_code_implementation_attempts_per_day"],
    }


def reset_expensive_execution_after_cycle(*, run_profile: str | None = None) -> dict[str, Any]:
    run_profile = run_profile or get_run_profile()
    policy = load_policy()
    if policy.get("pin_expensive_execution"):
        return {"reset": False, "reason": "pinned"}
    if run_profile in {"controlled_expensive_single_cycle", "operator_override"} or policy.get("allow_expensive_execution"):
        policy["allow_expensive_execution"] = False
        save_policy(policy)
        return {"reset": True, "reason": "post_cycle_default"}
    return {"reset": False, "reason": "not_authorized_profile"}


def self_check() -> None:
    policy = load_policy()
    assert policy["budget_mode"] == "cheap_default"
    assert policy["allow_expensive_execution"] is False
    assert not expensive_execution_allowed()
    ok, _ = allow_worker_session()
    assert not ok
    ok, reason = allow_task_execution("docs_update")
    assert ok and reason == "cheap_task_allowed"
    acct = begin_cycle_accounting(0)
    assert acct["cycle_id"] == 0
    artifact = finalize_cycle_accounting(0, budget_decision_reason="self_check")
    assert "estimated_token_cost" in artifact
    monthly = monthly_token_status(ceiling=500_000)
    assert "monthly_token_usage" in monthly and "monthly_token_ceiling" in monthly
    status = budget_status()
    assert status["budget_mode"] == "cheap_default"
    assert "today" in status
    set_run_profile("cheap_default_shadow")
    assert get_run_profile() == "cheap_default_shadow"
    clear_run_profile()
    print("loop-cost-policy: PASS")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="purple_halo cost policy")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.show:
        print(json.dumps({"policy": load_policy(), "accounting": load_accounting(), "status": budget_status()}, indent=2))
        return 0
    parser.error("specify --self-check or --show")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())