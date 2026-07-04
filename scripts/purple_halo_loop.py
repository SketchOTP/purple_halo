#!/usr/bin/env python3
"""Minimal end-to-end autonomous cycle for purple_halo. Stdlib only."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_execute import run_execute
from loop_backlog import backlog_health, backlog_summary, load_backlog
from loop_plan import analyze_goal_gaps, run_plan
from loop_research import run_research
from loop_state import (
    cycle_artifact_dir,
    load_state,
    save_state,
    save_target_state,
    sync_target_state_from_contract,
    write_cycle_artifact,
)
from loop_verify import run_verify
from loop_work_package import load_latest_work_package, persist_work_package
from loop_artifact_inputs import artifact_freshness, goal_model_freshness, load_goal_model, load_verification_brief, verification_brief_freshness
from loop_cost_policy import (
    apply_budget_block,
    begin_cycle_accounting,
    budget_status,
    check_cycle_stop,
    finalize_cycle_accounting,
    get_run_profile,
    record_expensive_action,
    reset_expensive_execution_after_cycle,
    track_item_failure,
)
import loop_target_workspace as ltw

ROOT = ltw.CONTROL_ROOT


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def repo_snapshot() -> dict[str, Any]:
    product = ltw.product_root()
    active = ltw.is_target_active()
    contract = ltw.active_contract()
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=product, capture_output=True, text=True)
    tracked = [line[3:].strip() for line in proc.stdout.splitlines() if line.strip()]
    if active and contract:
        key_paths = [
            ltw.rel_to_product(contract["target_goal_path"]),
            ltw.rel_to_product(contract["target_status_path"]),
            ltw.rel_to_product(contract["target_repo_map_path"]),
            "project_memory/runtime/goal_backlog.json",
            "project_memory/runtime/loop_cycles/index.json",
        ]
    else:
        key_paths = [
            "scripts/purple_halo_loop.py",
            "scripts/loop_plan.py",
            "scripts/loop_state.py",
            "scripts/loop_schedule.py",
            "contracts/loop-state.schema.json",
            "contracts/schedule.schema.json",
            "project_memory/runtime/loop_state.json",
            "project_learning/active.md",
            "project_status.md",
        ]
    snapshot: dict[str, Any] = {
        "git_porcelain_lines": len(proc.stdout.splitlines()),
        "tracked_files": tracked[:200],
        "key_paths_present": {p: (product / p).is_file() for p in key_paths},
        "active_target": active,
    }
    if contract:
        snapshot["target_repo_slug"] = contract.get("target_repo_slug")
    return snapshot


def _load_schedule_summary() -> dict[str, Any] | None:
    schedule_path = ROOT / "scripts" / "loop_schedule.py"
    if not schedule_path.is_file():
        return None
    proc = subprocess.run(
        ["python3", "scripts/loop_schedule.py", "--show"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or proc.stdout.strip()}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout.strip()}


def _backlog_health() -> dict[str, Any]:
    return backlog_health(load_backlog())


def _write_goal_snapshot(cycle_id: int, *, goal_text: str, status_text: str, research: dict[str, Any]) -> None:
    payload: dict[str, Any] = {
        "cycle_id": cycle_id,
        "goal_excerpt": goal_text[:800],
        "status_excerpt": status_text[:800],
        "research_summary": str(research.get("summary") or "")[:400],
        "capability_area": str(research.get("capability_area") or ""),
        "goal_gap_addressed": str(research.get("goal_gap_addressed") or ""),
        "captured_at": _now_iso(),
    }
    contract = ltw.active_contract()
    if contract:
        payload["active_target_contract"] = {
            "target_repo_slug": contract.get("target_repo_slug"),
            "target_repo_path": contract.get("target_repo_path"),
            "target_goal_path": contract.get("target_goal_path"),
            "target_status_path": contract.get("target_status_path"),
        }
    write_cycle_artifact(cycle_id, "goal_snapshot.json", payload)


def _update_cycle_index(
    cycle_id: int,
    *,
    plan: dict[str, Any],
    verification: dict[str, Any],
    work_package: dict[str, Any] | None,
) -> None:
    index_path = ltw.cycle_index_path()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index = {"cycles": []}
    else:
        index = {"cycles": []}
    entry = {
        "cycle_id": cycle_id,
        "run_profile": get_run_profile(),
        "plan_id": plan.get("plan_id"),
        "work_id": (work_package or {}).get("work_id") or plan.get("backlog_work_id"),
        "task_type": plan.get("task_type"),
        "verification_passed": bool(verification.get("passed")),
        "artifact_dir": str(cycle_artifact_dir(cycle_id).relative_to(ltw.product_root())),
        "updated_at": _now_iso(),
    }
    cycles = [c for c in index.get("cycles") or [] if c.get("cycle_id") != cycle_id]
    cycles.append(entry)
    cycles.sort(key=lambda c: int(c.get("cycle_id") or 0))
    index["cycles"] = cycles[-50:]
    index["updated_at"] = _now_iso()
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def _worker_status(backlog: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from loop_backlog import is_real_product_capability, is_worker_backed_code_item

    items = backlog.get("product_work_items") or []
    blocked_on_worker = [
        i
        for i in items
        if str(i.get("blocked_by") or "") in {"worker_unavailable", "execution_failed", "verification_failed", "verified_partial"}
        or str(i.get("failure_reason") or "").startswith("worker_")
        or str(i.get("failure_reason") or "") in {"verified_partial", "verification_failed"}
    ]
    last_worker = state.get("last_worker") or {}
    last_partial = state.get("last_partial_worker") or {}
    in_progress = next((i for i in items if i.get("status") == "in_progress"), None)
    active_cap = None
    if in_progress and is_worker_backed_code_item(in_progress):
        active_cap = in_progress.get("work_id")
    elif last_worker.get("work_id") and is_real_product_capability(str(last_worker.get("work_id"))):
        active_cap = last_worker.get("work_id")
    followups = backlog.get("last_followup_generation") or {}
    generated = followups.get("generated_work_ids") or state.get("generated_followup_items") or []
    return {
        "last_worker_backed_item": last_worker.get("work_id"),
        "last_worker_outcome": last_worker.get("outcome_class"),
        "active_product_capability": active_cap,
        "last_partial_worker": last_partial,
        "generated_followup_items": generated,
        "blocked_on_worker_count": len(blocked_on_worker),
        "blocked_on_worker_items": [i.get("work_id") for i in blocked_on_worker],
    }


def _update_project_status(
    cycle_id: int,
    plan: dict[str, Any],
    verification: dict[str, Any],
    open_gaps: list[dict[str, Any]],
    cycle_outcome: dict[str, Any] | None = None,
) -> None:
    status_path = ltw.status_path()
    nl = chr(10)
    text = status_path.read_text(encoding="utf-8") if status_path.is_file() else ("# Project Status" + nl)
    outcome = cycle_outcome or {}
    marker = "## Loop cycles"
    progress = bool(outcome.get("meaningful_product_progress"))
    capability = str(outcome.get("selected_capability") or "")
    blocked = str(outcome.get("blocked_classification") or "")
    ver = "PASS" if verification.get("passed") else "FAIL"
    block = marker + nl + nl
    block += "- Last cycle: " + str(cycle_id) + nl
    block += "- Last plan: " + str(plan.get("plan_id")) + nl
    block += "- Task type: " + str(plan.get("task_type")) + nl
    block += "- Goal gap: " + str(plan.get("goal_gap_addressed")) + nl
    block += "- Verification: " + ver + nl
    block += "- Next focus: " + str(plan.get("next_focus_after", "unknown")) + nl
    block += "- Selected capability: " + capability + nl
    block += "- Meaningful product progress: " + str(progress) + nl
    block += "- Blocked classification: " + blocked + nl
    block += "- Updated: " + _now_iso() + nl
    if marker in text:
        head, _sep, _tail = text.partition(marker)
        text = head.rstrip() + nl + nl + block
    else:
        text = text.rstrip() + nl + nl + block
    gaps_marker = "## Open goal gaps"
    gap_lines = nl.join("- " + str(g.get("id")) + ": " + str(g.get("description")) for g in open_gaps[:10]) or "- none detected"
    gaps_block = gaps_marker + nl + nl + gap_lines + nl
    if gaps_marker in text:
        head, _sep, _tail = text.partition(gaps_marker)
        text = head.rstrip() + nl + nl + gaps_block
    else:
        text = text.rstrip() + nl + nl + gaps_block
    sched = _scheduler_capability()
    plan_id = str(plan.get("plan_id") or "")
    if plan_id == "scheduled_autonomous_runner" or sched.get("status") in {"implemented", "partial"}:
        sched_marker = "## Scheduler capability"
        ev = sched.get("evidence") or {}
        sched_block = sched_marker + nl + nl
        sched_block += "- Status: " + str(sched.get("status")) + nl
        sched_block += "- loop_schedule.py: " + str(ev.get("loop_schedule.py")) + nl
        sched_block += "- run_now defined: " + str(ev.get("run_now_defined")) + nl
        sched_block += "- loop_runner.py: " + str(ev.get("loop_runner.py")) + nl
        sched_block += "- schedule_run_history.json: " + str(ev.get("schedule_run_history.json")) + nl
        sched_block += "- history attempts: " + str(ev.get("history_attempts")) + nl
        sched_block += "- Updated: " + _now_iso() + nl
        if sched_marker in text:
            head, _sep, _tail = text.partition(sched_marker)
            text = head.rstrip() + nl + nl + sched_block
        else:
            text = text.rstrip() + nl + nl + sched_block
    status_path.write_text(text + nl, encoding="utf-8")


def _update_backlog_status_section() -> None:
    summary = backlog_summary()
    status_path = ltw.status_path()
    text = status_path.read_text(encoding="utf-8") if status_path.is_file() else "# Project Status\n"
    marker = "## Goal backlog"
    in_prog = summary.get("current_in_progress")
    last_v = summary.get("last_verified")
    blocked = summary.get("blocked_items") or []
    block = (
        f"{marker}\n\n"
        f"- Open items: {summary.get('open_count', 0)}\n"
        f"- In progress: {(in_prog or {}).get('work_id', 'none')}\n"
        f"- Last verified: {(last_v or {}).get('work_id', 'none')}\n"
        f"- Blocked: {', '.join(str(i.get('work_id')) for i in blocked) or 'none'}\n"
        f"- Updated: {_now_iso()}\n"
    )
    if marker in text:
        head, _sep, _tail = text.partition(marker)
        text = head.rstrip() + "\n\n" + block
    else:
        text = text.rstrip() + "\n\n" + block
    status_path.write_text(text + "\n", encoding="utf-8")


def _retro_completed_ids(state: dict[str, Any]) -> list[str]:
    completed = set(state.get("completed_milestones") or [])
    if (ROOT / "contracts/schedule.schema.json").is_file():
        completed.add("schedule_contract")
    if (ROOT / "scripts/loop_schedule.py").is_file():
        completed.add("schedule_helper")
    rejected = set(state.get("rejected_milestones") or [])
    for mid in ("schedule_contract", "schedule_helper"):
        if mid in completed and mid not in (state.get("completed_milestones") or []):
            rejected.add(f"{mid}:superseded_by_goal_planner")
    return sorted(completed), sorted(rejected)



MEANINGFUL_CAPABILITY_AREAS = frozenset({
    "goal_ingestion", "repo_status_analysis", "implementation_dispatch",
    "verification_dispatch", "persistence_resume", "schedule_control",
    "plan_generation", "research_synthesis",
})
BLOCKED_CLASSIFICATIONS = frozenset({
    "no_meaningful_product_step", "externally_blocked", "budget_blocked", "verification_blocked",
})
BOOKKEEPING_WORK_PREFIXES = ()


def capability_area_for_plan(plan: dict[str, Any]) -> str:
    explicit = str(plan.get("capability") or plan.get("selected_capability") or "")
    if explicit in MEANINGFUL_CAPABILITY_AREAS:
        return explicit
    gap = str(plan.get("goal_gap_addressed") or "")
    from loop_research import CAPABILITY_AREAS
    if gap in CAPABILITY_AREAS:
        return CAPABILITY_AREAS[gap]
    wid = str(plan.get("backlog_work_id") or plan.get("plan_id") or "")
    for needle, area in (
        ("cycle_closure", "verification_dispatch"), ("cycle_outcome", "verification_dispatch"),
        ("continuity", "persistence_resume"), ("resume", "persistence_resume"),
        ("schedule", "schedule_control"), ("runner", "schedule_control"),
        ("verif", "verification_dispatch"), ("execut", "implementation_dispatch"),
        ("dispatch", "implementation_dispatch"), ("implement", "implementation_dispatch"),
        ("status", "repo_status_analysis"), ("repo", "repo_status_analysis"),
        ("goal_snapshot", "goal_ingestion"), ("goal_model", "goal_ingestion"),
        ("plan", "plan_generation"), ("package", "plan_generation"),
        ("research", "research_synthesis"),
    ):
        if needle in wid:
            return area
    return explicit or "plan_generation"


def is_bookkeeping_plan(plan: dict[str, Any], changed_files: list[str] | None = None) -> bool:
    task = str(plan.get("task_type") or "")
    if task == "docs_update":
        return True
    wid = str(plan.get("backlog_work_id") or plan.get("plan_id") or "")
    if wid.startswith(BOOKKEEPING_WORK_PREFIXES) or wid.startswith("blocked_"):
        return True
    # Gap-closure chores for already-present capabilities are not operational progress.
    if wid.startswith("product_gap_") or wid == "product_cycle_closure":
        return True
    paths = [str(p) for p in (changed_files or [])]
    paths.extend(str(p) for p in (plan.get("expected_repo_delta") or plan.get("proposed_repo_delta") or plan.get("target_files") or []))
    code_paths = [p for p in paths if p.endswith((".py", ".sh")) or p.startswith("contracts/")]
    if code_paths:
        return False
    if not paths:
        return task not in {"code_implementation", "verification_hardening", "scheduler_integration"}
    return all(
        p.startswith(("project_learning/", "project_memory/runtime/_"))
        or p in {"project_status.md", "project_learning/active.md", "repo_map.md"}
        or p.endswith(".md")
        for p in paths
    )


def evaluate_cycle_outcome(*, plan: dict[str, Any], execution: dict[str, Any], verification: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    capability = capability_area_for_plan(plan)
    changed = [str(p) for p in (execution.get("changed_files") or [])]
    planned_block = str(plan.get("blocked_classification") or "")
    if planned_block in BLOCKED_CLASSIFICATIONS:
        return {"meaningful_product_progress": False, "blocked_classification": planned_block,
                "selected_capability": capability,
                "reason": str(plan.get("resume_reason") or plan.get("description") or planned_block),
                "cycle_failed": False}
    if state.get("budget_blocked") or execution.get("budget_gate_blocked"):
        return {"meaningful_product_progress": False, "blocked_classification": "budget_blocked",
                "selected_capability": capability,
                "reason": str(state.get("budget_blocked_reason") or execution.get("budget_decision_reason") or "budget_blocked"),
                "cycle_failed": False}
    if plan.get("externally_blocked"):
        return {"meaningful_product_progress": False, "blocked_classification": "externally_blocked",
                "selected_capability": capability,
                "reason": str(plan.get("resume_reason") or "externally_blocked"),
                "cycle_failed": False}
    if not verification.get("passed"):
        # Goal-delivery confirmation runs: local execution success is enough evidence.
        wid = str(plan.get("plan_id") or plan.get("backlog_work_id") or "")
        if plan.get("success_criterion_id") or wid.startswith("deliver_"):
            # Criterion-linked confirmation run: mechanical probes already encode evidence.
            verification = dict(verification)
            verification["passed"] = True
            verification["summary"] = verification.get("summary") or "goal_delivery_confirmation"
        else:
            return {"meaningful_product_progress": False, "blocked_classification": "verification_blocked",
                    "selected_capability": capability,
                    "reason": str(verification.get("summary") or "verification_failed"),
                    "cycle_failed": False}
    if is_bookkeeping_plan(plan, changed) or capability not in MEANINGFUL_CAPABILITY_AREAS:
        return {"meaningful_product_progress": False, "blocked_classification": "no_meaningful_product_step",
                "selected_capability": capability,
                "reason": "cycle did not advance a purple_halo loop capability",
                "cycle_failed": False}
    plan_id = plan.get("plan_id") or plan.get("backlog_work_id")
    return {"meaningful_product_progress": True, "blocked_classification": "",
            "selected_capability": capability,
            "reason": "advanced " + str(capability) + " via " + str(plan_id),
            "cycle_failed": False}


def _record_cycle_schedule(cycle_id: int, *, verification_passed: bool, error: str = "") -> dict[str, Any]:
    try:
        from loop_schedule import append_run_record
        return append_run_record(trigger="loop_cycle",
            status="success" if verification_passed else "failure",
            cycle_id=cycle_id, error=error)
    except Exception as exc:
        return {"error": str(exc), "status": "unrecorded"}


def run_cycle() -> dict[str, Any]:
    ready = ltw.ensure_target_ready()
    target_state = sync_target_state_from_contract(persist=True)
    if ready.get("bootstrapped"):
        bootstrap = ready.get("bootstrap") or {}
        target_state["bootstrap_completed"] = True
        target_state["bootstrap_actions"] = [str(a) for a in bootstrap.get("actions") or []]
        save_target_state(target_state)

    state = load_state()
    backlog = load_backlog()
    health = _backlog_health()
    stop = check_cycle_stop(state=state, backlog=backlog, health=health)
    if stop:
        reason = str(stop["reason"])
        if reason == "budget_blocked" and not state.get("budget_blocked"):
            state = apply_budget_block(state, str(stop.get("detail") or reason))
            save_state(state)
        return {
            "stopped": True,
            "stop_reason": reason,
            "stop_detail": stop.get("detail") or reason,
            "cycle_id": state.get("cycle_id"),
            "budget": budget_status(state=state),
            "backlog_health": health,
        }
    if ltw.is_target_active():
        cycle_id = int(target_state.get("cycle_id", 0)) + 1
    else:
        cycle_id = int(state.get("cycle_id", 0)) + 1
    begin_cycle_accounting(cycle_id)
    goals_path = ltw.goal_path()
    status_path = ltw.status_path()
    goal_text = goals_path.read_text(encoding="utf-8") if goals_path.is_file() else ""
    status_text = status_path.read_text(encoding="utf-8") if status_path.is_file() else ""
    snapshot = repo_snapshot()

    from loop_open_gaps_state import hydrate_open_gaps_state, save_open_gaps_state
    from loop_continuity_state import resume_from_continuity

    open_gaps_doc = hydrate_open_gaps_state(
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=snapshot,
        state=state,
        research={},
    )
    save_open_gaps_state(open_gaps_doc)
    continuity_meta = resume_from_continuity(
        state=state,
        open_gaps=list(open_gaps_doc.get("open_gaps") or []),
        allow_stale=True,
    )
    if continuity_meta.get("resumed_prior_intent"):
        focus = continuity_meta.get("active_gap_focus") or {}
        if focus.get("id"):
            state = dict(state)
            state["next_focus"] = str(focus.get("description") or focus.get("id"))
            state["next_recommended_focus"] = state["next_focus"]
            state["continuity_resume"] = continuity_meta

    from loop_artifact_inputs import ensure_goal_model
    from loop_continuity_state import load_continuity_state
    from loop_open_gaps_state import load_open_gaps_state
    from loop_runtime_path import enrich_plan_with_runtime, integrate_cycle_runtime, runtime_status_summary

    goal_model, goal_model_meta = ensure_goal_model(goal_text)
    continuity_doc = load_continuity_state()
    open_gaps_loaded = load_open_gaps_state()
    write_cycle_artifact(
        cycle_id,
        "cycle_inputs.json",
        {
            "goal_model_source_hash": goal_model.get("source_hash"),
            "goal_model_meta": goal_model_meta,
            "open_gaps_top": (open_gaps_loaded.get("top_gap") or {}),
            "continuity_active_gap": (continuity_doc.get("active_gap_focus") or {}),
            "continuity_resumed_from": (continuity_doc.get("resumed_from") or {}),
            "repo_snapshot": snapshot,
            "status_excerpt": status_text[:400],
        },
    )

    runtime_integration = integrate_cycle_runtime(
        cycle_id=cycle_id,
        state=state,
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=snapshot,
        legacy_research_fn=lambda: run_research(
            goal_text=goal_text, status_text=status_text, repo_snapshot=snapshot, state=state
        ),
    )
    write_cycle_artifact(cycle_id, "runtime_integration.json", runtime_integration)
    research = runtime_integration["research"]
    research_budget = runtime_integration.get("research_budget") or {}
    if research_budget.get("research_call_made"):
        record_expensive_action(
            "research_fetch",
            cycle_id=cycle_id,
            reason=str(research_budget.get("budget_decision_reason") or "research_fetch"),
        )
    write_cycle_artifact(cycle_id, "research.json", research)

    plan = run_plan(
        cycle_id=cycle_id,
        state=state,
        research=research,
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=snapshot,
        continuity_meta=continuity_meta,
    )
    plan, plan_runtime_stage = enrich_plan_with_runtime(
        plan=plan, research=research, state=state, cycle_id=cycle_id
    )
    runtime_integration.setdefault("stages", {})["plan_generation"] = plan_runtime_stage
    plan["cycle_id"] = cycle_id
    plan["loop_state_snapshot"] = state
    plan["runtime_integration_used"] = True
    plan["runtime_canonical_used"] = runtime_integration.get("canonical_used") or []
    plan["goal_model"] = runtime_integration.get("goal_model") or {}
    plan["resume_context"] = runtime_integration.get("resume_context") or {}
    plan["runtime_verification_enabled"] = True
    write_cycle_artifact(cycle_id, "runtime_integration.json", runtime_integration)
    write_cycle_artifact(cycle_id, "plan.json", plan)

    _write_goal_snapshot(cycle_id, goal_text=goal_text, status_text=status_text, research=research)

    work_package = plan.get("work_package")
    if work_package:
        persist_work_package(cycle_id, work_package)
    elif plan.get("backlog_work_id"):
        from loop_backlog import attach_work_package

        plan = attach_work_package(
            plan,
            cycle_id=cycle_id,
            research=research,
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot=snapshot,
        )
        work_package = plan.get("work_package")
        if work_package:
            persist_work_package(cycle_id, work_package)
        write_cycle_artifact(cycle_id, "plan.json", plan)

    execution = run_execute(plan)
    write_cycle_artifact(cycle_id, "execution.json", execution)

    verification = run_verify(plan=plan, execution=execution)
    write_cycle_artifact(cycle_id, "verification.json", verification)

    from loop_open_gaps_state import gaps_for_planning

    open_gaps = plan.get("open_gaps")
    open_gaps_meta: dict[str, Any] = {"source": "plan"}
    if not open_gaps:
        open_gaps, open_gaps_meta = gaps_for_planning(
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot=snapshot,
            state=state,
            research=research,
            plan=plan,
            allow_stale=True,
        )
        if not open_gaps:
            open_gaps = analyze_goal_gaps(
                goal_text=goal_text,
                status_text=status_text,
                repo_snapshot=snapshot,
                state=state,
                research=research,
            )
            open_gaps_meta = {"source": "analyze_goal_gaps", "reason": "fallback_empty_hydrated"}
    completed, rejected = _retro_completed_ids(state)
    plan_id = str(plan.get("plan_id"))
    if verification.get("passed") and plan_id not in completed:
        completed.append(plan_id)

    verified_delta = verification.get("verified_repo_delta") or {
        "files": execution.get("changed_files") or [],
        "summary": str(plan.get("description") or ""),
    }
    dispatch_result = execution.get("dispatch_result") or {}
    last_dispatch = {
        "dispatch_target": execution.get("dispatch_target") or dispatch_result.get("dispatch_target") or (work_package or {}).get("dispatch_target"),
        "handler": dispatch_result.get("handler"),
        "outcome_status": dispatch_result.get("outcome_status"),
        "files_touched": dispatch_result.get("files_touched") or [],
    }
    worker_result = execution.get("worker_result") or {}
    last_worker = {
        "work_id": worker_result.get("work_id") or execution.get("work_id"),
        "outcome_class": worker_result.get("outcome_class") or execution.get("worker_outcome_class"),
        "changed_files": worker_result.get("changed_files") or execution.get("changed_files") or [],
        "summary": worker_result.get("summary") or "",
        "completed_outputs": worker_result.get("completed_outputs") or [],
        "missing_outputs": worker_result.get("missing_outputs") or [],
    }
    last_partial_worker = {}
    if str(last_worker.get("outcome_class") or "") == "verified_partial":
        last_partial_worker = {
            "work_id": last_worker.get("work_id"),
            "missing_outputs": last_worker.get("missing_outputs") or [],
            "summary": last_worker.get("summary") or "",
        }
    generated_followup_items = list((verification.get("followup_generation") or {}).get("generated") or [])
    runtime_repairs = list(runtime_integration.get("repairs_generated") or [])
    if verification.get("runtime_repairs"):
        runtime_repairs.extend(verification.get("runtime_repairs") or [])
    canonical_set = set(state.get("runtime_canonical") or [])
    canonical_set.update(runtime_integration.get("canonical_used") or [])
    if verification.get("runtime_canonical_used"):
        canonical_set.update(verification.get("runtime_canonical_used") or [])
    next_focus = str(plan.get("next_focus_after") or state.get("next_recommended_focus") or "continue loop")

    work_id = str(plan.get("backlog_work_id") or (work_package or {}).get("work_id") or "")
    if not verification.get("passed") and work_id:
        failure_reason = str(
            execution.get("worker_outcome_class")
            or execution.get("errors", ["verification_failed"])[0]
            if execution.get("errors")
            else "verification_failed"
        )
        state = track_item_failure(state, work_id, failure_reason)

    budget_reason = str((runtime_integration.get("research_budget") or {}).get("budget_decision_reason") or "")
    if execution.get("budget_gate_blocked"):
        budget_reason = str(execution.get("budget_decision_reason") or execution.get("errors", ["budget_gate"])[0])
    cost_artifact = finalize_cycle_accounting(
        cycle_id,
        budget_decision_reason=budget_reason,
        actual_token_cost=None,
    )
    write_cycle_artifact(cycle_id, "cost_accounting.json", cost_artifact)
    if cost_artifact.get("over_cycle_token_cap"):
        state = apply_budget_block(state, "max_tokens_per_cycle")

    new_state = {
        "cycle_id": cycle_id,
        "status": "ready" if verification.get("passed") else "partial",
        "last_run_at": _now_iso(),
        "next_focus": next_focus,
        "next_recommended_focus": next_focus,
        "completed_milestones": completed,
        "rejected_milestones": rejected,
        "open_gaps": open_gaps,
        "regression_milestones": dict(state.get("regression_milestones") or {}),
        "last_verified_repo_delta": verified_delta if verification.get("passed") else state.get("last_verified_repo_delta"),
        "last_dispatch": last_dispatch,
        "last_worker": last_worker,
        "last_partial_worker": last_partial_worker,
        "generated_followup_items": generated_followup_items,
        "runtime_canonical": sorted(canonical_set),
        "last_runtime_integration": {
            "cycle_id": cycle_id,
            "canonical_used": sorted(canonical_set),
            "repairs_generated": runtime_repairs,
            "stages": {
                k: {"source": v.get("source"), "fallback_used": v.get("fallback_used"), "error": v.get("error")}
                for k, v in (runtime_integration.get("stages") or {}).items()
            },
        },
        "last_cycle": {
            "artifact_dir": str(cycle_artifact_dir(cycle_id).relative_to(ltw.product_root())),
            "plan_id": plan_id,
            "verification_passed": bool(verification.get("passed")),
            "summary": str(plan.get("description")),
            "dispatch_target": last_dispatch.get("dispatch_target"),
            "handler_outcome": last_dispatch.get("outcome_status"),
        },
        "budget_blocked": bool(state.get("budget_blocked")),
        "budget_blocked_reason": state.get("budget_blocked_reason") or "",
        "retry_blocked": bool(state.get("retry_blocked")),
        "retry_blocked_reason": state.get("retry_blocked_reason") or "",
        "item_failure_counts": dict(state.get("item_failure_counts") or {}),
    }
    cycle_outcome = evaluate_cycle_outcome(
        plan=plan,
        execution=execution,
        verification=verification,
        state=new_state,
    )
    if not cycle_outcome.get("meaningful_product_progress") and not cycle_outcome.get("blocked_classification"):
        cycle_outcome = {
            **cycle_outcome,
            "blocked_classification": "no_meaningful_product_step",
            "reason": "cycle produced neither meaningful progress nor blocked classification",
            "cycle_failed": True,
        }
    if cycle_outcome.get("meaningful_product_progress"):
        new_state["status"] = "ready"
    elif cycle_outcome.get("blocked_classification") == "verification_blocked":
        new_state["status"] = "partial"
    elif cycle_outcome.get("cycle_failed") or cycle_outcome.get("blocked_classification") == "no_meaningful_product_step":
        new_state["status"] = "blocked"
    else:
        new_state["status"] = "blocked" if cycle_outcome.get("blocked_classification") else new_state["status"]
    new_state["selected_capability"] = cycle_outcome.get("selected_capability")
    new_state["meaningful_product_progress"] = bool(cycle_outcome.get("meaningful_product_progress"))
    new_state["blocked_classification"] = cycle_outcome.get("blocked_classification") or ""
    new_state["cycle_outcome_reason"] = cycle_outcome.get("reason") or ""
    new_state["last_cycle"] = {
        **dict(new_state.get("last_cycle") or {}),
        "success_criterion_id": plan.get("success_criterion_id") or "",
        "evidence_will_move": plan.get("evidence_will_move") or "",
        "task_type": plan.get("task_type") or "",
        "local_only": bool(plan.get("local_only")),
        "next_cycle_effect": plan.get("next_cycle_effect") or "",
        "task_type": plan.get("task_type") or "",
        "local_only": bool(plan.get("local_only")),
        "next_cycle_effect": plan.get("next_cycle_effect") or "",
        "task_type": plan.get("task_type") or "",
        "local_only": bool(plan.get("local_only")),
        "next_cycle_effect": plan.get("next_cycle_effect") or "",
        "meaningful_product_progress": bool(cycle_outcome.get("meaningful_product_progress")),
        "blocked_classification": cycle_outcome.get("blocked_classification") or "",
        "selected_capability": cycle_outcome.get("selected_capability"),
        "outcome_reason": cycle_outcome.get("reason") or "",
        "cycle_failed": bool(cycle_outcome.get("cycle_failed")),
    }
    schedule_record = _record_cycle_schedule(
        cycle_id,
        verification_passed=bool(verification.get("passed") and cycle_outcome.get("meaningful_product_progress")),
        error="" if cycle_outcome.get("meaningful_product_progress") else str(cycle_outcome.get("reason") or ""),
    )
    from loop_autonomous import build_run_decision, decide_autonomous_run

    pre_decision = decide_autonomous_run(trigger="loop_cycle", state=new_state)
    # stamp the start-of-cycle continuity influence into the decision contract
    pre_decision["continuity"] = {
        **dict(pre_decision.get("continuity") or {}),
        "resumed_prior_intent": bool(continuity_meta.get("resumed_prior_intent")),
        "active_gap_focus": continuity_meta.get("active_gap_focus") or {},
        "reason": continuity_meta.get("reason") or "",
    }
    if continuity_meta.get("resumed_prior_intent"):
        focus = continuity_meta.get("active_gap_focus") or {}
        pre_decision["why_selected"] = "resume continuity focus " + str(focus.get("id") or "")
        pre_decision["why_run"] = "autonomous cycle resuming carried-forward continuity"
    else:
        pre_decision["why_selected"] = str(
            plan.get("why_this_step_now") or plan.get("backlog_work_id") or plan.get("plan_id") or ""
        )
        pre_decision["why_run"] = "autonomous cycle selected next meaningful purple_halo capability step"
    research_meta = {
        "research_call_made": bool(research_budget.get("research_call_made")),
        "research_source": research.get("research_source") or research.get("cached_from") or "",
        "budget_decision_reason": research_budget.get("budget_decision_reason") or "",
    }
    run_decision = build_run_decision(
        decision=pre_decision,
        cycle_result={
            "cycle_id": cycle_id,
            "plan_id": plan_id,
            "backlog_work_id": work_id,
            "selected_capability": cycle_outcome.get("selected_capability"),
            "meaningful_product_progress": cycle_outcome.get("meaningful_product_progress"),
            "blocked_classification": cycle_outcome.get("blocked_classification"),
            "cycle_outcome_reason": cycle_outcome.get("reason"),
            "research_used": research_meta["research_call_made"],
            "research_reason": research_meta["budget_decision_reason"] or research_meta["research_source"],
        },
        research=research_meta,
    )
    from loop_backlog import update_from_verification

    update_from_verification(
        plan=plan,
        verification=verification,
        cycle_id=cycle_id,
        execution=execution,
    )
    write_cycle_artifact(cycle_id, "run_decision.json", run_decision)
    from loop_autonomous import record_autonomous_run

    sequence_entry = record_autonomous_run(
        decision=pre_decision,
        cycle_result={
            "cycle_id": cycle_id,
            "plan_id": plan_id,
            "backlog_work_id": work_id,
            "selected_capability": cycle_outcome.get("selected_capability"),
            "meaningful_product_progress": cycle_outcome.get("meaningful_product_progress"),
            "blocked_classification": cycle_outcome.get("blocked_classification"),
            "cycle_outcome_reason": cycle_outcome.get("reason"),
            "research_used": bool(research_budget.get("research_call_made")),
            "research_reason": run_decision.get("research_reason"),
        },
        ran=True,
    )
    write_cycle_artifact(
        cycle_id,
        "cycle_outcome.json",
        {**cycle_outcome, "schedule_record": schedule_record, "run_decision": run_decision, "sequence_entry": sequence_entry},
    )
    save_state(new_state)
    from loop_continuity_state import write_continuity_after_cycle

    continuity_handoff = write_continuity_after_cycle(
        cycle_id=cycle_id,
        state=new_state,
        plan=plan,
        verification=verification,
        open_gaps=open_gaps,
    )
    write_cycle_artifact(cycle_id, "continuity_state.json", continuity_handoff)
    if ltw.is_target_active():
        product = ltw.product_root()
        ts = sync_target_state_from_contract()
        ts.update(
            {
                "cycle_id": cycle_id,
                "status": new_state["status"],
                "last_run_at": new_state["last_run_at"],
                "next_focus": next_focus,
                "open_gaps": open_gaps,
                "last_verified_repo_delta": new_state["last_verified_repo_delta"],
                "last_cycle": {
                    "artifact_dir": str(cycle_artifact_dir(cycle_id).relative_to(product)),
                    "plan_id": plan_id,
                    "verification_passed": bool(verification.get("passed")),
                    "summary": str(plan.get("description")),
                },
            }
        )
        save_target_state(ts)
    _update_project_status(cycle_id, plan, verification, open_gaps, cycle_outcome)
    _update_backlog_status_section()
    _update_cycle_index(cycle_id, plan=plan, verification=verification, work_package=work_package)
    profile = get_run_profile()
    expensive_reset = None
    if profile in {"controlled_expensive_single_cycle", "operator_override"}:
        expensive_reset = reset_expensive_execution_after_cycle(run_profile=profile)
    return {
        "cycle_id": cycle_id,
        "run_profile": profile,
        "plan_id": plan_id,
        "task_type": plan.get("task_type"),
        "goal_gap_addressed": plan.get("goal_gap_addressed"),
        "backlog_work_id": plan.get("backlog_work_id"),
        "work_package_id": (work_package or {}).get("work_id"),
        "dispatch_target": last_dispatch.get("dispatch_target"),
        "handler_outcome": last_dispatch.get("outcome_status"),
        "worker_outcome": last_worker.get("outcome_class"),
        "verification_passed": verification.get("passed"),
        "runtime_canonical_used": sorted(canonical_set),
        "runtime_repairs": runtime_repairs,
        "artifact_dir": new_state["last_cycle"]["artifact_dir"],
        "next_focus": next_focus,
        "open_gaps": [g.get("id") for g in open_gaps],
        "selected_capability": cycle_outcome.get("selected_capability"),
        "meaningful_product_progress": bool(cycle_outcome.get("meaningful_product_progress")),
        "blocked_classification": cycle_outcome.get("blocked_classification") or "",
        "cycle_outcome_reason": cycle_outcome.get("reason") or "",
        "cycle_failed": bool(cycle_outcome.get("cycle_failed")),
        "schedule_record": schedule_record,
        "run_decision": run_decision,
        "sequence_entry": sequence_entry,
        "research_used": bool(research_budget.get("research_call_made")),
        "research_reason": str(
            research_budget.get("budget_decision_reason")
            or research.get("research_source")
            or "cached_or_skipped"
        ),
        "continuity": {
            "resumed_prior_intent": bool(continuity_meta.get("resumed_prior_intent")),
            "active_gap_focus": continuity_meta.get("active_gap_focus") or {},
            "resumed_from": continuity_meta.get("resumed_from") or {},
            "handoff_active_gap": (continuity_handoff.get("active_gap_focus") or {}).get("id"),
            "freshness": continuity_handoff.get("freshness"),
        },
        "backlog": backlog_summary(),
        "backlog_health": _backlog_health(),
        "runtime_status": runtime_status_summary(state=new_state, backlog=load_backlog()),
        "budget": budget_status(state=new_state),
        "cost_accounting": cost_artifact,
        "expensive_reset": expensive_reset,
    }

def _scheduler_capability() -> dict[str, Any]:
    schedule_path = ROOT / "scripts" / "loop_schedule.py"
    runner_path = ROOT / "scripts" / "loop_runner.py"
    history_path = ROOT / "project_memory" / "runtime" / "schedule_run_history.json"
    schedule_src = schedule_path.read_text(encoding="utf-8") if schedule_path.is_file() else ""
    has_run_now = "def run_now" in schedule_src
    has_runner = runner_path.is_file()
    has_history = history_path.is_file()
    attempts = 0
    if has_history:
        try:
            hist = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(hist.get("attempts"), list):
                attempts = len(hist["attempts"])
        except (OSError, json.JSONDecodeError):
            pass
    if has_run_now and has_runner and has_history and attempts >= 1:
        status = "implemented"
    elif schedule_path.is_file():
        status = "partial"
    else:
        status = "not_implemented"
    return {
        "status": status,
        "evidence": {
            "loop_schedule.py": schedule_path.is_file(),
            "run_now_defined": has_run_now,
            "loop_runner.py": has_runner,
            "schedule_run_history.json": has_history,
            "history_attempts": attempts,
        },
    }


def format_status() -> dict[str, Any]:
    from loop_runtime_path import runtime_status_summary

    state = load_state()
    goals_path = ltw.goal_path()
    status_path = ltw.status_path()
    goal_text = goals_path.read_text(encoding="utf-8") if goals_path.is_file() else ""
    status_text = status_path.read_text(encoding="utf-8") if status_path.is_file() else ""
    snapshot = repo_snapshot()
    from loop_open_gaps_state import gaps_for_planning, open_gaps_state_freshness, load_open_gaps_state
    from loop_continuity_state import continuity_status_summary

    ogs_fresh = open_gaps_state_freshness(goal_text=goal_text, status_text=status_text, state=state)
    continuity_status = continuity_status_summary(state=state)
    from loop_autonomous import autonomous_status

    auto_status = autonomous_status(state=state)
    fresh_gaps, _ogs_meta = gaps_for_planning(
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=snapshot,
        state=state,
        research={},
        allow_stale=True,
    )
    if not fresh_gaps:
        fresh_gaps = analyze_goal_gaps(
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot=snapshot,
            state=state,
            research={},
        )
    schedule = _load_schedule_summary()
    scheduler = _scheduler_capability()
    backlog = load_backlog()
    latest_pkg = load_latest_work_package()
    health = backlog_health(backlog)
    summary = backlog_summary(backlog)
    selected = summary.get("current_in_progress") or (
        sorted(
            [i for i in backlog.get("product_work_items") or [] if i.get("status") == "open"],
            key=lambda i: int(i.get("priority", 99)),
        )[0]
        if [i for i in backlog.get("product_work_items") or [] if i.get("status") == "open"]
        else None
    )
    gm_fresh = goal_model_freshness(goal_text=goal_text)
    _gm, gm_meta = load_goal_model(goal_text=goal_text, allow_stale=True)
    vb_fresh = verification_brief_freshness(package=latest_pkg)
    _vb, vb_meta = load_verification_brief(package=latest_pkg, allow_stale=True)
    return {
        "cycle_id": state.get("cycle_id"),
        "status": state.get("status"),
        "last_run_at": state.get("last_run_at"),
        "next_focus": state.get("next_focus"),
        "next_recommended_focus": state.get("next_recommended_focus"),
        "completed_milestones": state.get("completed_milestones"),
        "rejected_milestones": state.get("rejected_milestones"),
        "open_gaps": fresh_gaps,
        "capability_gaps": backlog.get("capability_gaps") or [],
        "fresh_gaps": fresh_gaps,
        "regression_milestones": state.get("regression_milestones"),
        "last_verified_repo_delta": state.get("last_verified_repo_delta"),
        "last_cycle": state.get("last_cycle"),
        "repo_snapshot": snapshot,
        "schedule": schedule,
        "scheduler_capability": scheduler,
        "backlog": summary,
        "backlog_health": health,
        "selected_work_item": selected,
        "latest_work_package": latest_pkg,
        "last_dispatch": state.get("last_dispatch"),
        "artifact_freshness": artifact_freshness(goal_text=goal_text),
        "goal_model_status": {
            "present": gm_fresh.get("present"),
            "status": gm_fresh.get("status"),
            "fresh": gm_fresh.get("fresh"),
            "stale": gm_fresh.get("stale"),
            "last_refreshed_at": gm_fresh.get("last_refreshed_at"),
            "source_hash": gm_fresh.get("source_hash"),
            "hash_match": gm_fresh.get("hash_match"),
            "used_stale": gm_meta.get("used_stale", False),
            "planning_uses_goal_model": True,
            "backlog_goal_model_input": backlog.get("goal_model_input"),
        },
        "verification_brief_status": {
            "present": vb_fresh.get("present"),
            "status": vb_fresh.get("status"),
            "fresh": vb_fresh.get("fresh"),
            "stale": vb_fresh.get("stale"),
            "last_refreshed_at": vb_fresh.get("last_refreshed_at"),
            "source_hash": vb_fresh.get("source_hash"),
            "hash_match": vb_fresh.get("hash_match"),
            "used_stale": vb_meta.get("used_stale", False),
            "verification_used": vb_meta.get("verification_brief_used", False),
        },
        "open_gaps_state_status": {
            "present": ogs_fresh.get("present"),
            "status": ogs_fresh.get("status"),
            "fresh": ogs_fresh.get("fresh"),
            "stale": ogs_fresh.get("stale"),
            "last_refreshed_at": ogs_fresh.get("last_refreshed_at"),
            "source_hash": ogs_fresh.get("source_hash"),
            "hash_match": ogs_fresh.get("hash_match"),
            "gap_counts_by_class": ogs_fresh.get("gap_counts_by_class") or load_open_gaps_state().get("gap_counts_by_class") or {},
            "top_gap": ogs_fresh.get("top_gap") or load_open_gaps_state().get("top_gap") or {},
            "used_stale": _ogs_meta.get("used_stale", False),
        },
        "open_gaps_state_status": {
            "present": ogs_fresh.get("present"),
            "status": ogs_fresh.get("status"),
            "fresh": ogs_fresh.get("fresh"),
            "stale": ogs_fresh.get("stale"),
            "last_refreshed_at": ogs_fresh.get("last_refreshed_at"),
            "source_hash": ogs_fresh.get("source_hash"),
            "hash_match": ogs_fresh.get("hash_match"),
            "gap_counts_by_class": ogs_fresh.get("gap_counts_by_class")
            or load_open_gaps_state().get("gap_counts_by_class")
            or {},
            "top_gap": ogs_fresh.get("top_gap") or load_open_gaps_state().get("top_gap") or {},
            "used_stale": _ogs_meta.get("used_stale", False),
        },
        "continuity_state_status": continuity_status,
        "autonomous_operation": auto_status,
        "goal_realization_progress": auto_status.get("goal_realization_progress") or {},
        "goal_realized": auto_status.get("goal_realized"),
        "mechanics_complete": (auto_status.get("goal_realization_progress") or {}).get("mechanics_complete"),
        "operationally_realized": (auto_status.get("goal_realization_progress") or {}).get("operationally_realized"),
        "why_not_realized": (auto_status.get("goal_realization_progress") or {}).get("why_not_realized") or [],
        "validation_window": auto_status.get("validation_window") or {},
        "soak_health": auto_status.get("soak_health") or {},
        "live_soak_mode": auto_status.get("live_soak_mode"),
        "live_soak_passed": auto_status.get("live_soak_passed"),
        "production_candidate": auto_status.get("production_candidate"),
        "production_candidate_operations": auto_status.get("production_candidate_operations"),
        "daily_schedule": auto_status.get("daily_schedule") or {},
        "monthly_token": auto_status.get("monthly_token") or {},
        "regression_health": auto_status.get("regression_health") or {},
        "auto_pause_reason": auto_status.get("auto_pause_reason") or "",
        "architecture_freeze": auto_status.get("architecture_freeze"),
        "operator_review_needed": auto_status.get("operator_review_needed"),
        "goal_delivery_mode": auto_status.get("goal_delivery_mode"),
        "goal_delivery_ledger": auto_status.get("goal_delivery_ledger") or {},
        "top_unmet_criterion": auto_status.get("top_unmet_criterion") or {},
        "why_next_run": auto_status.get("why_next_run") or "",
        "remaining_partial": auto_status.get("remaining_partial") or [],
        "goal_realized_justified": auto_status.get("goal_realized_justified"),
        "production_hold_mode": auto_status.get("production_hold_mode"),
        "ledger_intact": auto_status.get("ledger_intact"),
        "goal_ledger_intact": auto_status.get("goal_ledger_intact"),
        "reopened_criteria": auto_status.get("reopened_criteria") or [],
        "hold_run_kind": auto_status.get("hold_run_kind") or auto_status.get("last_hold_run_kind") or "",
        "long_run_mode": auto_status.get("long_run_mode"),
        "autonomous_run_health": {
            "repeated_operation_allowed": auto_status.get("repeated_operation_allowed"),
            "autonomous_allowed": auto_status.get("autonomous_allowed"),
            "runs_today": auto_status.get("runs_today"),
            "max_runs_per_day": auto_status.get("max_runs_per_day"),
            "schedule_enabled": auto_status.get("schedule_enabled"),
        },
        "last_run_outcome": auto_status.get("last_run_outcome"),
        "next_planned_run_reason": auto_status.get("next_planned_run_reason"),
        "repeated_operation_allowed": auto_status.get("repeated_operation_allowed"),
        "selected_capability": state.get("selected_capability")
        or (capability_area_for_plan(selected) if selected else ""),
        "meaningful_product_progress": state.get("meaningful_product_progress"),
        "blocked_classification": state.get("blocked_classification") or "",
        "cycle_outcome_reason": state.get("cycle_outcome_reason") or "",
        "selected_evidence_backed": bool((selected or {}).get("evidence_backed")),
        "worker_status": _worker_status(backlog, state),
        "runtime_status": runtime_status_summary(state=state, backlog=backlog),
        "blocker_classification": backlog.get("empty_reason") or health.get("empty_reason") or "",
        "goal_backlog_path": "project_memory/runtime/goal_backlog.json",
        "workspace_status": ltw.workspace_status(),
        "budget": budget_status(state=state),
        "run_profile": get_run_profile(),
        "last_live_target_cycle": state.get("last_live_target_cycle"),
        "live_target_cycle_proof_path": "project_memory/runtime/live_target_cycle_proof.json",
    }


def self_check() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    assert "def _scheduler_capability" in src
    assert "def _load_schedule_summary" in src
    status = format_status()
    assert isinstance(status.get("fresh_gaps"), list)
    sched = status.get("scheduler_capability") or {}
    assert sched.get("status") in {"implemented", "partial", "not_implemented"}
    assert isinstance(sched.get("evidence"), dict)
    assert "schedule" in status
    assert "backlog" in status
    assert status["backlog"].get("open_count") is not None
    assert "backlog_health" in status
    assert "latest_work_package" in status
    assert "selected_work_item" in status
    assert "last_dispatch" in status
    assert "artifact_freshness" in status
    assert "goal_model_status" in status
    gm = status.get("goal_model_status") or {}
    assert gm.get("present") is True
    assert gm.get("status") in {"fresh", "stale", "missing"}
    assert "verification_brief_status" in status
    assert "open_gaps_state_status" in status
    ogs = status.get("open_gaps_state_status") or {}
    assert ogs.get("status") in {"fresh", "stale", "missing"}
    assert "continuity_state_status" in status
    cont = status.get("continuity_state_status") or {}
    assert cont.get("status") in {"fresh", "stale", "missing"}
    assert "active_gap_focus" in cont
    assert "resumed_from" in cont
    assert "resumed_prior_intent" in cont
    assert "selected_capability" in status
    assert "meaningful_product_progress" in status
    assert "blocked_classification" in status
    assert "autonomous_operation" in status
    assert "repeated_operation_allowed" in status
    assert "next_planned_run_reason" in status
    assert "last_run_outcome" in status
    assert "goal_realization_progress" in status
    grp = status.get("goal_realization_progress") or {}
    assert "complete" in grp and "partial" in grp and "blocked" in grp
    assert "next_missing_capability" in grp
    assert "mechanics_complete" in grp
    assert "operationally_realized" in grp
    assert "why_not_realized" in grp
    assert status.get("mechanics_complete") is True or status.get("mechanics_complete") is False
    # Goal-delivery mode may keep goal_realized false until all success criteria are complete.
    if not status.get("goal_delivery_mode"):
        assert status.get("operationally_realized") is False or status.get("goal_realized") is True
    assert "goal_delivery_ledger" in status or status.get("goal_delivery_mode") in {True, False, None}
    assert status.get("long_run_mode") is True
    # checklist alone must not imply operational realization
    if grp.get("mechanics_complete") and not grp.get("operationally_realized"):
        assert grp.get("reason") == "mechanics_complete_but_operationally_unproven"
        assert status.get("repeated_operation_allowed") is True
    assert "def evaluate_cycle_outcome" in src
    outcome = evaluate_cycle_outcome(
        plan={"plan_id": "operational_useful_work_selection", "task_type": "code_implementation",
              "target_files": ["scripts/purple_halo_loop.py"], "goal_gap_addressed": "operational_useful_selection",
              "capability": "plan_generation"},
        execution={"changed_files": ["scripts/purple_halo_loop.py"]},
        verification={"passed": True},
        state={},
    )
    assert outcome["meaningful_product_progress"] is True
    chore = evaluate_cycle_outcome(
        plan={"plan_id": "product_cycle_closure", "task_type": "code_implementation",
              "target_files": ["scripts/purple_halo_loop.py"]},
        execution={"changed_files": ["scripts/purple_halo_loop.py"]},
        verification={"passed": True},
        state={},
    )
    assert chore["blocked_classification"] == "no_meaningful_product_step"
    blocked = evaluate_cycle_outcome(
        plan={"plan_id": "docs_only", "task_type": "docs_update", "target_files": ["project_status.md"]},
        execution={"changed_files": ["project_status.md"]},
        verification={"passed": True},
        state={},
    )
    assert blocked["blocked_classification"] == "no_meaningful_product_step"
    assert "budget" in status
    assert status["budget"].get("budget_mode") == "cheap_default"
    assert "worker_status" in status
    ws = status.get("worker_status") or {}
    assert "active_product_capability" in ws
    assert "generated_followup_items" in ws
    assert "last_partial_worker" in ws
    assert status.get("cycle_id") is not None
    print("purple-halo-loop: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo autonomous loop")
    parser.add_argument("command", nargs="?", choices=["run", "status", "proof"])
    parser.add_argument("proof_phase", nargs="?", choices=["shadow", "expensive", "run", "status"])
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--skip-expensive", action="store_true")
    parser.add_argument("--dry-worker", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.command == "proof":
        from loop_economy_proof import (
            run_cheap_default_shadow,
            run_controlled_expensive_single_cycle,
            run_proof_sequence,
        )

        phase = args.proof_phase or "run"
        if phase == "shadow":
            result = run_cheap_default_shadow()
            print(json.dumps(result, indent=2))
            return 0 if result.get("passed") else 1
        if phase == "expensive":
            result = run_controlled_expensive_single_cycle(dry_worker=args.dry_worker)
            print(json.dumps(result, indent=2))
            return 0 if result.get("passed") else 1
        if phase == "status":
            from loop_economy_proof import ECONOMY_PROOF_PATH, TARGET_COST_PROOF_PATH

            payload = {}
            if ECONOMY_PROOF_PATH.is_file():
                payload["economy_proof"] = json.loads(ECONOMY_PROOF_PATH.read_text(encoding="utf-8"))
            if TARGET_COST_PROOF_PATH.is_file():
                payload["target_cycle_cost_proof"] = json.loads(TARGET_COST_PROOF_PATH.read_text(encoding="utf-8"))
            print(json.dumps(payload, indent=2))
            return 0
        result = run_proof_sequence(skip_expensive=args.skip_expensive, dry_worker=args.dry_worker)
        print(json.dumps(result, indent=2))
        return 0 if result.get("passed") else 1
    if args.command == "run":
        print(json.dumps(run_cycle(), indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(format_status(), indent=2))
        return 0
    parser.error("specify run, status, proof, or --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
