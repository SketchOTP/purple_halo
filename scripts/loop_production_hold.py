#!/usr/bin/env python3
"""Production hold mode: verify-only when healthy, repair-only on regression."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "project_memory" / "runtime" / "schedule_run_history.json"
HOLD_STATE_PATH = ROOT / "project_memory" / "runtime" / "production_hold_state.json"
LEDGER_PATH = ROOT / "project_memory" / "runtime" / "goal_delivery_ledger.json"

HOLD_WORK_CLASSES = frozenset({
    "regression_fix",
    "drift_repair",
    "token_efficiency_repair",
    "verification_truthfulness_repair",
    "operator_requested_change",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def _history() -> dict[str, Any]:
    return _load_json(HISTORY_PATH)


def _save_history(history: dict[str, Any]) -> None:
    history["updated_at"] = _now_iso()
    _save_json(HISTORY_PATH, history)


def production_hold_active(history: dict[str, Any] | None = None) -> bool:
    history = history if history is not None else _history()
    try:
        from loop_production_ops import production_ops_active

        prod = production_ops_active(history)
    except Exception:
        prod = bool(history.get("production_candidate_operations"))
    return bool(prod and history.get("production_hold_mode"))


def ensure_production_hold_mode() -> dict[str, Any]:
    """Enter hold mode when production ops is active and all criteria are complete."""
    from loop_goal_delivery import criteria_complete, ensure_goal_delivery_mode, refresh_ledger
    from loop_production_ops import ensure_production_candidate_operations

    history = ensure_production_candidate_operations()
    ensure_goal_delivery_mode()
    ledger = refresh_ledger()
    all_complete = bool(ledger.get("all_criteria_complete") or criteria_complete(ledger))
    changed = False
    if all_complete and not history.get("production_hold_mode"):
        history["production_hold_mode"] = True
        history["feature_freeze"] = True
        history["architecture_freeze"] = True
        changed = True
    if history.get("production_hold_mode"):
        _ensure_hold_baseline(ledger)
    if changed:
        _save_history(history)
    return history


def _ensure_hold_baseline(ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    hold = _load_json(HOLD_STATE_PATH)
    ledger = ledger or _load_json(LEDGER_PATH)
    if not hold.get("baseline_criteria") and ledger.get("criteria"):
        hold = {
            "version": 1,
            "entered_at": _now_iso(),
            "baseline_criteria": {
                str(c.get("id") or ""): {
                    "status": c.get("status"),
                    "evidence": list(c.get("evidence") or []),
                }
                for c in (ledger.get("criteria") or [])
                if c.get("id")
            },
            "reopened_criteria": [],
            "last_check_at": "",
            "last_run_kind": "",
            "last_regressions": [],
            "ledger_intact": True,
        }
        _save_json(HOLD_STATE_PATH, hold)
    return hold


def load_hold_state() -> dict[str, Any]:
    return _ensure_hold_baseline()


def detect_hold_regressions() -> list[dict[str, Any]]:
    """Regression checks against the complete goal ledger baseline."""
    from loop_goal_delivery import refresh_ledger
    from loop_cost_policy import load_policy, load_accounting

    regressions: list[dict[str, Any]] = []
    hold = load_hold_state()
    baseline = dict(hold.get("baseline_criteria") or {})
    ledger = refresh_ledger()
    current = {str(c.get("id") or ""): c for c in (ledger.get("criteria") or []) if c.get("id")}

    # 1) Criterion regression: was complete in baseline, no longer complete.
    for cid, base in baseline.items():
        if str(base.get("status") or "") != "complete":
            continue
        cur = current.get(cid) or {}
        if str(cur.get("status") or "") != "complete":
            regressions.append({
                "class": "criterion_regression",
                "work_class": "regression_fix",
                "criterion_id": cid,
                "detail": "criterion left complete status: " + str(cur.get("status") or "missing"),
                "blocker_reason": cur.get("blocker_reason") or "criterion_regressed",
            })

    # 2) Continuity regression — only if artifact is missing/unreadable.
    # Empty focus is valid in verify-only hold.
    cont_path = ROOT / "project_memory" / "runtime" / "continuity_state.json"
    if not cont_path.is_file():
        regressions.append({
            "class": "continuity_regression",
            "work_class": "drift_repair",
            "criterion_id": "continuity_state",
            "detail": "continuity_state missing",
            "blocker_reason": "continuity_state_missing",
        })
    else:
        cont = _load_json(cont_path)
        if cont_path.stat().st_size > 0 and not cont:
            regressions.append({
                "class": "continuity_regression",
                "work_class": "drift_repair",
                "criterion_id": "continuity_state",
                "detail": "continuity_state unreadable",
                "blocker_reason": "continuity_state_corrupt",
            })

    # 3) cheap_default regression
    try:
        policy = load_policy()
        if str(policy.get("budget_mode") or "") != "cheap_default" or policy.get("allow_expensive_execution"):
            regressions.append({
                "class": "cheap_default_regression",
                "work_class": "token_efficiency_repair",
                "criterion_id": "autonomous_iteration",
                "detail": "budget_mode is not cheap_default or expensive execution enabled",
                "blocker_reason": "cheap_default_disabled",
            })
        # Only count workers observed during hold runs, not pre-hold history.
        history = _history()
        hold_workers = any(
            r.get("worker_used") for r in (history.get("hold_run_results") or [])
        )
        if hold_workers:
            regressions.append({
                "class": "cheap_default_regression",
                "work_class": "token_efficiency_repair",
                "criterion_id": "autonomous_iteration",
                "detail": "worker sessions recorded under hold mode",
                "blocker_reason": "worker_session_used",
            })
    except Exception as exc:
        regressions.append({
            "class": "cheap_default_regression",
            "work_class": "token_efficiency_repair",
            "criterion_id": "autonomous_iteration",
            "detail": "cost policy unreadable: " + str(exc)[:120],
            "blocker_reason": "cost_policy_unreadable",
        })

    # 4) False progress regression: recent delivery/hold results claim progress without truth
    history = _history()
    for row in list(history.get("goal_delivery_results") or [])[-5:]:
        if row.get("meaningful_progress") and row.get("verification_truthful") is False:
            regressions.append({
                "class": "false_progress_regression",
                "work_class": "verification_truthfulness_repair",
                "criterion_id": str(row.get("success_criterion_id") or "verification_evidence"),
                "detail": "meaningful_progress without verification_truthful",
                "blocker_reason": "false_progress",
            })
            break
    for row in list(history.get("hold_run_results") or [])[-5:]:
        if row.get("run_kind") == "repair" and row.get("meaningful_progress") and not row.get("verification_truthful", True):
            regressions.append({
                "class": "false_progress_regression",
                "work_class": "verification_truthfulness_repair",
                "criterion_id": str(row.get("success_criterion_id") or "verification_evidence"),
                "detail": "repair claimed progress without truthful verification",
                "blocker_reason": "false_progress",
            })
            break

    # 5) Schedule/control regression
    try:
        from loop_autonomous import load_schedule_config

        cfg = load_schedule_config()
        if not cfg.get("enabled"):
            regressions.append({
                "class": "schedule_control_regression",
                "work_class": "drift_repair",
                "criterion_id": "schedule_config",
                "detail": "schedule disabled",
                "blocker_reason": "schedule_disabled",
            })
        if cfg.get("mode") and str(cfg.get("mode")) != "self_product_mode":
            regressions.append({
                "class": "schedule_control_regression",
                "work_class": "drift_repair",
                "criterion_id": "schedule_config",
                "detail": "mode is not self_product_mode",
                "blocker_reason": "mode_drift",
            })
        if history.get("production_hold_mode") and not history.get("autonomous_allowed", True):
            if str(history.get("stop_classification") or "") not in {"", "verify_only_healthy", "operator_pause"}:
                # unexpected halt while in hold
                if str(history.get("stop_classification") or "") not in HOLD_WORK_CLASSES and not str(
                    history.get("stop_classification") or ""
                ).startswith("repair_"):
                    pass  # allow intentional pauses; repair path handles reopen
    except Exception as exc:
        regressions.append({
            "class": "schedule_control_regression",
            "work_class": "drift_repair",
            "criterion_id": "schedule_config",
            "detail": "schedule unreadable: " + str(exc)[:120],
            "blocker_reason": "schedule_unreadable",
        })

    # unique by class+criterion
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in regressions:
        key = str(r.get("class")) + ":" + str(r.get("criterion_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def apply_regressions(regressions: list[dict[str, Any]]) -> dict[str, Any]:
    """Reopen only affected criteria and record hold-state repair needs."""
    hold = load_hold_state()
    hold["last_check_at"] = _now_iso()
    hold["last_regressions"] = regressions
    reopened = []
    if not regressions:
        hold["ledger_intact"] = True
        hold["reopened_criteria"] = []
        hold["last_run_kind"] = "verify_only"
        _save_json(HOLD_STATE_PATH, hold)
        return hold

    hold["ledger_intact"] = False
    hold["last_run_kind"] = "repair"
    baseline = dict(hold.get("baseline_criteria") or {})
    for reg in regressions:
        cid = str(reg.get("criterion_id") or "")
        if not cid:
            continue
        reopened.append({
            "id": cid,
            "work_class": reg.get("work_class") or "regression_fix",
            "regression_class": reg.get("class") or "criterion_regression",
            "detail": reg.get("detail") or "",
            "blocker_reason": reg.get("blocker_reason") or "",
            "reopened_at": _now_iso(),
        })
        # Mark baseline entry as needing repair (status no longer trusted complete).
        if cid in baseline:
            baseline[cid] = dict(baseline[cid])
            baseline[cid]["status"] = "regressed"
            baseline[cid]["regression"] = reg.get("class")
    hold["baseline_criteria"] = baseline
    # unique by id
    by_id = {str(r.get("id")): r for r in reopened}
    hold["reopened_criteria"] = list(by_id.values())
    _save_json(HOLD_STATE_PATH, hold)
    return hold


def hold_repair_specs() -> list[dict[str, Any]]:
    """Only repair-class work for reopened criteria."""
    if not production_hold_active():
        return []
    hold = load_hold_state()
    regressions = detect_hold_regressions()
    hold = apply_regressions(regressions)
    specs: list[dict[str, Any]] = []
    priority = 1
    for item in hold.get("reopened_criteria") or []:
        cid = str(item.get("id") or "")
        work_class = str(item.get("work_class") or "regression_fix")
        if work_class not in HOLD_WORK_CLASSES:
            work_class = "regression_fix"
        work_id = "repair_" + work_class + "_" + cid
        verify_cmd = ["python3", "scripts/loop_goal_delivery.py", "--self-check"]
        if work_class == "token_efficiency_repair":
            verify_cmd = ["python3", "scripts/loop_cost_policy.py", "--self-check"]
        elif work_class == "verification_truthfulness_repair":
            verify_cmd = ["python3", "scripts/loop_verify.py", "--self-check"]
        elif work_class == "drift_repair":
            verify_cmd = ["python3", "scripts/loop_continuity_state.py", "--self-check"]
        specs.append({
            "work_id": work_id,
            "title": "Hold repair: " + work_class + " for " + cid,
            "capability": "schedule_control",
            "goal_gap_addressed": cid,
            "success_criterion_id": cid,
            "success_criterion_text": "Repair regression on " + cid,
            "evidence_will_move": str(item.get("detail") or item.get("blocker_reason") or "repair regression"),
            "runtime_evidence_required": "repair run restores criterion health under hold mode",
            "next_cycle_effect": "criterion returns to complete and hold mode resumes verify_only",
            "task_type": "verification_hardening",
            "priority": priority,
            "local_only": True,
            "hold_work_class": work_class,
            "objective": "Repair " + work_class + " affecting " + cid,
            "why_now": "production_hold_mode regression: " + str(item.get("regression_class") or ""),
            "detect_open": lambda: True,
            "target_files": [],
            "proposed_repo_delta": [],
            "expected_outputs": [],
            "execution_steps": [{"type": "run_command", "command": verify_cmd}],
            "verification_commands": [verify_cmd],
            "done_when": [
                "regression cleared for " + cid,
                f"command passes: {' '.join(verify_cmd)}",
            ],
            "generated_from": "production_hold_repair",
        })
        priority += 1
    return specs


def evaluate_hold_run() -> dict[str, Any]:
    """Decide verify_only vs repair for the current hold-mode run."""
    ensure_production_hold_mode()
    if not production_hold_active():
        return {
            "production_hold_mode": False,
            "run_kind": "",
            "allow_implementation": True,
            "regressions": [],
            "reopened_criteria": [],
            "ledger_intact": True,
        }
    regressions = detect_hold_regressions()
    hold = apply_regressions(regressions)
    if regressions:
        return {
            "production_hold_mode": True,
            "run_kind": "repair",
            "allow_implementation": True,
            "regressions": regressions,
            "reopened_criteria": hold.get("reopened_criteria") or [],
            "ledger_intact": False,
            "why": "regression detected: " + ", ".join(
                str(r.get("class")) + ":" + str(r.get("criterion_id")) for r in regressions
            ),
        }
    return {
        "production_hold_mode": True,
        "run_kind": "verify_only",
        "allow_implementation": False,
        "regressions": [],
        "reopened_criteria": [],
        "ledger_intact": True,
        "why": "all success criteria complete; no regression detected",
    }


def record_hold_run(entry: dict[str, Any], *, run_kind: str) -> dict[str, Any]:
    history = _history()
    if not production_hold_active(history):
        return history
    row = {
        "cycle_id": entry.get("cycle_id"),
        "started_at": entry.get("started_at") or _now_iso(),
        "run_kind": run_kind,
        "plan_id": entry.get("plan_id") or "",
        "success_criterion_id": entry.get("success_criterion_id") or "",
        "hold_work_class": entry.get("hold_work_class") or "",
        "meaningful_progress": bool(entry.get("meaningful_product_progress")),
        "verification_truthful": bool(entry.get("verification_truthful", True)),
        "worker_used": bool(entry.get("worker_used")),
        "outcome_class": entry.get("outcome_class") or "",
    }
    history.setdefault("hold_run_results", []).append(row)
    history["hold_run_results"] = history["hold_run_results"][-50:]
    history["last_hold_run_kind"] = run_kind
    _save_history(history)
    # Successful repair: if no regressions remain, restore baseline complete markers.
    if run_kind == "repair" and row.get("meaningful_progress"):
        regs = detect_hold_regressions()
        hold = apply_regressions(regs)
        if not regs:
            # restore baseline statuses to complete for previously regressed ids
            baseline = dict(hold.get("baseline_criteria") or {})
            for cid, base in baseline.items():
                if str(base.get("status") or "") == "regressed":
                    baseline[cid] = dict(base)
                    baseline[cid]["status"] = "complete"
                    baseline[cid].pop("regression", None)
            hold["baseline_criteria"] = baseline
            hold["ledger_intact"] = True
            hold["reopened_criteria"] = []
            hold["last_run_kind"] = "verify_only"
            _save_json(HOLD_STATE_PATH, hold)
    return history


def hold_status_fields() -> dict[str, Any]:
    history = _history()
    active = production_hold_active(history)
    if not active:
        return {
            "production_hold_mode": False,
            "ledger_intact": True,
            "reopened_criteria": [],
            "last_hold_run_kind": history.get("last_hold_run_kind") or "",
            "hold_run_kind": "",
        }
    decision = evaluate_hold_run()
    hold = load_hold_state()
    return {
        "production_hold_mode": True,
        "ledger_intact": bool(decision.get("ledger_intact")),
        "goal_ledger_intact": bool(decision.get("ledger_intact")),
        "reopened_criteria": decision.get("reopened_criteria") or [],
        "last_hold_run_kind": history.get("last_hold_run_kind") or hold.get("last_run_kind") or "",
        "hold_run_kind": decision.get("run_kind") or "",
        "hold_regressions": decision.get("regressions") or [],
        "hold_why": decision.get("why") or "",
    }


def self_check() -> None:
    assert "regression_fix" in HOLD_WORK_CLASSES
    regs = detect_hold_regressions()
    assert isinstance(regs, list)
    decision = evaluate_hold_run()
    assert "run_kind" in decision
    assert decision.get("run_kind") in {"", "verify_only", "repair"}
    status = hold_status_fields()
    assert "production_hold_mode" in status
    assert "ledger_intact" in status or not status.get("production_hold_mode")
    # When hold active and healthy, no repair specs.
    if status.get("production_hold_mode") and status.get("ledger_intact"):
        assert hold_repair_specs() == []
        assert decision.get("allow_implementation") is False
        assert decision.get("run_kind") == "verify_only"
    print("loop-production-hold: PASS")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="purple_halo production hold mode")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.status:
        print(json.dumps(hold_status_fields(), indent=2))
        return 0
    if args.evaluate:
        print(json.dumps(evaluate_hold_run(), indent=2))
        return 0
    parser.error("specify --self-check, --status, or --evaluate")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())