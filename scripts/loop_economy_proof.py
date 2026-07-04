#!/usr/bin/env python3
"""Economy proof mode: cheap-by-default shadow + one controlled expensive cycle. Stdlib only."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "project_memory" / "runtime"
ECONOMY_PROOF_PATH = RUNTIME / "economy_proof.json"
TARGET_COST_PROOF_PATH = RUNTIME / "target_cycle_cost_proof.json"
LIVE_TARGET_CYCLE_PROOF_PATH = RUNTIME / "live_target_cycle_proof.json"
TARGET_WORK_PROOF_PATH = RUNTIME / "target_work_proof.json"
SHADOW_CYCLE_ID = 99991

LIVE_FAILURE_CLASSES = frozenset({
    "live_worker_unavailable",
    "live_worker_blocked",
    "live_target_verification_failed",
    "live_target_no_delta",
    "live_budget_exceeded",
})

sys.path.insert(0, str(ROOT / "scripts"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _ensure_cheap_policy() -> dict[str, Any]:
    from loop_cost_policy import DEFAULT_POLICY, load_policy, save_policy

    policy = load_policy()
    policy["budget_mode"] = "cheap_default"
    policy["allow_expensive_execution"] = False
    policy.setdefault("pin_expensive_execution", False)
    save_policy({**DEFAULT_POLICY, **{k: policy[k] for k in DEFAULT_POLICY}})
    return load_policy()


def _snapshot_accounting() -> dict[str, Any]:
    from loop_cost_policy import load_accounting

    acct = load_accounting()
    return {
        "worker_session_count": int(acct.get("worker_session_count") or 0),
        "research_call_count": int(acct.get("research_call_count") or 0),
        "estimated_token_cost": int(acct.get("estimated_token_cost") or 0),
    }


def run_cheap_default_shadow() -> dict[str, Any]:
    from loop_backlog import load_backlog, save_backlog
    from loop_cost_policy import (
        begin_cycle_accounting,
        budget_status,
        expensive_execution_allowed,
        finalize_cycle_accounting,
        load_policy,
        set_run_profile,
        clear_run_profile,
    )
    from loop_execute import run_execute
    from loop_research import resolve_research
    from loop_schedule import _scheduler_gate
    from loop_state import load_state, save_state, write_cycle_artifact

    started = _now_iso()
    _ensure_cheap_policy()
    set_run_profile("cheap_default_shadow")
    os.environ["PURPLE_HALO_RUN_PROFILE"] = "cheap_default_shadow"
    before = _snapshot_accounting()
    checks: dict[str, Any] = {}

    policy = load_policy()
    checks["cheap_default_policy"] = policy["budget_mode"] == "cheap_default"
    checks["expensive_disabled"] = not expensive_execution_allowed(policy)

    workers_before = before["worker_session_count"]
    plan = {
        "plan_id": "economy_shadow_worker_gate",
        "cycle_id": SHADOW_CYCLE_ID,
        "task_type": "code_implementation",
        "work_package": {
            "work_id": "economy_shadow_worker_gate",
            "cycle_id": SHADOW_CYCLE_ID,
            "task_type": "code_implementation",
            "objective": "economy shadow worker gate",
            "target_files": ["scripts/loop_worker_proof.py"],
            "proposed_repo_delta": ["scripts/loop_worker_proof.py"],
            "execution_steps": [],
            "verification_commands": [],
            "goal_inputs": {},
            "research_inputs": {},
            "verification_basis": {},
        },
    }
    gate_result = run_execute(plan)
    after_gate = _snapshot_accounting()
    checks["worker_gate_blocked"] = bool(gate_result.get("budget_gate_blocked"))
    checks["worker_session_unchanged"] = after_gate["worker_session_count"] == workers_before

    state = load_state()
    goal_text = (ROOT / "project_goals.md").read_text(encoding="utf-8") if (ROOT / "project_goals.md").is_file() else ""
    status_text = (ROOT / "project_status.md").read_text(encoding="utf-8") if (ROOT / "project_status.md").is_file() else ""
    _, research_meta = resolve_research(
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot={"tracked_files": []},
        state=state,
    )
    after_research = _snapshot_accounting()
    checks["research_cached_or_skipped"] = research_meta.get("research_call_made") is False
    checks["research_call_unchanged"] = after_research["research_call_count"] == before["research_call_count"]

    backlog = load_backlog()
    orig_empty = backlog.get("empty_reason")
    orig_items = backlog.get("product_work_items")
    state_pc = load_state()
    orig_state_budget = state_pc.get("budget_blocked")
    if orig_state_budget:
        state_pc.pop("budget_blocked", None)
        state_pc.pop("budget_blocked_reason", None)
        save_state(state_pc)
    backlog["empty_reason"] = "product_complete"
    backlog["product_work_items"] = []
    save_backlog(backlog)
    sched_pc = _scheduler_gate("manual")
    checks["scheduler_refuses_product_complete"] = bool(sched_pc and sched_pc.get("reason") == "product_complete")
    backlog["empty_reason"] = orig_empty or ""
    backlog["product_work_items"] = orig_items or []
    save_backlog(backlog)
    if orig_state_budget:
        state_pc["budget_blocked"] = orig_state_budget
        save_state(state_pc)

    orig_budget = state.get("budget_blocked")
    state["budget_blocked"] = True
    state["budget_blocked_reason"] = "economy_shadow_test"
    save_state(state)
    sched_bb = _scheduler_gate("manual")
    checks["scheduler_refuses_budget_blocked"] = bool(sched_bb and sched_bb.get("reason") == "budget_blocked")
    if not orig_budget:
        state.pop("budget_blocked", None)
        state.pop("budget_blocked_reason", None)
    else:
        state["budget_blocked"] = orig_budget
    save_state(state)

    begin_cycle_accounting(SHADOW_CYCLE_ID)
    cost_artifact = finalize_cycle_accounting(SHADOW_CYCLE_ID, budget_decision_reason="cheap_default_shadow")
    write_cycle_artifact(SHADOW_CYCLE_ID, "cost_accounting.json", cost_artifact)
    checks["cost_artifact_written"] = cost_artifact.get("run_profile") == "cheap_default_shadow"
    checks["cost_artifact_has_fields"] = all(
        k in cost_artifact for k in ("estimated_token_cost", "worker_session_count", "research_call_count")
    )

    import loop_target_workspace as ltw

    if ltw.is_external_target() and ltw.target_proof_satisfied() and not ltw.force_proof_mode():
        from loop_backlog import is_proof_work_item, pick_next_item

        from loop_backlog import _goals_need_enrichment

        backlog = _refresh_target_backlog_for_product_work()
        nxt = pick_next_item(backlog)
        if nxt is not None:
            checks["non_proof_selection_ready"] = not is_proof_work_item(nxt)
            checks["selected_non_proof_work_id"] = bool(str(nxt.get("work_id") or ""))
        else:
            checks["goal_truth_complete"] = not _goals_need_enrichment()
            checks["non_proof_selection_ready"] = checks["goal_truth_complete"]
            checks["selected_non_proof_work_id"] = checks["goal_truth_complete"]
    passed = all(checks.values())
    proof = {
        "phase": "cheap_default_shadow",
        "run_profile": "cheap_default_shadow",
        "started_at": started,
        "finished_at": _now_iso(),
        "passed": passed,
        "checks": checks,
        "accounting_before": before,
        "accounting_after": _snapshot_accounting(),
        "budget": budget_status(state=load_state()),
        "worker_session_count": after_gate["worker_session_count"],
        "research_call_count": after_research["research_call_count"],
        "cost_artifact_path": "project_memory/runtime/loop_cycles/cycle_99991/cost_accounting.json",
    }
    _write_json(ECONOMY_PROOF_PATH, proof)
    clear_run_profile()
    os.environ.pop("PURPLE_HALO_RUN_PROFILE", None)
    return proof


def _ensure_target_config() -> dict[str, Any]:
    import loop_target_workspace as ltw

    config_path = RUNTIME / "target_workspace.json"
    if ltw.target_configured():
        return ltw.load_target_config() or {}
    payload = {
        "target_repo_path": str(ROOT),
        "target_repo_slug": "purple_halo",
        "target_verification_commands": [["python3", "scripts/verify-loop.sh"]],
        "proof_bootstrap": True,
        "created_at": _now_iso(),
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def classify_live_failure(
    *,
    cycle_result: dict[str, Any],
    execution: dict[str, Any] | None,
    cost_artifact: dict[str, Any],
    changed_files: list[str],
    verification_passed: bool | None,
    worker_outcome: str | None,
    dry_worker: bool,
) -> str | None:
    if dry_worker:
        return None
    if cycle_result.get("stopped"):
        reason = str(cycle_result.get("stop_reason") or "")
        if reason == "budget_blocked":
            return "live_budget_exceeded"
        return "live_worker_blocked"
    if cost_artifact.get("over_cycle_token_cap") or (cycle_result.get("budget") or {}).get("budget_blocked"):
        return "live_budget_exceeded"
    if execution and execution.get("budget_gate_blocked"):
        return "live_worker_blocked"
    outcome = str(worker_outcome or (execution or {}).get("worker_outcome_class") or "")
    if outcome == "worker_unavailable":
        return "live_worker_unavailable"
    if outcome in {"execution_failed", "verified_partial"} and not changed_files:
        return "live_worker_unavailable"
    if changed_files and verification_passed is False:
        return "live_target_verification_failed"
    if not changed_files and verification_passed is not True:
        return "live_target_no_delta"
    return None


def _feed_live_failure(
    failure_class: str,
    *,
    work_id: str,
    cycle_id: int | None,
    detail: str = "",
) -> None:
    from loop_backlog import load_backlog, mark_item_status, save_backlog
    from loop_state import load_state, save_state

    backlog = load_backlog()
    mark_item_status(
        backlog,
        work_id,
        "open",
        failure_reason=failure_class,
        cycle_id=cycle_id,
    )
    save_backlog(backlog)
    state = load_state()
    state["last_live_target_cycle"] = {
        "failure_class": failure_class,
        "work_id": work_id,
        "cycle_id": cycle_id,
        "detail": detail,
        "updated_at": _now_iso(),
        "dry_run": False,
    }
    save_state(state)


def _persist_live_success(
    *,
    work_id: str,
    cycle_id: int | None,
    changed_files: list[str],
    verification_passed: bool | None,
    worker_outcome: str | None,
) -> None:
    from loop_state import load_state, save_state

    state = load_state()
    state["last_live_target_cycle"] = {
        "failure_class": None,
        "work_id": work_id,
        "cycle_id": cycle_id,
        "execution_path": "worker_bridge",
        "changed_files": changed_files,
        "verification_passed": verification_passed,
        "worker_outcome": worker_outcome,
        "updated_at": _now_iso(),
        "dry_run": False,
    }
    save_state(state)




def _refresh_target_backlog_for_product_work() -> dict[str, Any]:
    from loop_backlog import refresh_backlog
    from loop_research import resolve_research
    from loop_state import load_state
    from purple_halo_loop import repo_snapshot

    import loop_target_workspace as ltw

    state = load_state()
    goal_text = ltw.goal_path().read_text(encoding="utf-8") if ltw.goal_path().is_file() else ""
    status_text = ltw.status_path().read_text(encoding="utf-8") if ltw.status_path().is_file() else ""
    snapshot = repo_snapshot()
    _, research = resolve_research(
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=snapshot,
        state=state,
    )
    return refresh_backlog(
        capability_gaps=[],
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=snapshot,
        state=state,
        research=research,
    )


def _select_target_product_work_id() -> tuple[str | None, str | None, dict[str, Any] | None]:
    from loop_backlog import is_proof_work_item, pick_next_item

    backlog = _refresh_target_backlog_for_product_work()
    item = pick_next_item(backlog)
    if item is None:
        return None, "target_no_executable_product_work", None
    if is_proof_work_item(item):
        return None, "target_proof_item_blocked", item
    return str(item.get("work_id") or ""), None, item


def _persist_target_work_proof(proof: dict[str, Any]) -> None:
    _write_json(TARGET_WORK_PROOF_PATH, proof)


def _ensure_expensive_backlog_item() -> str:
    import loop_target_workspace as ltw

    if ltw.is_external_target() and (ltw.target_worker_bridge_proven() or ltw.target_proof_satisfied()) and not ltw.force_proof_mode():
        raise RuntimeError("proof backlog item disabled for external targets with satisfied worker bridge proof")
    from loop_backlog import load_backlog, save_backlog

    backlog = load_backlog()
    work_id = "economy_proof_target_slice"
    items = backlog.get("product_work_items") or []
    existing = next((i for i in items if str(i.get("work_id")) == work_id), None)
    if existing is None:
        existing = {
            "work_id": work_id,
            "title": "Economy proof target slice",
            "capability": "implementation_dispatch",
            "goal_gap_addressed": "economy_proof",
            "task_type": "code_implementation",
            "priority": 1,
            "status": "open",
            "objective": "Write live proof marker via governed worker bridge only.",
            "target_files": ["project_memory/runtime/live_proof_marker.txt"],
            "proposed_repo_delta": ["project_memory/runtime/live_proof_marker.txt"],
            "expected_outputs": ["project_memory/runtime/live_proof_marker.txt"],
            "dispatch_target": "",
            "force_worker_bridge": True,
            "execution_steps": [{"type": "write_file", "path": "project_memory/runtime/live_proof_marker.txt", "content": "live worker bridge proof\n"}],
            "verification_commands": [["test", "-f", "project_memory/runtime/live_proof_marker.txt"]],
            "goal_inputs": {},
            "research_inputs": {},
            "verification_basis": {},
        }
        items.append(existing)
    else:
        existing["status"] = "open"
        existing["dispatch_target"] = ""
        existing["force_worker_bridge"] = True
        existing["execution_steps"] = [{"type": "write_file", "path": "project_memory/runtime/live_proof_marker.txt", "content": "live worker bridge proof\n"}]
        existing.pop("blocked_by", None)
        existing.pop("failure_reason", None)
    backlog["product_work_items"] = items
    backlog["empty_reason"] = ""
    backlog["backlog_health"] = {
        "open_count": 1,
        "executable_open_count": 1,
        "has_executable_open": True,
        "empty_reason": "",
    }
    save_backlog(backlog)
    return work_id


def _prepare_live_run_state(work_id: str) -> None:
    from loop_cost_policy import clear_budget_blocks
    from loop_state import load_state, save_state

    state = clear_budget_blocks(load_state())
    counts = dict(state.get("item_failure_counts") or {})
    counts.pop(work_id, None)
    state["item_failure_counts"] = counts
    save_state(state)


def _load_cycle_execution(cycle_id: int | None) -> dict[str, Any]:
    if cycle_id is None:
        return {}
    from loop_state import cycle_artifact_dir

    path = cycle_artifact_dir(int(cycle_id)) / "execution.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def run_controlled_expensive_single_cycle(*, dry_worker: bool = False) -> dict[str, Any]:
    import loop_target_workspace as ltw
    from loop_cost_policy import (
        authorize_expensive_cycle,
        budget_status,
        load_policy,
        reset_expensive_execution_after_cycle,
        set_run_profile,
        clear_run_profile,
    )
    from purple_halo_loop import run_cycle

    started = _now_iso()
    target_config = _ensure_target_config()
    selected_item: dict[str, Any] | None = None
    blocker_class: str | None = None
    if ltw.is_external_target() and ltw.target_proof_satisfied() and not ltw.force_proof_mode():
        work_id, blocker_class, selected_item = _select_target_product_work_id()
        if blocker_class:
            proof = {
                "phase": "target_product_work",
                "run_profile": "controlled_expensive_single_cycle",
                "started_at": started,
                "finished_at": _now_iso(),
                "target_repo": {
                    "slug": target_config.get("target_repo_slug") or "",
                    "path": target_config.get("target_repo_path") or "",
                },
                "selected_work_item": None,
                "failure_class": blocker_class,
                "passed": False,
            }
            _persist_target_work_proof(proof)
            clear_run_profile()
            os.environ.pop("PURPLE_HALO_RUN_PROFILE", None)
            return proof
        assert work_id
    else:
        work_id = _ensure_expensive_backlog_item()
    before = _snapshot_accounting()
    workers_before = before["worker_session_count"]
    live_run = not dry_worker
    if live_run:
        _prepare_live_run_state(work_id)

    auth = authorize_expensive_cycle(max_worker_sessions=1)
    set_run_profile("controlled_expensive_single_cycle")
    os.environ["PURPLE_HALO_RUN_PROFILE"] = "controlled_expensive_single_cycle"
    if dry_worker:
        os.environ["PURPLE_HALO_WORKER_DRY_RUN"] = "1"

    try:
        cycle_result = run_cycle()
    finally:
        os.environ.pop("PURPLE_HALO_WORKER_DRY_RUN", None)

    after = _snapshot_accounting()
    worker_delta = after["worker_session_count"] - workers_before
    cycle_worker_sessions = int((cycle_result.get("cost_accounting") or {}).get("worker_session_count") or 0)
    policy_after_cycle = load_policy()
    reset_info = reset_expensive_execution_after_cycle(run_profile="controlled_expensive_single_cycle")
    policy_after_reset = load_policy()

    cost_artifact = cycle_result.get("cost_accounting") or {}
    verification_passed = cycle_result.get("verification_passed")
    cycle_id = cycle_result.get("cycle_id")
    execution = {} if cycle_result.get("stopped") else _load_cycle_execution(cycle_id)
    worker_outcome = cycle_result.get("worker_outcome") or execution.get("worker_outcome_class")
    changed_files = list(execution.get("changed_files") or [])
    verified_delta: dict[str, Any] = {}
    if not cycle_result.get("stopped"):
        from loop_state import load_state

        verified_delta = load_state().get("last_verified_repo_delta") or {}
        if changed_files:
            verified_delta = {"files": changed_files, "summary": verified_delta.get("summary") or ""}
    else:
        verified_delta = {"files": [], "summary": cycle_result.get("stop_reason") or "stopped"}

    over_budget = bool(cost_artifact.get("over_cycle_token_cap"))
    budget_blocked = bool((cycle_result.get("budget") or {}).get("budget_blocked"))
    failure_class = classify_live_failure(
        cycle_result=cycle_result,
        execution=execution,
        cost_artifact=cost_artifact,
        changed_files=changed_files,
        verification_passed=verification_passed,
        worker_outcome=worker_outcome,
        dry_worker=dry_worker,
    )
    execution_path = (
        "worker_bridge"
        if execution.get("worker_routed")
        else "dispatch"
        if execution.get("dispatch_routed")
        else "local"
    )

    proof = {
        "phase": "controlled_expensive_single_cycle",
        "run_profile": "controlled_expensive_single_cycle",
        "live_run": live_run,
        "dry_worker": dry_worker,
        "started_at": started,
        "finished_at": _now_iso(),
        "authorization": auth,
        "target_repo": {
            "slug": target_config.get("target_repo_slug") or ltw.active_contract().get("target_repo_slug") if ltw.active_contract() else "",
            "path": target_config.get("target_repo_path") or (ltw.active_contract() or {}).get("target_repo_path", ""),
        },
        "selected_work_item": cycle_result.get("plan_id") or work_id,
        "execution_path": execution_path,
        "worker_outcome": worker_outcome,
        "changed_target_files": changed_files,
        "verification_result": {
            "passed": verification_passed,
            "artifact_dir": cycle_result.get("artifact_dir"),
        },
        "token_cost": {
            "estimated": cost_artifact.get("estimated_token_cost"),
            "actual": cost_artifact.get("actual_token_cost"),
            "worker_session_delta": worker_delta,
        },
        "failure_class": failure_class,
        "cycle_result_summary": {
            "stopped": cycle_result.get("stopped"),
            "cycle_id": cycle_id,
            "plan_id": cycle_result.get("plan_id"),
            "worker_outcome": worker_outcome,
            "verification_passed": verification_passed,
            "artifact_dir": cycle_result.get("artifact_dir"),
        },
        "worker_session_delta": worker_delta,
        "worker_session_at_most_one": cycle_worker_sessions <= 1,
        "research_call_count": after["research_call_count"],
        "cost_artifact": cost_artifact,
        "target_file_delta": verified_delta,
        "verification_passed": verification_passed,
        "over_budget": over_budget,
        "budget_blocked": budget_blocked,
        "policy_after_cycle": {"allow_expensive_execution": bool(policy_after_cycle.get("allow_expensive_execution"))},
        "reset": reset_info,
        "policy_after_reset": {
            "allow_expensive_execution": bool(policy_after_reset.get("allow_expensive_execution")),
            "pin_expensive_execution": bool(policy_after_reset.get("pin_expensive_execution")),
        },
        "returned_to_cheap_defaults": not bool(policy_after_reset.get("allow_expensive_execution")),
        "budget": budget_status(),
        "passed": (
            cycle_worker_sessions <= 1
            and not bool(policy_after_reset.get("allow_expensive_execution"))
            and failure_class is None
            and not (live_run and cycle_result.get("stopped"))
        ),
    }
    if over_budget:
        proof["budget_blocked_evidence"] = {
            "over_cycle_token_cap": True,
            "budget_blocked": budget_blocked,
            "cost_artifact": cost_artifact,
        }
    if live_run:
        if failure_class:
            _feed_live_failure(failure_class, work_id=work_id, cycle_id=cycle_id, detail=str(verified_delta.get("summary") or ""))
        else:
            _persist_live_success(
                work_id=work_id,
                cycle_id=cycle_id,
                changed_files=changed_files,
                verification_passed=verification_passed,
                worker_outcome=worker_outcome,
            )
        if ltw.is_external_target() and ltw.target_proof_satisfied() and not __import__('loop_backlog', fromlist=['is_proof_work_item']).is_proof_work_item({"work_id": work_id}):
            target_work_proof = dict(proof)
            target_work_proof["phase"] = "target_product_work"
            target_work_proof["proof_mode"] = "real_target_backlog"
            _persist_target_work_proof(target_work_proof)
        _write_json(LIVE_TARGET_CYCLE_PROOF_PATH, proof)
    else:
        _write_json(TARGET_COST_PROOF_PATH, proof)
    clear_run_profile()
    os.environ.pop("PURPLE_HALO_RUN_PROFILE", None)
    return proof


def run_proof_sequence(*, skip_expensive: bool = False, dry_worker: bool = False) -> dict[str, Any]:
    shadow = run_cheap_default_shadow()
    expensive = None if skip_expensive else run_controlled_expensive_single_cycle(dry_worker=dry_worker)
    summary = {
        "started_at": shadow.get("started_at"),
        "finished_at": _now_iso(),
        "cheap_default_shadow": shadow,
        "controlled_expensive_single_cycle": expensive,
        "passed": shadow.get("passed") and (expensive.get("passed") if expensive else True),
    }
    _write_json(RUNTIME / "economy_proof_sequence.json", summary)
    return summary


def self_check() -> None:
    from loop_cost_policy import RUN_PROFILES, get_run_profile, set_run_profile, clear_run_profile

    assert "cheap_default_shadow" in RUN_PROFILES
    assert "controlled_expensive_single_cycle" in RUN_PROFILES
    set_run_profile("cheap_default_shadow")
    assert get_run_profile() == "cheap_default_shadow"
    clear_run_profile()
    shadow = run_cheap_default_shadow()
    assert shadow.get("passed"), shadow.get("checks")
    assert ECONOMY_PROOF_PATH.is_file()
    print("loop-economy-proof: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo economy proof mode")
    parser.add_argument("command", nargs="?", choices=["shadow", "expensive", "run", "status"])
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--skip-expensive", action="store_true")
    parser.add_argument("--dry-worker", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.command == "shadow":
        result = run_cheap_default_shadow()
        print(json.dumps(result, indent=2))
        return 0 if result.get("passed") else 1
    if args.command == "expensive":
        result = run_controlled_expensive_single_cycle(dry_worker=args.dry_worker)
        print(json.dumps(result, indent=2))
        return 0 if result.get("passed") else 1
    if args.command == "run":
        result = run_proof_sequence(skip_expensive=args.skip_expensive, dry_worker=args.dry_worker)
        print(json.dumps(result, indent=2))
        return 0 if result.get("passed") else 1
    if args.command == "status":
        payload = {}
        if ECONOMY_PROOF_PATH.is_file():
            payload["economy_proof"] = json.loads(ECONOMY_PROOF_PATH.read_text(encoding="utf-8"))
        if TARGET_COST_PROOF_PATH.is_file():
            payload["target_cycle_cost_proof"] = json.loads(TARGET_COST_PROOF_PATH.read_text(encoding="utf-8"))
        if LIVE_TARGET_CYCLE_PROOF_PATH.is_file():
            payload["live_target_cycle_proof"] = json.loads(LIVE_TARGET_CYCLE_PROOF_PATH.read_text(encoding="utf-8"))
        print(json.dumps(payload, indent=2))
        return 0
    parser.error("specify shadow, expensive, run, status, or --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())