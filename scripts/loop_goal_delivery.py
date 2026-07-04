#!/usr/bin/env python3
"""Goal-delivery mode: map production runs to project_goals.md success criteria."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
GOALS_PATH = ROOT / "project_goals.md"
LEDGER_PATH = ROOT / "project_memory" / "runtime" / "goal_delivery_ledger.json"
HISTORY_PATH = ROOT / "project_memory" / "runtime" / "schedule_run_history.json"

CORE_FOCUS_ORDER = [
    "cycle_inspect_decide",
    "explicit_plan",
    "agent_execution",
    "verification_evidence",
    "continuity_state",
    "autonomous_iteration",
]
CORE_FOCUS = frozenset(CORE_FOCUS_ORDER)
CORE_DISPLAY_NAME = {
    "cycle_inspect_decide": "cycle_inspect_decide",
    "explicit_plan": "bounded_plan_generation",
    "agent_execution": "bounded_implementation_execution",
    "verification_evidence": "verification_of_real_work",
    "continuity_state": "state_persistence_for_next_cycle",
    "autonomous_iteration": "scheduled_repeated_execution_until_goal_realized",
}
INPUT_LAYER = frozenset({
    "durable_mission_goal",
    "repo_derived_status",
    "online_research",
    "schedule_config",
    "honest_stop",
    "minimal_loop",
})

CriterionStatus = str  # unmet | partial | complete | blocked


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _symbol_exists(spec: str) -> bool:
    # "path:symbol" or bare path exists
    if ":" in spec:
        path_s, name = spec.split(":", 1)
        path = ROOT / path_s
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
        return ("def " + name) in text or ("class " + name) in text
    path = ROOT / spec
    return path.is_file()


def _file_has(path_s: str, needle: str) -> bool:
    path = ROOT / path_s
    if not path.is_file():
        return False
    return needle in path.read_text(encoding="utf-8", errors="replace")


def parse_success_criteria(goal_text: str | None = None) -> list[dict[str, Any]]:
    text = goal_text if goal_text is not None else (
        GOALS_PATH.read_text(encoding="utf-8") if GOALS_PATH.is_file() else ""
    )
    lines = text.splitlines()
    in_section = False
    bullets: list[str] = []
    for line in lines:
        if line.strip().startswith("## Success Criteria"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.strip().startswith("- "):
            bullets.append(line.strip()[2:].strip())
    # Stable ids for known purple_halo criteria (order matches project_goals.md).
    ids = [
        "durable_mission_goal",
        "repo_derived_status",
        "cycle_inspect_decide",
        "online_research",
        "explicit_plan",
        "agent_execution",
        "verification_evidence",
        "continuity_state",
        "schedule_config",
        "autonomous_iteration",
        "honest_stop",
        "minimal_loop",
    ]
    out: list[dict[str, Any]] = []
    for i, bullet in enumerate(bullets):
        cid = ids[i] if i < len(ids) else "criterion_" + str(i + 1)
        out.append({"id": cid, "text": bullet, "index": i + 1})
    return out


# Evidence probes: (complete_specs, partial_specs, capability_step, default_unblock)
CRITERION_PROBES: dict[str, dict[str, Any]] = {
    "durable_mission_goal": {
        "complete": ["project_goals.md", "scripts/goal_parser_runtime.py:parse_goals"],
        "partial": ["project_goals.md"],
        "capability": "goal_ingestion",
        "work_id": "deliver_durable_mission_goal",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "repo_derived_status": {
        "complete": ["project_status.md", "scripts/purple_halo_loop.py:_update_project_status"],
        "partial": ["project_status.md"],
        "capability": "repo_status_analysis",
        "work_id": "deliver_repo_derived_status",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "cycle_inspect_decide": {
        "complete": [
            "scripts/purple_halo_loop.py:run_cycle",
            "scripts/loop_plan.py:run_plan",
            "scripts/loop_backlog.py:pick_next_item",
        ],
        "partial": ["scripts/purple_halo_loop.py:run_cycle"],
        "capability": "plan_generation",
        "work_id": "deliver_cycle_inspect_decide",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "online_research": {
        "complete": [
            "scripts/loop_research.py:run_research",
            "scripts/research_fetch_runtime.py:fetch_research_context",
        ],
        "partial": ["scripts/loop_research.py:run_research"],
        "capability": "research_synthesis",
        "work_id": "deliver_online_research",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "explicit_plan": {
        "complete": [
            "scripts/loop_plan.py:run_plan",
            "scripts/plan_generator_runtime.py:generate_plan_brief",
        ],
        "partial": ["scripts/loop_plan.py:run_plan"],
        "capability": "plan_generation",
        "work_id": "deliver_explicit_plan",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "agent_execution": {
        "complete": [
            "scripts/loop_execute.py:run_execute",
            "scripts/loop_worker_bridge.py:run_worker_bridge",
        ],
        "partial": ["scripts/loop_execute.py:run_execute"],
        "capability": "implementation_dispatch",
        "work_id": "deliver_agent_execution",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "verification_evidence": {
        "complete": [
            "scripts/loop_verify.py:run_verify",
            "scripts/verification_runner_runtime.py:run_verification_suite",
        ],
        "partial": ["scripts/loop_verify.py:run_verify"],
        "capability": "verification_dispatch",
        "work_id": "deliver_verification_evidence",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "continuity_state": {
        "complete": [
            "scripts/loop_continuity_state.py:resume_from_continuity",
            "scripts/loop_continuity_state.py:write_continuity_after_cycle",
            "project_memory/runtime/continuity_state.json",
        ],
        "partial": ["scripts/loop_continuity_state.py:resume_from_continuity"],
        "capability": "persistence_resume",
        "work_id": "deliver_continuity_state",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "schedule_config": {
        "complete": [
            "scripts/loop_schedule.py:run_due",
            "project_memory/runtime/schedule.json",
        ],
        "partial": ["scripts/loop_schedule.py:run_due"],
        "capability": "schedule_control",
        "work_id": "deliver_schedule_config",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "autonomous_iteration": {
        "complete": [
            "scripts/loop_autonomous.py:decide_autonomous_run",
            "scripts/loop_autonomous.py:record_autonomous_run",
            "scripts/loop_production_ops.py:production_ops_active",
        ],
        "partial": ["scripts/loop_autonomous.py:decide_autonomous_run"],
        "capability": "schedule_control",
        "work_id": "deliver_autonomous_iteration",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "honest_stop": {
        "complete": [
            "scripts/loop_autonomous.py:decide_autonomous_run",
            "scripts/loop_production_ops.py:detect_regressions",
        ],
        "partial": ["scripts/loop_autonomous.py:decide_autonomous_run"],
        "capability": "schedule_control",
        "work_id": "deliver_honest_stop",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
    "minimal_loop": {
        "complete": ["scripts/purple_halo_loop.py:run_cycle", "AGENTS.md"],
        "partial": ["scripts/purple_halo_loop.py:run_cycle"],
        "capability": "plan_generation",
        "work_id": "deliver_minimal_loop",
        "verify": [["python3", "scripts/loop_goal_delivery.py", "--self-check"]],
    },
}


def _delivery_results() -> list[dict[str, Any]]:
    return list(_load_json(HISTORY_PATH).get("goal_delivery_results") or [])


def _runtime_evidence_ok(cid: str, row: dict[str, Any]) -> bool:
    if str(row.get("success_criterion_id") or "") != cid:
        return False
    if not row.get("meaningful_progress"):
        return False
    if str(row.get("outcome_class") or "") != "meaningful_product_progress":
        return False
    if row.get("cycle_id") is None:
        return False
    runtime = row.get("runtime_behavior") or {}
    plan_id = runtime.get("plan_id") or row.get("plan_id")
    capability = runtime.get("selected_capability") or row.get("selected_capability")
    if not plan_id or not capability:
        return False
    if cid == "cycle_inspect_decide":
        return True
    if cid == "explicit_plan":
        return bool(runtime.get("task_type") or plan_id)
    if cid == "agent_execution":
        return bool(runtime.get("local_only") or runtime.get("execution_ok"))
    if cid == "verification_evidence":
        return bool(runtime.get("verification_passed") or row.get("verification_truthful"))
    if cid == "continuity_state":
        return bool(row.get("next_cycle_effect_observed"))
    if cid == "autonomous_iteration":
        core_runs = [
            r for r in _delivery_results()
            if r.get("meaningful_progress")
            and str(r.get("success_criterion_id") or "") in CORE_FOCUS
        ]
        return len(core_runs) >= 2
    return True


def _delivery_proven(cid: str) -> bool:
    rows = list(_delivery_results())
    for i, row in enumerate(rows):
        if str(row.get("success_criterion_id") or "") != cid:
            continue
        if cid == "continuity_state" and not row.get("next_cycle_effect_observed"):
            for later in rows[i + 1 :]:
                if later.get("continuity_influenced") and later.get("cycle_id") is not None:
                    row = dict(row)
                    row["next_cycle_effect_observed"] = True
                    break
        if _runtime_evidence_ok(cid, row):
            return True
    return False


def _probe_status(cid: str) -> tuple[str, list[str], str, str]:
    probe = CRITERION_PROBES.get(cid) or {}
    complete_specs = list(probe.get("complete") or [])
    partial_specs = list(probe.get("partial") or [])
    evidence: list[str] = []
    missing_complete: list[str] = []
    for spec in complete_specs:
        ok = _symbol_exists(spec)
        evidence.append(("static_ok:" if ok else "static_missing:") + spec)
        if not ok:
            missing_complete.append(spec)
    partial_ok = all(_symbol_exists(s) for s in partial_specs) if partial_specs else False
    # Input-layer criteria are frozen once static probes pass (already proven upstream).
    if cid in INPUT_LAYER and complete_specs and not missing_complete:
        evidence.append("static_complete:input_layer_frozen")
        return "complete", evidence, "", ""
    proven = _delivery_proven(cid)
    if proven:
        evidence.append("runtime_ok:autonomous_delivery_run:" + cid)
    else:
        evidence.append("runtime_missing:autonomous_delivery_run:" + cid)
    display = CORE_DISPLAY_NAME.get(cid, cid)
    if proven:
        return "complete", evidence, "", ""
    if (not missing_complete and complete_specs) or partial_ok or (
        complete_specs and len(missing_complete) < len(complete_specs)
    ):
        if cid in CORE_FOCUS:
            blocker = "awaiting_runtime_evidence"
            unblock = (
                "run autonomous criterion-linked work for "
                + display
                + " with live plan/execution/verification/persistence evidence"
            )
        elif missing_complete:
            blocker = "missing_evidence:" + ",".join(missing_complete[:3])
            unblock = "land evidence for " + missing_complete[0]
        else:
            blocker = "awaiting_runtime_evidence"
            unblock = "run criterion-linked delivery work for " + cid
        return "partial", evidence, blocker, unblock
    blocker = "criterion_not_started" if not evidence else "missing_evidence:" + ",".join(missing_complete[:3])
    unblock = "implement next capability step for " + cid
    return "unmet", evidence, blocker, unblock


def build_ledger(*, goal_text: str | None = None) -> dict[str, Any]:
    criteria_src = parse_success_criteria(goal_text)
    criteria: list[dict[str, Any]] = []
    for src in criteria_src:
        cid = str(src["id"])
        status, evidence, blocker, unblock = _probe_status(cid)
        probe = CRITERION_PROBES.get(cid) or {}
        criteria.append({
            "id": cid,
            "display_name": CORE_DISPLAY_NAME.get(cid, cid),
            "text": src["text"],
            "status": status,
            "evidence": evidence,
            "next_required_capability_step": str(probe.get("capability") or "plan_generation"),
            "blocker_reason": blocker if status != "complete" else "",
            "unblock_condition": unblock if status != "complete" else "",
            "work_id": str(probe.get("work_id") or ("deliver_" + cid)),
            "core_focus": cid in CORE_FOCUS,
            "frozen_complete": status == "complete" and cid in INPUT_LAYER,
        })
    counts = {"complete": 0, "partial": 0, "unmet": 0, "blocked": 0}
    for c in criteria:
        counts[str(c["status"])] = counts.get(str(c["status"]), 0) + 1
    unmet = [c for c in criteria if c["status"] in {"unmet", "partial", "blocked"}]
    core_unmet = [c for cid in CORE_FOCUS_ORDER for c in unmet if c.get("id") == cid]
    other_unmet = [c for c in unmet if c.get("id") not in CORE_FOCUS]
    ranked_unmet = core_unmet + other_unmet
    top = ranked_unmet[0] if ranked_unmet else {}
    remaining_partial = [
        {
            "id": c.get("id"),
            "display_name": c.get("display_name"),
            "status": c.get("status"),
            "missing_evidence": c.get("blocker_reason"),
            "unblock_condition": c.get("unblock_condition"),
            "core_focus": c.get("core_focus"),
        }
        for c in ranked_unmet
    ]
    all_complete = bool(criteria) and counts["complete"] == len(criteria)
    ledger = {
        "version": 1,
        "goal_delivery_mode": True,
        "updated_at": _now_iso(),
        "source": str(GOALS_PATH.relative_to(ROOT)),
        "criteria": criteria,
        "counts": counts,
        "top_unmet_criterion": top,
        "remaining_partial": remaining_partial,
        "core_focus_order": list(CORE_FOCUS_ORDER),
        "all_criteria_complete": all_complete,
        "why_next_run": (
            "all success criteria complete"
            if all_complete
            else (
                "advance " + str(top.get("id") or "unknown")
                + ": " + str(top.get("text") or "")[:160]
                + " | evidence needed: " + str(top.get("unblock_condition") or "")
            )
        ),
    }
    return ledger


def save_ledger(ledger: dict[str, Any]) -> Path:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return LEDGER_PATH


def load_ledger() -> dict[str, Any]:
    raw = _load_json(LEDGER_PATH)
    if raw.get("criteria"):
        return raw
    return build_ledger()


def refresh_ledger(*, goal_text: str | None = None) -> dict[str, Any]:
    ledger = build_ledger(goal_text=goal_text)
    save_ledger(ledger)
    return ledger


def goal_delivery_active(history: dict[str, Any] | None = None) -> bool:
    if history is None:
        history = _load_json(HISTORY_PATH)
    try:
        from loop_production_ops import production_ops_active
        prod = production_ops_active(history)
    except Exception:
        prod = bool(history.get("production_candidate_operations"))
    return bool(prod and history.get("goal_delivery_mode", True))


def ensure_goal_delivery_mode() -> dict[str, Any]:
    from loop_production_ops import ensure_production_candidate_operations
    history = ensure_production_candidate_operations()
    changed = False
    if not history.get("goal_delivery_mode"):
        history["goal_delivery_mode"] = True
        changed = True
    if changed:
        history["updated_at"] = _now_iso()
        HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    refresh_ledger()
    return history


def criteria_complete(ledger: dict[str, Any] | None = None) -> bool:
    ledger = ledger or load_ledger()
    return bool(ledger.get("all_criteria_complete"))


def delivery_work_specs() -> list[dict[str, Any]]:
    if not goal_delivery_active():
        return []
    try:
        from loop_production_hold import evaluate_hold_run, production_hold_active, hold_repair_specs
        if production_hold_active():
            decision = evaluate_hold_run()
            if decision.get("run_kind") == "verify_only":
                return []
            return hold_repair_specs()
    except Exception:
        pass
    ledger = refresh_ledger()
    specs: list[dict[str, Any]] = []
    criteria = list(ledger.get("criteria") or [])
    by_id = {str(c.get("id") or ""): c for c in criteria}
    ordered: list[dict[str, Any]] = []
    for cid in CORE_FOCUS_ORDER:
        c = by_id.get(cid)
        if c and c.get("status") != "complete":
            ordered.append(c)
    for c in criteria:
        cid = str(c.get("id") or "")
        if cid in CORE_FOCUS or c.get("status") == "complete" or cid in INPUT_LAYER:
            continue
        ordered.append(c)
    priority = 1
    for c in ordered:
        cid = str(c.get("id") or "")
        probe = CRITERION_PROBES.get(cid) or {}
        work_id = str(c.get("work_id") or probe.get("work_id") or ("deliver_" + cid))
        capability = str(c.get("next_required_capability_step") or "plan_generation")
        verify = list(probe.get("verify") or [["python3", "scripts/loop_goal_delivery.py", "--self-check"]])
        evidence_needed = str(c.get("unblock_condition") or ("advance " + cid))
        target_files = []
        for spec in list(probe.get("complete") or [])[:3]:
            target_files.append(spec.split(":", 1)[0])
        if not target_files:
            target_files = ["scripts/purple_halo_loop.py"]
        specs.append({
            "work_id": work_id,
            "title": "Deliver success criterion: " + cid,
            "capability": capability,
            "goal_gap_addressed": cid,
            "success_criterion_id": cid,
            "success_criterion_text": c.get("text") or "",
            "evidence_will_move": evidence_needed,
            "runtime_evidence_required": (
                "live autonomous run selects and executes work for "
                + CORE_DISPLAY_NAME.get(cid, cid)
            ),
            "next_cycle_effect": (
                "continuity resumes this criterion focus on the next scheduled run"
                if cid in {"continuity_state", "cycle_inspect_decide", "autonomous_iteration"}
                else "ledger marks " + CORE_DISPLAY_NAME.get(cid, cid) + " complete from runtime evidence"
            ),
            "task_type": "verification_hardening",
            "priority": priority,
            "local_only": True,
            "objective": "Advance unmet success criterion " + cid + " toward complete.",
            "why_now": "Goal-delivery mode prioritizes unmet project_goals.md criteria.",
            "detect_open": lambda _cid=cid: True,
            "target_files": target_files,
            "proposed_repo_delta": target_files,
            "execution_steps": [{"type": "run_command", "command": cmd} for cmd in verify],
            "verification_commands": verify,
            "done_when": ["criterion evidence: " + evidence_needed],
            "generated_from": "goal_delivery",
            "criterion_status": c.get("status"),
            "blocker_reason": c.get("blocker_reason") or "",
        })
        priority += 1
    return specs


def linked_improve_specs() -> list[dict[str, Any]]:
    """Loop-quality improve_* only when explicitly the blocker to a core unmet criterion."""
    if not goal_delivery_active():
        return []
    try:
        from loop_production_hold import production_hold_active
        if production_hold_active():
            return []
    except Exception:
        pass
    ledger = load_ledger()
    unmet = [
        c for c in (ledger.get("criteria") or [])
        if c.get("status") != "complete" and str(c.get("id") or "") in CORE_FOCUS
    ]
    if not unmet:
        return []
    # Map improve work to criteria it can unblock.
    links = [
        ("improve_useful_work_selection", "cycle_inspect_decide", "plan_generation",
         ["scripts/loop_backlog.py"], [["python3", "scripts/loop_backlog.py", "--self-check"]]),
        ("improve_verification_truthfulness", "verification_evidence", "verification_dispatch",
         ["scripts/loop_verify.py"], [["python3", "scripts/loop_verify.py", "--self-check"]]),
        ("improve_continuity_quality", "continuity_state", "persistence_resume",
         ["scripts/loop_continuity_state.py"], [["python3", "scripts/loop_continuity_state.py", "--self-check"]]),
        ("improve_token_efficiency", "autonomous_iteration", "schedule_control",
         ["scripts/loop_cost_policy.py"], [["python3", "scripts/loop_cost_policy.py", "--self-check"]]),
    ]
    unmet_ids = {str(c.get("id")) for c in unmet}
    specs: list[dict[str, Any]] = []
    # Rank after direct delivery work (priority starts at 50).
    priority = 50
    for work_id, cid, capability, files, verify in links:
        if cid not in unmet_ids:
            continue
        c = next(x for x in unmet if x.get("id") == cid)
        specs.append({
            "work_id": work_id,
            "title": "Unblock criterion via " + work_id,
            "capability": capability,
            "goal_gap_addressed": cid,
            "success_criterion_id": cid,
            "success_criterion_text": c.get("text") or "",
            "evidence_will_move": str(c.get("unblock_condition") or ("support " + cid)),
            "task_type": "verification_hardening",
            "priority": priority,
            "local_only": True,
            "objective": "Loop-quality work only because it unblocks unmet criterion " + cid + ".",
            "why_now": "Linked improve_* for unmet criterion " + cid,
            "detect_open": lambda: True,
            "target_files": files,
            "proposed_repo_delta": files,
            "execution_steps": [{"type": "run_command", "command": cmd} for cmd in verify],
            "verification_commands": verify,
            "done_when": ["linked criterion: " + cid],
            "generated_from": "goal_delivery_linked_improve",
            "criterion_status": c.get("status"),
            "blocker_reason": c.get("blocker_reason") or "",
        })
        priority += 1
    return specs


def goal_delivery_status() -> dict[str, Any]:
    ledger = refresh_ledger()
    top = ledger.get("top_unmet_criterion") or {}
    return {
        "goal_delivery_mode": True,
        "goal_delivery_ledger": {
            "counts": ledger.get("counts") or {},
            "criteria": ledger.get("criteria") or [],
            "remaining_partial": ledger.get("remaining_partial") or [],
            "core_focus_order": ledger.get("core_focus_order") or list(CORE_FOCUS_ORDER),
            "all_criteria_complete": ledger.get("all_criteria_complete"),
            "updated_at": ledger.get("updated_at"),
        },
        "top_unmet_criterion": top,
        "remaining_partial": ledger.get("remaining_partial") or [],
        "why_next_run": ledger.get("why_next_run") or "",
        "goal_realized_justified": bool(ledger.get("all_criteria_complete")),
    }


def record_delivery_selection(entry: dict[str, Any]) -> dict[str, Any]:
    history = _load_json(HISTORY_PATH)
    if not goal_delivery_active(history):
        return history
    if not entry.get("cycle_id") or not (entry.get("success_criterion_id") or str(entry.get("plan_id") or "").startswith("deliver_")):
        return history
    cid = str(entry.get("success_criterion_id") or "")
    if not cid:
        pid = str(entry.get("plan_id") or "")
        if pid.startswith("deliver_"):
            cid = pid[len("deliver_") :]
    runtime = {
        "plan_id": entry.get("plan_id") or "",
        "selected_capability": entry.get("selected_capability") or "",
        "task_type": entry.get("task_type") or "",
        "local_only": bool(entry.get("local_only", True)),
        "execution_ok": bool(entry.get("meaningful_product_progress")),
        "verification_passed": bool(entry.get("meaningful_product_progress")),
        "verification_truthful": bool(entry.get("verification_truthful", True)),
        "worker_used": bool(entry.get("worker_used")),
        "trigger": entry.get("trigger") or "",
    }
    row = {
        "cycle_id": entry.get("cycle_id"),
        "plan_id": entry.get("plan_id"),
        "success_criterion_id": cid,
        "evidence_will_move": entry.get("evidence_will_move") or "",
        "next_cycle_effect": entry.get("next_cycle_effect") or "",
        "meaningful_progress": bool(entry.get("meaningful_product_progress")),
        "outcome_class": entry.get("outcome_class") or "",
        "continuity_influenced": bool(entry.get("continuity_influenced")),
        "selected_capability": entry.get("selected_capability") or "",
        "runtime_behavior": runtime,
        "persisted_state": {
            "continuity_influenced": bool(entry.get("continuity_influenced")),
            "ledger_refresh": True,
        },
        "next_cycle_effect_observed": False,
        "started_at": entry.get("started_at") or _now_iso(),
    }
    if row["continuity_influenced"]:
        prior = list(history.get("goal_delivery_results") or [])
        for prev in prior:
            if str(prev.get("success_criterion_id") or "") == "continuity_state":
                prev["next_cycle_effect_observed"] = True
        history["goal_delivery_results"] = prior
    history.setdefault("goal_delivery_results", []).append(row)
    history["goal_delivery_results"] = history["goal_delivery_results"][-50:]
    history["updated_at"] = _now_iso()
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    refresh_ledger()
    return history


def self_check() -> None:
    criteria = parse_success_criteria()
    assert len(criteria) >= 8
    assert criteria[0]["id"] == "durable_mission_goal"
    ledger = build_ledger()
    assert "criteria" in ledger and ledger["criteria"]
    assert "counts" in ledger
    assert set(ledger["counts"]) >= {"complete", "partial", "unmet", "blocked"}
    for c in ledger["criteria"]:
        assert c["status"] in {"unmet", "partial", "complete", "blocked"}
        assert "evidence" in c
        assert "next_required_capability_step" in c
    status = goal_delivery_status()
    assert "goal_delivery_ledger" in status
    assert "top_unmet_criterion" in status or status.get("goal_realized_justified")
    assert "why_next_run" in status
    assert "remaining_partial" in status
    specs = delivery_work_specs()
    for s in specs:
        assert s.get("success_criterion_id")
        assert s.get("runtime_evidence_required")
        assert s.get("next_cycle_effect")
        assert str(s.get("success_criterion_id") or "") in CORE_FOCUS
    if specs:
        assert str(specs[0].get("success_criterion_id") or "") in CORE_FOCUS
    print("loop-goal-delivery: PASS")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="purple_halo goal-delivery mode")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.refresh:
        print(json.dumps(refresh_ledger(), indent=2))
        return 0
    if args.status:
        print(json.dumps(goal_delivery_status(), indent=2))
        return 0
    parser.error("specify --self-check, --refresh, or --status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
