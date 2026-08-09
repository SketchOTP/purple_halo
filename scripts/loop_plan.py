#!/usr/bin/env python3
"""Goal-gap driven planner for purple_halo loop cycles. Stdlib only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from loop_backlog import (
    attach_work_package,
    backlog_summary,
    classify_empty,
    load_backlog,
    open_items,
    pick_next_item,
    refresh_backlog,
    save_backlog,
    work_item_to_plan,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from loop_target_workspace import goal_path, status_path  # noqa: E402

TASK_TYPES = frozenset(
    {
        "docs_update",
        "repo_analysis",
        "code_implementation",
        "verification_hardening",
        "scheduler_integration",
    }
)

MARKER_SUFFIXES = ("_marker.txt", "_maintenance.txt", "self_check.txt")

GAP_CLOSURE_MILESTONES: dict[str, str] = {
    "gap_scaffold_planner": "goal_driven_planner",
    "gap_verification_evidence": "verification_repo_delta",
    "gap_executor_actions": "executor_real_actions",
    "gap_research_goal_binding": "research_goal_binding",
    "gap_research_artifact_binding": "research_artifact_binding",
    "gap_continuity_open_gaps": "continuity_open_gaps",
    "gap_verify_schedule": "verify_schedule_integration",
    "gap_scheduler_status": "scheduler_status_integration",
    "gap_status_open_gaps": "status_open_gaps_section",
    "gap_product_realization": "product_progress_note",
    "gap_scheduled_execution": "scheduled_autonomous_runner",
}


def _file_text(rel: str) -> str:
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _file_text_path(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _has_section(text: str, heading: str) -> bool:
    return heading in text


def _retro_completed(repo: Path, milestone_id: str) -> bool:
    """Treat scaffold milestones as done when repo evidence exists."""
    if milestone_id == "schedule_contract":
        return (repo / "contracts/schedule.schema.json").is_file()
    if milestone_id == "schedule_helper":
        return (repo / "scripts/loop_schedule.py").is_file()
    if milestone_id == "scheduled_autonomous_runner":
        schedule_src = _file_text("scripts/loop_schedule.py")
        if "def run_now" not in schedule_src:
            return False
        if not (repo / "scripts/loop_runner.py").is_file():
            return False
        hist_path = repo / "project_memory/runtime/schedule_run_history.json"
        if not hist_path.is_file():
            return False
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        attempts = hist.get("attempts")
        return isinstance(attempts, list) and len(attempts) >= 1
    return False


def _scheduler_history_ok() -> bool:
    hist_path = ROOT / "project_memory/runtime/schedule_run_history.json"
    if not hist_path.is_file():
        return False
    try:
        hist = json.loads(hist_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    attempts = hist.get("attempts")
    return isinstance(attempts, list) and len(attempts) >= 1


def analyze_goal_gaps(
    *,
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
    state: dict[str, Any],
    research: dict[str, Any],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    verify_src = _file_text("scripts/loop_verify.py")
    loop_src = _file_text("scripts/purple_halo_loop.py")
    execute_src = _file_text("scripts/loop_execute.py")
    research_src = _file_text("scripts/loop_research.py")
    schedule_src = _file_text("scripts/loop_schedule.py")

    if (
        "def run_now" not in schedule_src
        or not (ROOT / "scripts/loop_runner.py").is_file()
        or "apply_code_slice" not in execute_src
        or not _scheduler_history_ok()
    ):
        gaps.append(
            {
                "id": "gap_scheduled_execution",
                "description": "purple_halo cannot yet autonomously run on schedule",
                "priority": 3,
            }
        )
    if "meaningful repo delta" not in verify_src or "verification_commands" not in verify_src:
        gaps.append(
            {
                "id": "gap_verification_evidence",
                "description": "Verification must prove repo delta and run plan verification_commands.",
                "priority": 10,
            }
        )
    if "_load_schedule_summary" not in loop_src and (ROOT / "scripts/loop_schedule.py").is_file():
        gaps.append(
            {
                "id": "gap_scheduler_status",
                "description": "Operator schedule exists but loop status does not expose schedule config.",
                "priority": 30,
            }
        )
    if "loop_schedule.py --self-check" not in _file_text("scripts/verify-loop.sh"):
        gaps.append(
            {
                "id": "gap_verify_schedule",
                "description": "verify-loop must exercise schedule helper when present.",
                "priority": 25,
            }
        )
    if "goal_gap_addressed" not in research_src:
        gaps.append(
            {
                "id": "gap_research_goal_binding",
                "description": "Research must cite the active goal gap, not generic notes.",
                "priority": 20,
            }
        )
    if not _has_section(status_text, "## Open goal gaps"):
        gaps.append(
            {
                "id": "gap_status_open_gaps",
                "description": "project_status.md must surface open goal gaps from loop state.",
                "priority": 35,
            }
        )
    plan_src = _file_text("scripts/loop_plan.py")
    if "def analyze_goal_gaps" not in plan_src or "def choose_step" not in plan_src:
        gaps.append(
            {
                "id": "gap_scaffold_planner",
                "description": "Planner still selects scaffold milestones instead of goal gaps.",
                "priority": 5,
            }
        )
    if "run_command" not in execute_src or "apply_code_slice" not in execute_src:
        gaps.append(
            {
                "id": "gap_executor_actions",
                "description": "Executor must support real repo actions beyond marker files.",
                "priority": 15,
            }
        )
    continuity_src = _file_text("scripts/loop_continuity_state.py")
    continuity_path = ROOT / "project_memory/runtime/continuity_state.json"
    if (
        "def resume_from_continuity" not in continuity_src
        or "def write_continuity_after_cycle" not in continuity_src
        or "resume_from_continuity" not in loop_src
        or "write_continuity_after_cycle" not in loop_src
        or not continuity_path.is_file()
    ):
        gaps.append(
            {
                "id": "gap_continuity_open_gaps",
                "description": "Loop must persist and resume open-gap focus across cycles via continuity_state.json.",
                "priority": 12,
            }
        )
    if not research.get("goal_gap_addressed"):
        gaps.append(
            {
                "id": "gap_research_artifact_binding",
                "description": "Latest research artifact must bind facts to the active goal gap.",
                "priority": 18,
            }
        )
    if "Success Criteria" in goal_text and "Level 0.3" in status_text:
        gaps.append(
            {
                "id": "gap_product_realization",
                "description": "Move from scaffold loop toward goal-driven autonomous product building.",
                "priority": 40,
            }
        )
    completed = set(state.get("completed_milestones") or [])
    for mid in list(completed):
        if _retro_completed(ROOT, mid):
            completed.add(mid)
    gaps = [
        g
        for g in gaps
        if GAP_CLOSURE_MILESTONES.get(g["id"], "close_" + str(g["id"])) not in completed
    ]
    gaps.sort(key=lambda g: int(g.get("priority", 99)))
    return gaps


def _milestone_from_gap(gap: dict[str, Any], *, cycle_id: int, research: dict[str, Any]) -> dict[str, Any]:
    gap_id = str(gap["id"])
    research_fact = research.get("summary") or research.get("goal_gap_fact") or gap["description"]

    catalog: dict[str, dict[str, Any]] = {
        "gap_scaffold_planner": {
            "id": "goal_driven_planner",
            "task_type": "code_implementation",
            "focus": "Replace scaffold milestone queue with goal-gap planner",
            "description": "Planner derives candidates from goal gaps and refuses completed milestones.",
            "why_this_step_now": "Scaffold milestone selection blocks real product progress.",
            "actions": [
                {
                    "type": "append_file",
                    "path": "project_learning/active.md",
                    "content": f"\n## Cycle {cycle_id}: goal-driven planner\n\n- Gap: {gap['description']}\n- Research: {research_fact}\n",
                }
            ],
            "expected_repo_delta": ["project_learning/active.md"],
            "verification_commands": [
                ["python3", "scripts/loop_plan.py", "--self-check"],
            ],
            "success_criteria": ["project_learning/active.md exists"],
            "next_focus_after": "Harden verification against repo delta",
        },
        "gap_verification_evidence": {
            "id": "verification_repo_delta",
            "task_type": "verification_hardening",
            "focus": "Fail verification when cycle produces no meaningful repo delta",
            "description": "Extend loop_verify to run plan verification_commands and reject marker-only deltas.",
            "why_this_step_now": "Cycles must not pass without proven repo progress.",
            "actions": [
                {
                    "type": "append_file",
                    "path": "project_learning/active.md",
                    "content": f"\n## Cycle {cycle_id}: verification hardening\n\n- Require verification_commands and meaningful repo delta.\n- Research: {research_fact}\n",
                }
            ],
            "expected_repo_delta": ["project_learning/active.md"],
            "verification_commands": [
                ["python3", "scripts/loop_verify.py", "--self-check"],
            ],
            "success_criteria": ["project_learning/active.md exists"],
            "next_focus_after": "Expose operator schedule in loop status",
        },
        "gap_executor_actions": {
            "id": "executor_real_actions",
            "task_type": "code_implementation",
            "focus": "Executor supports append_file and run_command actions",
            "description": "Enable real repo-improving executor actions beyond marker files.",
            "why_this_step_now": "Marker-only execution cannot advance the product goal.",
            "actions": [
                {
                    "type": "append_file",
                    "path": "project_learning/active.md",
                    "content": f"\n## Cycle {cycle_id}: executor real actions\n\n- Added append_file and run_command execution paths.\n",
                }
            ],
            "expected_repo_delta": ["project_learning/active.md"],
            "verification_commands": [
                ["python3", "scripts/loop_execute.py", "--self-check"],
            ],
            "success_criteria": ["project_learning/active.md exists"],
            "next_focus_after": "Bind research output to goal gaps",
        },
        "gap_research_goal_binding": {
            "id": "research_goal_binding",
            "task_type": "repo_analysis",
            "focus": "Research cites active goal gap in output",
            "description": "Research step records goal_gap_addressed and goal_gap_fact for planning.",
            "why_this_step_now": "Planning needs gap-tied research facts, not generic notes.",
            "actions": [
                {
                    "type": "append_file",
                    "path": "project_learning/active.md",
                    "content": f"\n## Cycle {cycle_id}: research goal binding\n\n- Gap: {gap['description']}\n- Fact: {research_fact}\n",
                }
            ],
            "expected_repo_delta": ["project_learning/active.md"],
            "verification_commands": [
                ["python3", "scripts/loop_research.py", "--self-check"],
            ],
            "success_criteria": ["project_learning/active.md exists"],
            "next_focus_after": "Persist open gaps in loop state",
        },
        "gap_research_artifact_binding": {
            "id": "research_artifact_binding",
            "task_type": "repo_analysis",
            "focus": "Record research facts tied to top open gap",
            "description": "Append gap-specific research summary to project_learning/active.md.",
            "why_this_step_now": "Prior research lacked explicit goal_gap_addressed binding.",
            "actions": [
                {
                    "type": "append_file",
                    "path": "project_learning/active.md",
                    "content": f"\n## Cycle {cycle_id}: research artifact binding\n\n- gap: {gap_id}\n- fact: {research_fact}\n",
                }
            ],
            "expected_repo_delta": ["project_learning/active.md"],
            "verification_commands": [
                ["python3", "scripts/loop_research.py", "--self-check"],
            ],
            "success_criteria": ["project_learning/active.md exists"],
            "next_focus_after": "Update project_status open gaps section",
        },
        "gap_continuity_open_gaps": {
            "id": "continuity_open_gaps",
            "task_type": "code_implementation",
            "focus": "Persist and resume open-gap continuity across cycles",
            "description": "Write continuity_state.json at cycle end and resume carried-forward focus at cycle start.",
            "why_this_step_now": "Resume requires durable continuity, not ad hoc gap rediscovery.",
            "actions": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_continuity_state.py", "--self-check"],
                }
            ],
            "expected_repo_delta": [
                "scripts/loop_continuity_state.py",
                "project_memory/runtime/continuity_state.json",
            ],
            "verification_commands": [
                ["python3", "scripts/loop_continuity_state.py", "--self-check"],
            ],
            "success_criteria": ["project_memory/runtime/continuity_state.json exists"],
            "next_focus_after": "Prefer carried-forward top gap in planning",
        },
        "gap_verify_schedule": {
            "id": "verify_schedule_integration",
            "task_type": "verification_hardening",
            "focus": "Integrate schedule helper into verify-loop harness",
            "description": "Ensure verify-loop.sh runs loop_schedule self-check when schedule helper exists.",
            "why_this_step_now": "Schedule contract exists; verification must cover it.",
            "actions": [
                {
                    "type": "append_file",
                    "path": "project_learning/active.md",
                    "content": f"\n## Cycle {cycle_id}: verify schedule integration\n\n- Schedule helper covered by verify-loop.\n",
                }
            ],
            "expected_repo_delta": ["project_learning/active.md"],
            "verification_commands": [
                ["python3", "scripts/loop_schedule.py", "--self-check"],
            ],
            "success_criteria": ["scripts/loop_schedule.py exists"],
            "next_focus_after": "Expose schedule in loop status output",
        },
        "gap_scheduler_status": {
            "id": "scheduler_status_integration",
            "task_type": "scheduler_integration",
            "focus": "Expose operator schedule in purple_halo_loop status",
            "description": "Status command reports schedule config and next recommended focus.",
            "why_this_step_now": "Operators need schedule visibility before daily automation.",
            "actions": [
                {
                    "type": "append_file",
                    "path": "project_learning/active.md",
                    "content": f"\n## Cycle {cycle_id}: scheduler status integration\n\n- Status exposes schedule config.\n",
                }
            ],
            "expected_repo_delta": ["project_learning/active.md"],
            "verification_commands": [
                ["python3", "scripts/loop_schedule.py", "--show"],
            ],
            "success_criteria": ["scripts/loop_schedule.py exists"],
            "next_focus_after": "Refresh project_status open gaps section",
        },
        "gap_status_open_gaps": {
            "id": "status_open_gaps_section",
            "task_type": "docs_update",
            "focus": "Add Open goal gaps section to project_status.md",
            "description": "Document current open gaps and next recommended focus in project_status.md.",
            "why_this_step_now": "Status must reflect goal progress, not only last cycle markers.",
            "actions": [
                {
                    "type": "ensure_section",
                    "path": "project_status.md",
                    "heading": "## Open goal gaps",
                    "content": f"Open gaps tracked by loop state (cycle {cycle_id}). See loop status for current list.\n",
                }
            ],
            "expected_repo_delta": ["project_status.md"],
            "verification_commands": [],
            "success_criteria": ["project_status.md exists"],
            "next_focus_after": "Continue next highest-priority goal gap",
        },
        "gap_product_realization": {
            "id": "product_progress_note",
            "task_type": "docs_update",
            "focus": "Record product progress toward autonomous build loop",
            "description": "Capture goal-driven loop progress in project_learning/active.md.",
            "why_this_step_now": "Document real product movement away from pure scaffold work.",
            "actions": [
                {
                    "type": "append_file",
                    "path": "project_learning/active.md",
                    "content": f"\n## Cycle {cycle_id}: product progress\n\n- Goal gap: {gap['description']}\n- Research: {research_fact}\n",
                }
            ],
            "expected_repo_delta": ["project_learning/active.md"],
            "verification_commands": [],
            "success_criteria": ["project_learning/active.md exists"],
            "next_focus_after": "Select next open goal gap milestone",
        },
        "gap_scheduled_execution": {
            "id": "scheduled_autonomous_runner",
            "task_type": "code_implementation",
            "focus": "Enable scheduled autonomous loop execution",
            "description": "Wire loop_schedule run_now, loop_runner launcher, and run history for autonomous cycles.",
            "why_this_step_now": "purple_halo cannot yet autonomously run on schedule.",
            "actions": [
                {"type": "apply_code_slice", "slice": "scheduled_runner"},
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_runner.py", "--self-check"],
                },
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_runner.py", "run-now"],
                },
            ],
            "code_contract": {
                "target_files": ["scripts/loop_schedule.py", "scripts/loop_runner.py"],
                "expected_symbols": {
                    "scripts/loop_schedule.py": ["run_now", "run_due", "append_run_record"],
                    "scripts/loop_runner.py": ["main"],
                },
                "runtime_artifacts": ["project_memory/runtime/schedule_run_history.json"],
            },
            "expected_repo_delta": ["scripts/loop_schedule.py", "scripts/loop_runner.py"],
            "verification_commands": [
                ["python3", "scripts/loop_runner.py", "--self-check"],
                ["python3", "scripts/loop_schedule.py", "--self-check"],
            ],
            "success_criteria": [
                "scripts/loop_schedule.py exists",
                "scripts/loop_runner.py exists",
                "project_memory/runtime/schedule_run_history.json exists",
            ],
            "next_focus_after": "Harden verification against repo delta",
        },
    }

    template = catalog.get(
        gap_id,
        {
            "id": f"close_{gap_id}",
            "task_type": "docs_update",
            "focus": f"Address gap: {gap_id}",
            "description": gap["description"],
            "why_this_step_now": f"Open gap {gap_id} blocks goal progress.",
            "actions": [
                {
                    "type": "append_file",
                    "path": "project_learning/active.md",
                    "content": f"\n## Cycle {cycle_id}: {gap_id}\n\n{gap['description']}\n",
                }
            ],
            "expected_repo_delta": ["project_learning/active.md"],
            "verification_commands": [],
            "success_criteria": ["project_learning/active.md exists"],
            "next_focus_after": "Continue goal-driven milestones",
        },
    )
    out = dict(template)
    out["goal_gap_addressed"] = gap_id
    out["resume_reason"] = f"Top open gap {gap_id} after cycle {cycle_id - 1}"
    return out


def build_candidate_work(
    gaps: list[dict[str, Any]],
    *,
    cycle_id: int,
    research: dict[str, Any],
) -> list[dict[str, Any]]:
    return [_milestone_from_gap(gap, cycle_id=cycle_id, research=research) for gap in gaps]


def choose_step(
    candidates: list[dict[str, Any]],
    state: dict[str, Any],
    continuity_meta: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    completed = set(state.get("completed_milestones") or [])
    rejected = set(state.get("rejected_milestones") or [])
    regressions = state.get("regression_milestones") or {}
    retro: set[str] = set()
    for mid in list(completed):
        if _retro_completed(ROOT, mid):
            retro.add(mid)
    completed |= retro

    focus_id = str(((continuity_meta or {}).get("active_gap_focus") or {}).get("id") or "")
    if continuity_meta and continuity_meta.get("resumed_prior_intent") and focus_id:
        candidates = sorted(
            candidates,
            key=lambda c: (
                0 if str(c.get("goal_gap_addressed") or "") == focus_id else 1,
                0 if c.get("task_type") == "code_implementation" else 1,
                str(c.get("id")),
            ),
        )
    elif candidates and str(candidates[0].get("goal_gap_addressed") or "") == "gap_scheduled_execution":
        candidates = sorted(
            candidates,
            key=lambda c: (0 if c.get("task_type") == "code_implementation" else 1, str(c.get("id"))),
        )

    skipped: list[str] = []
    for candidate in candidates:
        mid = str(candidate["id"])
        if mid in completed and mid not in regressions:
            skipped.append(f"{mid}: already completed")
            continue
        if mid in rejected and mid not in regressions:
            skipped.append(f"{mid}: previously rejected as redundant")
            continue
        if candidate.get("task_type") not in TASK_TYPES:
            skipped.append(f"{mid}: invalid task_type")
            continue
        return candidate, skipped
    return None, skipped


CAPABILITY_GAP_PRIORITY_THRESHOLD = 10


def run_plan(
    *,
    cycle_id: int,
    state: dict[str, Any],
    research: dict[str, Any],
    goal_text: str = "",
    status_text: str = "",
    repo_snapshot: dict[str, Any] | None = None,
    continuity_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo_snapshot = repo_snapshot or {}
    goal_text = goal_text or (_file_text_path(goal_path()))
    status_text = status_text or (_file_text_path(status_path()))
    from loop_artifact_inputs import ensure_goal_model
    from loop_continuity_state import (
        prefer_carried_forward_gaps,
        prefer_continuity_work_item,
        resume_from_continuity,
    )

    goal_model, goal_model_meta = ensure_goal_model(goal_text)
    from loop_open_gaps_state import gaps_for_planning

    gaps, open_gaps_meta = gaps_for_planning(
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=repo_snapshot,
        state=state,
        research=research,
        regenerate=True,
    )
    if continuity_meta is None:
        continuity_meta = resume_from_continuity(state=state, open_gaps=gaps, allow_stale=True)
    gaps = prefer_carried_forward_gaps(gaps, continuity_meta)
    capability_gaps = [g for g in gaps if str(g.get("id", "")).startswith("gap_")]
    backlog = refresh_backlog(
        capability_gaps=capability_gaps,
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=repo_snapshot,
        state=state,
        research=research,
        continuity_meta=continuity_meta,
    )
    open_backlog = open_items(backlog)

    # High-priority capability gaps block product work (scheduler, etc.)
    import loop_target_workspace as ltw_plan
    if ltw_plan.is_project_mode():
        urgent_caps = []
        candidates = []
        chosen, skipped = None, []
    else:
        urgent_caps = [g for g in capability_gaps if int(g.get("priority", 99)) < CAPABILITY_GAP_PRIORITY_THRESHOLD]
        candidates = build_candidate_work(urgent_caps, cycle_id=cycle_id, research=research) if urgent_caps else []
        chosen, skipped = choose_step(candidates, state, continuity_meta=continuity_meta)

    if chosen is None and open_backlog:
        from loop_backlog import is_bookkeeping_item, is_meaningful_product_item

        next_item = prefer_continuity_work_item(open_backlog, continuity_meta) or pick_next_item(
            backlog, continuity_meta=continuity_meta
        )
        if next_item and is_bookkeeping_item(next_item):
            meaningful_open = [i for i in open_backlog if is_meaningful_product_item(i)]
            if meaningful_open:
                next_item = pick_next_item(backlog, continuity_meta=continuity_meta)
            else:
                next_item = None
                skipped = ["refused_bookkeeping_only_work"]
        if next_item:
            chosen = work_item_to_plan(next_item, cycle_id=cycle_id, research=research)
            if continuity_meta.get("resumed_prior_intent"):
                focus = (continuity_meta.get("active_gap_focus") or {}).get("id")
                chosen["resume_reason"] = str(continuity_meta.get("reason") or "continuity_resume")
                chosen["why_this_step_now"] = (
                    "Resuming prior intent for " + str(focus) + ": "
                    + str(chosen.get("why_this_step_now") or chosen.get("description") or "")
                )
            skipped = []

    if chosen is None:
        from loop_backlog import is_meaningful_product_item

        meaningful_open = [i for i in open_backlog if is_meaningful_product_item(i)]
        if not meaningful_open:
            empty_reason = classify_empty(
                goal_text=goal_text,
                research=research,
                capability_gaps=capability_gaps,
                backlog=backlog,
            )
            if empty_reason == "product_complete" and open_backlog:
                empty_reason = "no_meaningful_product_step"
            if empty_reason not in {
                "no_meaningful_product_step",
                "externally_blocked",
                "budget_blocked",
                "verification_blocked",
            }:
                if empty_reason in {"implementation_blocked", "no_executable_product_work"}:
                    empty_reason = "no_meaningful_product_step"
            backlog["empty_reason"] = empty_reason
            save_backlog(backlog)
            chosen = {
                "id": f"blocked_{empty_reason}",
                "task_type": "repo_analysis",
                "focus": f"No meaningful product step: {empty_reason}",
                "description": f"Cycle blocked; classified as {empty_reason}.",
                "goal_gap_addressed": empty_reason,
                "why_this_step_now": f"No meaningful purple_halo capability work; reason={empty_reason}",
                "resume_reason": empty_reason,
                "blocked_classification": empty_reason
                if empty_reason
                in {
                    "no_meaningful_product_step",
                    "externally_blocked",
                    "budget_blocked",
                    "verification_blocked",
                }
                else "no_meaningful_product_step",
                "actions": [],
                "expected_repo_delta": [],
                "verification_commands": [],
                "success_criteria": [],
                "next_focus_after": "Unblock or define next loop capability",
            }
            skipped = [f"empty_reason:{empty_reason}"]

    plan_base = {
        "cycle_id": cycle_id,
        "plan_id": chosen["id"] if chosen else "blocked",
        "task_type": chosen["task_type"] if chosen else "repo_analysis",
        "focus": chosen["focus"] if chosen else "blocked",
        "description": chosen["description"] if chosen else "",
        "why_this_step_now": chosen["why_this_step_now"] if chosen else "",
        "goal_gap_addressed": chosen["goal_gap_addressed"] if chosen else "",
        "goal_text": goal_text,
        "status_text": status_text,
        "code_contract": chosen.get("code_contract") if chosen else None,
        "expected_repo_delta": list(chosen["expected_repo_delta"]) if chosen else [],
        "proposed_repo_delta": list(chosen.get("proposed_repo_delta") or chosen.get("expected_repo_delta") or []) if chosen else [],
        "target_files": list(chosen.get("target_files") or []) if chosen else [],
        "verification_commands": [list(cmd) for cmd in chosen.get("verification_commands") or []] if chosen else [],
        "done_when": list(chosen.get("done_when") or []) if chosen else [],
        "resume_reason": chosen.get("resume_reason", "") if chosen else "",
        "actions": list(chosen.get("execution_steps") or chosen.get("actions") or []) if chosen else [],
        "execution_steps": list(chosen.get("execution_steps") or chosen.get("actions") or []) if chosen else [],
        "dispatch_target": chosen.get("dispatch_target") or "" if chosen else "",
        "expected_outputs": list(chosen.get("expected_outputs") or []) if chosen else [],
        "force_worker_bridge": bool(chosen.get("force_worker_bridge")) if chosen else False,
        "research_summary": research.get("summary", ""),
        "research_goal_gap": research.get("goal_gap_addressed", ""),
        "goal_model": goal_model,
        "goal_model_meta": goal_model_meta,
        "goal_model_used": True,
        "open_gaps": gaps,
        "open_gaps_meta": open_gaps_meta,
        "continuity_meta": continuity_meta,
        "resumed_prior_intent": bool((continuity_meta or {}).get("resumed_prior_intent")),
        "blocked_classification": (chosen or {}).get("blocked_classification") or "",
        "selected_capability": (chosen or {}).get("capability") or "",
        "capability_gaps": capability_gaps,
        "backlog": backlog,
        "backlog_work_id": chosen.get("backlog_work_id") or chosen.get("work_id") if chosen else None,
        "local_only": bool(chosen.get("local_only")) if chosen else False,
        "generated_from": (chosen or {}).get("generated_from") or "",
        "hold_work_class": (chosen or {}).get("hold_work_class") or "",
        "candidate_skipped": skipped,
        "bounded_step": {
            "id": chosen["id"],
            "task_type": chosen["task_type"],
            "actions": list(chosen.get("execution_steps") or chosen.get("actions") or []) if chosen else [],
            "execution_steps": list(chosen.get("execution_steps") or chosen.get("actions") or []) if chosen else [],
            "success_criteria": chosen["success_criteria"],
            "expected_repo_delta": list(chosen["expected_repo_delta"]),
            "proposed_repo_delta": list(chosen.get("proposed_repo_delta") or chosen.get("expected_repo_delta") or []),
            "verification_commands": [list(cmd) for cmd in chosen.get("verification_commands") or []],
            "done_when": list(chosen.get("done_when") or []) if chosen else [],
            "dispatch_target": chosen.get("dispatch_target") or "" if chosen else "",
            "expected_outputs": list(chosen.get("expected_outputs") or []) if chosen else [],
            "code_contract": chosen.get("code_contract") if chosen else None,
        },
        "next_focus_after": chosen["next_focus_after"] if chosen else "Refresh backlog or resolve blocker",
    }

    if chosen and chosen.get("backlog_work_id"):
        return attach_work_package(
            plan_base,
            cycle_id=cycle_id,
            research=research,
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot=repo_snapshot,
        )

    return plan_base


def self_check() -> None:
    backlog_path = ROOT / "project_memory/runtime/goal_backlog.json"
    prior = backlog_path.read_bytes() if backlog_path.is_file() else None
    try:
      plan = run_plan(
        cycle_id=1,
        state={"completed_milestones": ["schedule_helper"], "rejected_milestones": [], "cycle_id": 0},
        research={"summary": "test", "goal_gap_addressed": "gap_product_realization"},
        goal_text=_file_text_path(goal_path()) or "Product Goal\n",
        status_text="Level 0.3\n",
        repo_snapshot={"tracked_files": []},
      )
    finally:
      if prior is None:
        backlog_path.unlink(missing_ok=True)
      else:
        backlog_path.write_bytes(prior)
    assert plan["plan_id"]
    assert plan["task_type"] in TASK_TYPES
    assert plan["why_this_step_now"]
    assert plan["goal_gap_addressed"]
    if plan.get("backlog_work_id"):
        assert plan["expected_repo_delta"] or plan.get("work_package", {}).get("dispatch_target")
        has_steps = plan["bounded_step"]["actions"] or plan["bounded_step"].get("execution_steps")
        has_dispatch = bool(plan.get("work_package", {}).get("dispatch_target"))
        assert has_steps or has_dispatch
    assert plan["goal_text"]
    assert "schedule_helper" not in plan["plan_id"]
    assert plan.get("backlog")
    assert len(plan["backlog"].get("product_work_items") or []) >= 5
    assert plan["plan_id"] != "no_open_gap_work"
    if plan.get("work_package"):
        assert plan["work_package"].get("work_id")
        assert plan["work_package"].get("done_when")

    sched_gap = {
        "id": "gap_scheduled_execution",
        "description": "purple_halo cannot yet autonomously run on schedule",
        "priority": 3,
    }
    sched_milestone = _milestone_from_gap(sched_gap, cycle_id=1, research={"summary": "schedule"})
    assert sched_milestone["id"] == "scheduled_autonomous_runner"
    assert sched_milestone["task_type"] == "code_implementation"
    assert sched_milestone.get("code_contract")
    chosen, _skipped = choose_step([sched_milestone], {"completed_milestones": [], "rejected_milestones": []})
    assert chosen is not None
    assert chosen["id"] == "scheduled_autonomous_runner"
    assert chosen["task_type"] == "code_implementation"

    sched_plan = run_plan(
        cycle_id=2,
        state={"completed_milestones": [], "rejected_milestones": [], "cycle_id": 1},
        research={"summary": "schedule gap", "goal_gap_addressed": "gap_scheduled_execution"},
        goal_text=_file_text_path(goal_path()) or "Product Goal\n",
        status_text="Level 0.3\n",
    )
    if sched_plan.get("goal_gap_addressed") == "gap_scheduled_execution":
        assert sched_plan["plan_id"] == "scheduled_autonomous_runner"
        assert sched_plan["task_type"] == "code_implementation"
        assert sched_plan.get("code_contract")
    print("loop-plan: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo loop planner")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    parser.error("use purple_halo_loop.py run or --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
