#!/usr/bin/env python3
"""Repeated autonomous self-mode operation for purple_halo. Stdlib only."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "project_memory" / "runtime" / "schedule_run_history.json"
SCHEDULE_PATH = ROOT / "project_memory" / "runtime" / "schedule.json"
SCHEDULE_DEFAULT = ROOT / "project_memory" / "runtime" / "schedule.default.json"
SEQUENCE_LIMIT = 20
ANTI_SPIN_STREAK = 2

STOP_CLASSIFICATIONS = frozenset(
    {
        "goal_realized",
        "product_complete",
        "budget_blocked",
        "verification_blocked",
        "no_meaningful_product_step",
        "anti_spin_halt",
        "sequence_complete_for_review",
        "externally_blocked",
        "operator_paused",
        "max_runs_per_day",
        "no_due_slot",
        "target_mode_active",
    }
)

GOAL_CAPABILITY_EVIDENCE: dict[str, tuple[str, ...]] = {
    "goal_ingestion": (
        "scripts/loop_artifact_inputs.py:build_structured_goal_model",
        "project_memory/runtime/goal_model.json",
    ),
    "repo_status_analysis": (
        "scripts/purple_halo_loop.py:repo_snapshot",
        "scripts/purple_halo_loop.py:_update_project_status",
    ),
    "research_synthesis": (
        "scripts/loop_research.py:run_research",
        "scripts/loop_research.py:should_fetch_fresh_research",
    ),
    "plan_generation": (
        "scripts/loop_plan.py:run_plan",
        "scripts/loop_work_package.py:build_work_package",
    ),
    "implementation_dispatch": (
        "scripts/loop_execute.py:run_execute",
    ),
    "verification_dispatch": (
        "scripts/loop_verify.py:run_verify",
        "scripts/purple_halo_loop.py:evaluate_cycle_outcome",
    ),
    "persistence_resume": (
        "scripts/loop_continuity_state.py:resume_from_continuity",
        "project_memory/runtime/continuity_state.json",
    ),
    "schedule_control": (
        "scripts/loop_schedule.py:run_due",
        "scripts/loop_autonomous.py:decide_autonomous_run",
    ),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _symbol_exists(spec: str) -> bool:
    if ":" in spec and not spec.startswith("project_memory/"):
        rel, symbol = spec.split(":", 1)
        path = ROOT / rel
        if not path.is_file():
            return False
        return f"def {symbol}" in path.read_text(encoding="utf-8")
    return (ROOT / spec).is_file()


def load_autonomous_history() -> dict[str, Any]:
    raw = _load_json(HISTORY_PATH)
    if not raw:
        return {
            "attempts": [],
            "sequence": [],
            "autonomous_allowed": True,
            "stop_classification": "",
            "stop_reason": "",
            "retry_count": 0,
            "last_failure": None,
        }
    raw.setdefault("attempts", [])
    raw.setdefault("sequence", [])
    if "autonomous_allowed" not in raw:
        raw["autonomous_allowed"] = True
    raw.setdefault("stop_classification", "")
    raw.setdefault("stop_reason", "")
    return raw


def save_autonomous_history(history: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


def load_schedule_config() -> dict[str, Any]:
    path = SCHEDULE_PATH if SCHEDULE_PATH.is_file() else SCHEDULE_DEFAULT
    cfg = _load_json(path) if path.is_file() else {}
    if not cfg:
        cfg = {"enabled": False, "timezone": "UTC", "runs": [], "max_runs_per_day": 4}
    cfg.setdefault("max_runs_per_day", 4)
    cfg.setdefault("mode", "self_product_mode")
    return cfg


def runs_today(history: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    history = history or load_autonomous_history()
    today = _today()
    skip = {
        "budget_skip",
        "no_work_skip",
        "product_complete_stop",
        "anti_spin_halt",
        "no_due_slot",
        "target_mode_active",
        "max_runs_per_day",
        "monthly_token_ceiling",
        "repeated_regression",
    }
    started = str(history.get("production_started_at") or "")
    prod = bool(
        history.get("production_candidate_operations")
        and history.get("production_candidate")
        and history.get("live_soak_passed")
        and not (history.get("live_soak_mode") and not history.get("live_soak_passed"))
    )
    out = []
    for r in history.get("sequence") or []:
        if not str(r.get("started_at") or "").startswith(today):
            continue
        if not r.get("ran"):
            continue
        if str(r.get("outcome_class") or "") in skip:
            continue
        # Production daily budget counts only runs after production activation.
        if prod and started and str(r.get("started_at") or "") < started:
            continue
        if prod and str(r.get("plan_id") or "").startswith("operational_"):
            continue
        # Goal-delivery daily budget counts criterion-linked work only.
        if prod and history.get("goal_delivery_mode"):
            pid = str(r.get("plan_id") or "")
            if not (
                pid.startswith("deliver_")
                or pid.startswith("improve_")
                or r.get("success_criterion_id")
            ):
                continue
        out.append(r)
    return out


PROOF_REVALIDATION_WORK_IDS = frozenset({"product_cycle_closure"})
END_GOAL_CAPABILITIES = (
    "goal_ingestion",
    "repo_status_analysis",
    "research_synthesis",
    "plan_generation",
    "implementation_dispatch",
    "verification_dispatch",
    "persistence_resume",
    "schedule_control",
)
OPERATIONAL_MIN_CONSECUTIVE_RUNS = 5
OPERATIONAL_MIN_PROGRESS_RUNS = 3
OPERATIONAL_MIN_CONTINUITY_RATIO = 0.5
HONEST_BLOCKER_OUTCOMES = frozenset({
    "budget_blocked",
    "verification_blocked",
    "externally_blocked",
    "no_meaningful_product_step",
    "anti_spin_halt",
    "budget_skip",
    "no_work_skip",
})


def ensure_long_run_mode() -> dict[str, Any]:
    history = load_autonomous_history()
    changed = False
    stop = str(history.get("stop_classification") or "")
    if stop in {"sequence_complete_for_review", "goal_realized", "product_complete"}:
        if not history.get("operationally_realized"):
            history["autonomous_allowed"] = True
            history["stop_classification"] = ""
            history["stop_reason"] = ""
            history["proof_complete"] = False
            changed = True
    if not history.get("long_run_mode"):
        history["long_run_mode"] = True
        changed = True
    if changed:
        history["updated_at"] = _now_iso()
        save_autonomous_history(history)
    return history

SOAK_MIN_RUNS = 5
SOAK_MAX_RUNS = 7
SOAK_TARGET_DAYS = 3
SOAK_FAILURE_REPEAT = 2
SOAK_ALLOWED_OUTCOMES = frozenset({
    "meaningful_product_progress",
    "budget_blocked",
    "budget_skip",
    "verification_blocked",
    "no_meaningful_product_step",
    "no_work_skip",
    "operator_pause",
    "anti_spin_halt",
    "goal_realized",
})


def start_live_soak(*, target_days: int = SOAK_TARGET_DAYS, min_runs: int = SOAK_MIN_RUNS) -> dict[str, Any]:
    history = ensure_long_run_mode()
    history["live_soak_mode"] = True
    history["live_soak_passed"] = False
    history["production_candidate"] = False
    history["soak_start_date"] = _today()
    history["soak_target_days"] = int(target_days)
    history["soak_min_runs"] = int(min_runs)
    history["soak_results"] = []
    history["soak_failures"] = []
    history["soak_blockers"] = []
    history["feature_freeze"] = True
    # Do not inherit a prior goal_realized pause into soak.
    if history.get("stop_classification") in {"goal_realized", "product_complete", "sequence_complete_for_review"}:
        history["autonomous_allowed"] = True
        history["stop_classification"] = ""
        history["stop_reason"] = ""
    history["updated_at"] = _now_iso()
    save_autonomous_history(history)
    try:
        from loop_backlog import refresh_backlog
        refresh_backlog()
    except Exception:
        pass
    return history


def live_soak_active(history: dict[str, Any] | None = None) -> bool:
    history = history if history is not None else load_autonomous_history()
    return bool(history.get("live_soak_mode") and not history.get("live_soak_passed"))


def _soak_entry_from_sequence(entry: dict[str, Any]) -> dict[str, Any]:
    plan_id = str(entry.get("plan_id") or "")
    progress = bool(entry.get("meaningful_product_progress"))
    blocked = str(entry.get("blocked_classification") or "")
    outcome = str(entry.get("outcome_class") or "")
    honest = progress or blocked in HONEST_BLOCKER_OUTCOMES or outcome in SOAK_ALLOWED_OUTCOMES
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


def detect_soak_failures(soak_results: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    if not soak_results:
        return failures
    # Continuity-driven repeats of useful work are healthy; churn is spin without progress.
    plan_counts: dict[str, int] = {}
    cap_counts: dict[str, int] = {}
    for r in soak_results:
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
        if pid.startswith("product_gap_") or pid == "product_cycle_closure":
            failures.append("proof_or_gap_work_in_soak")
        if pid.startswith("target_"):
            failures.append("target_mode_work_in_soak")
    if plan_counts:
        top_plan, top_count = max(plan_counts.items(), key=lambda kv: kv[1])
        if top_count >= 3:
            failures.append("repeated_low_value_work:" + top_plan)
    if cap_counts:
        top_cap, top_count = max(cap_counts.items(), key=lambda kv: kv[1])
        if top_count >= 3:
            failures.append("repeated_same_capability_churn:" + top_cap)
    progress = [r for r in soak_results if r.get("meaningful_progress")]
    if progress:
        cont = sum(1 for r in progress if r.get("continuity_influenced"))
        if cont / len(progress) < 0.5:
            failures.append("continuity_drift:" + str(cont) + "/" + str(len(progress)))
    # unique preserve order
    out: list[str] = []
    for f in failures:
        if f not in out:
            out.append(f)
    return out

def evaluate_soak(history: dict[str, Any] | None = None) -> dict[str, Any]:
    history = history or load_autonomous_history()
    results = list(history.get("soak_results") or [])
    failures = detect_soak_failures(results)
    start = str(history.get("soak_start_date") or _today())
    try:
        start_day = datetime.fromisoformat(start).date()
    except ValueError:
        start_day = datetime.now(timezone.utc).date()
    days = (datetime.now(timezone.utc).date() - start_day).days + 1
    executed = [r for r in results if r.get("outcome_class") not in {"no_due_slot"}]
    # Count only real soak attempts that ran or honest-skipped
    ran = [r for r in results if r.get("cycle_id") is not None or r.get("meaningful_progress") or r.get("honest_blocker")]
    # Prefer entries recorded from autonomous runs
    ran = [r for r in results if r.get("cycle_id") is not None]
    progress = [r for r in ran if r.get("meaningful_progress")]
    cont = sum(1 for r in progress if r.get("continuity_influenced"))
    worker = sum(1 for r in ran if r.get("worker_used") or not r.get("cheap_default_respected", True))
    min_runs = int(history.get("soak_min_runs") or SOAK_MIN_RUNS)
    target_days = int(history.get("soak_target_days") or SOAK_TARGET_DAYS)
    # Controlled soak may complete by run count when scheduled path is exercised repeatedly.
    enough_runs = len(ran) >= min_runs
    enough_days = days >= target_days
    window_complete = enough_runs  # primary gate for controlled soak
    passed = (
        bool(history.get("live_soak_mode"))
        and window_complete
        and not failures
        and worker == 0
        and len(progress) >= 3
        and (cont / max(1, len(progress))) >= 0.5
    )
    return {
        "live_soak_mode": bool(history.get("live_soak_mode")),
        "live_soak_passed": passed or bool(history.get("live_soak_passed")),
        "production_candidate": passed or bool(history.get("production_candidate")),
        "soak_days": days,
        "soak_target_days": target_days,
        "soak_run_count": len(ran),
        "soak_min_runs": min_runs,
        "qualifying_progress_count": len(progress),
        "continuity_influence_rate": (cont / max(1, len(progress))) if progress else 0.0,
        "worker_used_count": worker,
        "failures": failures,
        "blockers": list(history.get("soak_blockers") or failures),
        "feature_freeze": bool(history.get("feature_freeze")),
        "window_complete": window_complete,
        "results": results[-SOAK_MAX_RUNS:],
    }


def apply_soak_entry(entry: dict[str, Any]) -> dict[str, Any]:
    history = load_autonomous_history()
    if not history.get("live_soak_mode"):
        return history
    soak_entry = _soak_entry_from_sequence(entry)
    history.setdefault("soak_results", []).append(soak_entry)
    history["soak_results"] = history["soak_results"][-50:]
    failures = detect_soak_failures(history.get("soak_results") or [])
    history["soak_failures"] = failures
    # Pause on repeated soak failure classes
    prior = list(history.get("soak_blockers") or [])
    for f in failures:
        cls = f.split(":", 1)[0]
        prior_count = sum(1 for p in prior if str(p).split(":", 1)[0] == cls)
        if prior_count + 1 >= SOAK_FAILURE_REPEAT:
            history["autonomous_allowed"] = False
            history["stop_classification"] = "soak_failure"
            history["stop_reason"] = "soak failure repeated: " + f
            history["soak_blockers"] = failures
            history["live_soak_passed"] = False
            history["production_candidate"] = False
            history["updated_at"] = _now_iso()
            save_autonomous_history(history)
            return history
    history["soak_blockers"] = failures
    soak = evaluate_soak(history)
    if soak.get("live_soak_passed"):
        history["live_soak_passed"] = True
        history["production_candidate"] = True
        history["feature_freeze"] = False
        history["stop_classification"] = "live_soak_passed"
        history["stop_reason"] = "live soak passed; production candidate"
        # Keep autonomous allowed for production candidate scheduled operation
        history["autonomous_allowed"] = True
    history["updated_at"] = _now_iso()
    save_autonomous_history(history)
    return history


def _mechanics_assessment(*, state: dict[str, Any], goal_text: str, status_text: str) -> dict[str, Any]:
    import loop_target_workspace as ltw
    from loop_continuity_state import CONTINUITY_STATE_PATH
    from loop_open_gaps_state import OPEN_GAPS_STATE_PATH
    why_not: list[str] = []
    evidence: dict[str, Any] = {}
    if ltw.is_external_target():
        return {
            "mechanics_complete": False,
            "why_not": ["target_mode_active"],
            "evidence": {},
            "complete_caps": [],
            "partial_caps": ["self_product_mode"],
            "blocked_caps": ["target_mode_active"],
        }
    goal_path = ltw.goal_path()
    if not goal_path.is_file() or len((goal_text or goal_path.read_text(encoding="utf-8")).strip()) < 50:
        why_not.append("durable_project_goal_missing")
    complete_caps: list[str] = []
    partial_caps: list[str] = []
    for capability, specs in GOAL_CAPABILITY_EVIDENCE.items():
        missing = [s for s in specs if not _symbol_exists(s)]
        evidence[capability] = {"missing": missing, "ok": not missing}
        if missing:
            partial_caps.append(capability)
            why_not.append("capability_" + capability)
        else:
            complete_caps.append(capability)
    if not OPEN_GAPS_STATE_PATH.is_file():
        why_not.append("open_gaps_state_missing")
    if not CONTINUITY_STATE_PATH.is_file():
        why_not.append("continuity_state_missing")
    schedule = load_schedule_config()
    if not schedule.get("enabled"):
        why_not.append("schedule_disabled")
    if not (schedule.get("runs") or []):
        why_not.append("schedule_windows_missing")
    status_text = status_text or ""
    if "## Loop cycles" not in status_text and ltw.status_path().is_file():
        status_text = ltw.status_path().read_text(encoding="utf-8")
    if "## Loop cycles" not in status_text:
        why_not.append("status_not_loop_derived")
    return {
        "mechanics_complete": not why_not,
        "why_not": why_not,
        "evidence": evidence,
        "complete_caps": complete_caps,
        "partial_caps": partial_caps,
        "blocked_caps": [],
    }

def _operational_assessment(*, state: dict[str, Any], mechanics: dict[str, Any]) -> dict[str, Any]:
    import loop_target_workspace as ltw
    from loop_cost_policy import budget_status, get_run_profile
    why_not: list[str] = []
    if not mechanics.get("mechanics_complete"):
        why_not.append("mechanics_incomplete")
    if ltw.is_external_target():
        why_not.append("target_mode_active")
    hist = load_autonomous_history()
    seq = [r for r in (hist.get("sequence") or []) if r.get("ran")]
    window = seq[-OPERATIONAL_MIN_CONSECUTIVE_RUNS:]
    if len(window) < OPERATIONAL_MIN_CONSECUTIVE_RUNS:
        why_not.append("insufficient_consecutive_autonomous_runs:" + str(len(window)) + "/" + str(OPERATIONAL_MIN_CONSECUTIVE_RUNS))
    def _is_operational_progress(r: dict[str, Any]) -> bool:
        if not r.get("meaningful_product_progress"):
            return False
        pid = str(r.get("plan_id") or "")
        if pid in PROOF_REVALIDATION_WORK_IDS or pid.startswith("product_gap_"):
            return False
        return True

    progress_runs = [r for r in window if _is_operational_progress(r)]
    honest_runs = [
        r for r in window
        if _is_operational_progress(r)
        or str(r.get("outcome_class") or r.get("blocked_classification") or "") in HONEST_BLOCKER_OUTCOMES
    ]
    if window and len(honest_runs) < len(window):
        why_not.append("non_honest_run_outcomes_in_window")
    if len(progress_runs) < OPERATIONAL_MIN_PROGRESS_RUNS:
        why_not.append("insufficient_meaningful_progress_runs:" + str(len(progress_runs)) + "/" + str(OPERATIONAL_MIN_PROGRESS_RUNS))
    continuity_hits = sum(1 for r in progress_runs if r.get("continuity_influenced"))
    if progress_runs:
        ratio = continuity_hits / max(1, len(progress_runs))
        if ratio < OPERATIONAL_MIN_CONTINUITY_RATIO:
            why_not.append("continuity_not_improving_selection:" + str(continuity_hits) + "/" + str(len(progress_runs)))
    else:
        why_not.append("continuity_not_improving_selection:0/0")
    worker_hits = sum(1 for r in window if r.get("worker_used") or r.get("expensive_execution"))
    if worker_hits:
        why_not.append("expensive_or_worker_execution_in_window:" + str(worker_hits))
    plan_counts: dict[str, int] = {}
    for r in progress_runs:
        pid = str(r.get("plan_id") or "")
        if pid:
            plan_counts[pid] = plan_counts.get(pid, 0) + 1
    if plan_counts:
        top_plan, top_count = max(plan_counts.items(), key=lambda kv: kv[1])
        if top_count >= 3 and top_plan.startswith("product_gap_"):
            why_not.append("repeated_low_value_bookkeeping_loop:" + top_plan)
    proofish = [
        r for r in progress_runs
        if str(r.get("plan_id") or "") in PROOF_REVALIDATION_WORK_IDS
        or str(r.get("plan_id") or "").startswith("product_gap_")
    ]
    if progress_runs and len(proofish) == len(progress_runs):
        why_not.append("proof_mode_dependence")
    budget = budget_status(state=state)
    if str(budget.get("budget_mode") or "cheap_default") != "cheap_default":
        why_not.append("budget_mode_not_cheap_default")
    if budget.get("allow_expensive_execution"):
        why_not.append("expensive_execution_enabled")
    profile = get_run_profile()
    if profile not in {None, "", "cheap_default"}:
        why_not.append("non_cheap_run_profile:" + str(profile))
    if state.get("budget_blocked"):
        why_not.append("budget_blocked")
    if str(state.get("blocked_classification") or "") == "verification_blocked":
        why_not.append("verification_blocked")
    operationally_realized = bool(mechanics.get("mechanics_complete")) and not why_not
    return {
        "operationally_realized": operationally_realized,
        "why_not": why_not,
        "window_runs": len(window),
        "progress_runs": len(progress_runs),
        "continuity_influenced_progress_runs": continuity_hits,
        "worker_used_count": worker_hits,
        "window": [
            {
                "cycle_id": r.get("cycle_id"),
                "plan_id": r.get("plan_id"),
                "outcome_class": r.get("outcome_class"),
                "meaningful_product_progress": r.get("meaningful_product_progress"),
                "continuity_influenced": r.get("continuity_influenced"),
                "worker_used": r.get("worker_used"),
            }
            for r in window
        ],
    }

def evaluate_product_complete(
    *,
    state: dict[str, Any] | None = None,
    goal_text: str = "",
    status_text: str = "",
) -> dict[str, Any]:
    state = state or {}
    mechanics = _mechanics_assessment(state=state, goal_text=goal_text, status_text=status_text)
    operational = _operational_assessment(state=state, mechanics=mechanics)
    hist = load_autonomous_history()
    complete_caps = list(mechanics.get("complete_caps") or [])
    partial_caps = list(mechanics.get("partial_caps") or [])
    blocked_caps = list(mechanics.get("blocked_caps") or [])
    why_not_realized = list(operational.get("why_not") or [])
    if not mechanics.get("mechanics_complete"):
        why_not_realized = list(mechanics.get("why_not") or []) + [
            x for x in why_not_realized if x != "mechanics_incomplete"
        ]
    next_missing = ""
    if not mechanics.get("mechanics_complete"):
        for cap in END_GOAL_CAPABILITIES:
            if cap in partial_caps:
                next_missing = cap
                break
        if not next_missing and mechanics.get("why_not"):
            next_missing = str(mechanics["why_not"][0])
    elif not operational.get("operationally_realized"):
        next_missing = "operational_validation"
        if "operational_validation" not in partial_caps:
            partial_caps.append("operational_validation")
    operationally_realized = bool(operational.get("operationally_realized"))
    mechanics_complete = bool(mechanics.get("mechanics_complete"))
    if mechanics_complete and not operationally_realized:
        reason = "mechanics_complete_but_operationally_unproven"
    elif operationally_realized:
        reason = "purple_halo_operationally_realized"
    else:
        reason = "mechanics_incomplete"
    progress = {
        "goal_realized": operationally_realized,
        "mechanics_complete": mechanics_complete,
        "operationally_realized": operationally_realized,
        "why_not_realized": why_not_realized,
        "reason": reason,
        "complete": [c for c in END_GOAL_CAPABILITIES if c in complete_caps and c not in partial_caps],
        "partial": [c for c in END_GOAL_CAPABILITIES if c in partial_caps] + (
            ["operational_validation"] if mechanics_complete and not operationally_realized and "operational_validation" not in partial_caps else []
        ),
        "blocked": blocked_caps,
        "next_missing_capability": next_missing,
        "capabilities": {
            cap: {
                "status": (
                    "complete" if cap in complete_caps and cap not in partial_caps else
                    "partial" if cap in partial_caps else
                    "blocked" if cap in blocked_caps else
                    "partial"
                ),
                "detail": (mechanics.get("evidence") or {}).get(cap, {}),
            }
            for cap in END_GOAL_CAPABILITIES
        },
        "operational_gate": operational,
        "continuity_steered_progress_runs": operational.get("continuity_influenced_progress_runs") or 0,
        "long_run_mode": bool(hist.get("long_run_mode")),
    }
    # ensure operational_validation appears in partial list uniquely
    partial = []
    for item in progress["partial"]:
        if item not in partial:
            partial.append(item)
    progress["partial"] = partial
    goal_realized = operationally_realized
    try:
        from loop_backlog import load_backlog
        if str(load_backlog().get("empty_reason") or "") == "product_complete":
            goal_realized = True
            reason = "product_complete"
    except Exception:
        pass
    try:
        from loop_goal_delivery import criteria_complete, goal_delivery_active
        if goal_delivery_active():
            if not criteria_complete():
                goal_realized = False
                if "goal_criteria_incomplete" not in why_not_realized:
                    why_not_realized = list(why_not_realized) + ["goal_criteria_incomplete"]
                reason = "goal_delivery_criteria_incomplete"
            else:
                goal_realized = True
                reason = "all_goal_criteria_complete"
    except Exception:
        pass
    progress["goal_realized"] = goal_realized
    progress["why_not_realized"] = why_not_realized
    return {
        "product_complete": goal_realized,
        "goal_realized": goal_realized,
        "mechanics_complete": mechanics_complete,
        "operationally_realized": operationally_realized,
        "why_not_realized": why_not_realized,
        "reason": reason,
        "incomplete": why_not_realized,
        "evidence": mechanics.get("evidence") or {},
        "progress": progress,
    }


def _continuity_next_step(state: dict[str, Any] | None = None) -> dict[str, Any]:
    from loop_continuity_state import continuity_status_summary, load_continuity_state, resume_from_continuity

    state = state or {}
    cont = load_continuity_state()
    meta = resume_from_continuity(state=state, open_gaps=list(cont.get("carried_forward_open_gaps") or []), allow_stale=True)
    summary = continuity_status_summary(state=state)
    focus = meta.get("active_gap_focus") or cont.get("active_gap_focus") or {}
    return {
        "resumed_prior_intent": bool(meta.get("resumed_prior_intent")),
        "active_gap_focus": focus,
        "next_intended_capability_step": cont.get("next_intended_capability_step") or focus.get("id") or "",
        "freshness": (summary.get("status") or cont.get("freshness") or "missing"),
        "used_stale": bool(meta.get("used_stale")),
        "reason": meta.get("reason") or "",
    }


def _has_meaningful_work(state: dict[str, Any] | None = None) -> tuple[bool, str]:
    from loop_backlog import is_meaningful_product_item, load_backlog, open_items
    from loop_open_gaps_state import load_open_gaps_state

    backlog = load_backlog()
    meaningful = []
    for i in open_items(backlog):
        if not is_meaningful_product_item(i):
            continue
        wid = str(i.get("work_id") or "")
        if wid in PROOF_REVALIDATION_WORK_IDS:
            continue
        cap = str(i.get("capability") or "")
        if wid.startswith("product_gap_") and cap:
            specs = GOAL_CAPABILITY_EVIDENCE.get(cap, ())
            if specs and all(_symbol_exists(s) for s in specs):
                continue
        meaningful.append(i)
    if meaningful:
        return True, "open_meaningful_item:" + str(meaningful[0].get("work_id"))
    cont = _continuity_next_step(state)
    focus_id = str((cont.get("active_gap_focus") or {}).get("id") or "")
    ignore_gaps = {
        "gap_status_open_gaps",
        "gap_research_artifact_binding",
        "gap_product_realization",
        "gap_continuity_open_gaps",
    }
    if cont.get("resumed_prior_intent") and focus_id and focus_id not in ignore_gaps:
        return True, "continuity_focus:" + focus_id
    ogs = load_open_gaps_state()
    gaps = [g for g in (ogs.get("open_gaps") or []) if isinstance(g, dict) and g.get("id")]
    actionable = [g for g in gaps if str(g.get("id") or "") not in ignore_gaps]
    if actionable:
        return True, "open_gap:" + str(actionable[0].get("id"))
    # Mechanics-complete systems still need operational validation work.
    assessment = evaluate_product_complete(state=state or {})
    if assessment.get("mechanics_complete") and not assessment.get("operationally_realized"):
        return True, "operational_validation_needed"
    if live_soak_active():
        return True, "live_soak_sustaining_work"
    from loop_production_ops import production_ops_active
    from loop_goal_delivery import delivery_work_specs, goal_delivery_active, linked_improve_specs
    if production_ops_active() or goal_delivery_active():
        linked = delivery_work_specs() + linked_improve_specs()
        if linked:
            top = linked[0]
            return True, (
                "goal_delivery:" + str(top.get("success_criterion_id") or top.get("work_id"))
                + ":" + str(top.get("evidence_will_move") or "")[:80]
            )
        return False, "no_criterion_linked_work"
    return False, "no_meaningful_work"


def _project_mode_runs_today(history: dict[str, Any] | None = None) -> int:
    """Count successful project_mode runs today (simple daily cap)."""
    history = history or load_autonomous_history()
    today = _today()
    return sum(
        1
        for r in history.get("sequence") or []
        if r.get("ran")
        and str(r.get("started_at") or "").startswith(today)
        and str(r.get("outcome_class") or "") not in {"no_due_slot", "operator_paused"}
    )


def _decide_project_mode_run(
    *,
    trigger: str,
    state: dict[str, Any],
    schedule: dict[str, Any],
    continuity: dict[str, Any],
) -> dict[str, Any]:
    """Product A: drop-in loop — operator, budget, daily cap, mission complete."""
    history = load_autonomous_history()
    max_runs = int(schedule.get("max_runs_per_day") or 8)
    today_count = _project_mode_runs_today(history)
    decision: dict[str, Any] = {
        "decision_id": str(uuid.uuid4())[:8],
        "trigger": trigger,
        "decided_at": _now_iso(),
        "allow": True,
        "classification": "run",
        "why_run": "",
        "why_selected": "",
        "research_used": None,
        "research_reason": "deferred_to_cycle",
        "continue_later": True,
        "continue_reason": "",
        "stop_condition": "",
        "continuity": continuity,
        "max_runs_per_day": max_runs,
        "runs_today": today_count,
        "mode": "project_mode",
    }
    if not history.get("autonomous_allowed", True):
        decision.update(
            allow=False,
            classification=str(history.get("stop_classification") or "operator_paused"),
            why_run=str(history.get("stop_reason") or "operator paused"),
            continue_later=False,
            stop_condition=str(history.get("stop_classification") or "operator_paused"),
        )
        return decision
    if state.get("budget_blocked"):
        decision.update(
            allow=False,
            classification="budget_blocked",
            why_run="budget_blocked: " + str(state.get("budget_blocked_reason") or "budget_blocked"),
            continue_later=True,
            continue_reason="resume when budget block is cleared",
            stop_condition="budget_blocked",
        )
        return decision
    complete = evaluate_product_complete(state=state)
    if complete.get("goal_realized") or complete.get("product_complete"):
        decision.update(
            allow=False,
            classification="goal_realized",
            why_run="operator mission success criteria are complete",
            continue_later=False,
            stop_condition="goal_realized",
            product_complete=complete,
            goal_realized=complete,
            goal_realization_progress=complete.get("progress") or {},
        )
        return decision
    if today_count >= max_runs:
        decision.update(
            allow=False,
            classification="max_runs_per_day",
            why_run=f"already executed {today_count} runs today (max {max_runs})",
            continue_later=True,
            continue_reason="resume next day within schedule windows",
            stop_condition="max_runs_per_day",
        )
        return decision
    focus = continuity.get("active_gap_focus") or {}
    decision["why_selected"] = str(focus.get("id") or continuity.get("next_intended_capability_step") or "mission")
    decision["why_run"] = "project_mode scheduled run toward operator mission"
    decision["continue_reason"] = "continue until mission complete, blocker, or operator pause"
    decision["stop_condition"] = "goal_realized|budget_blocked|operator_pause|max_runs_per_day"
    decision["product_complete"] = complete
    return decision


def decide_autonomous_run(*, trigger: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pre-run gate for repeated self-mode autonomy."""
    import loop_target_workspace as ltw
    from loop_state import load_state

    state = state if state is not None else load_state()
    schedule = load_schedule_config()
    continuity = _continuity_next_step(state)
    if str(schedule.get("mode")) == "project_mode":
        return _decide_project_mode_run(
            trigger=trigger, state=state, schedule=schedule, continuity=continuity
        )
    from loop_production_ops import ensure_production_candidate_operations, production_ops_active
    from loop_goal_delivery import criteria_complete, ensure_goal_delivery_mode, goal_delivery_active
    from loop_production_hold import (
        ensure_production_hold_mode,
        evaluate_hold_run,
        production_hold_active,
        record_hold_run,
    )
    history = ensure_goal_delivery_mode()
    history = ensure_production_hold_mode()
    decision = {
        "decision_id": str(uuid.uuid4())[:8],
        "trigger": trigger,
        "decided_at": _now_iso(),
        "allow": True,
        "classification": "run",
        "why_run": "",
        "why_selected": "",
        "research_used": None,
        "research_reason": "deferred_to_cycle",
        "continue_later": True,
        "continue_reason": "",
        "stop_condition": "",
        "continuity": continuity,
        "max_runs_per_day": int(schedule.get("max_runs_per_day") or 4),
        "runs_today": len(runs_today(history)),
    }

    if ltw.is_external_target():
        decision.update(
            allow=False,
            classification="target_mode_active",
            why_run="external target mode is active; autonomous self runs are paused",
            continue_later=False,
            stop_condition="return_to_self_product_mode",
        )
        return decision

    if not history.get("autonomous_allowed", True):
        decision.update(
            allow=False,
            classification=str(history.get("stop_classification") or "operator_paused"),
            why_run=str(history.get("stop_reason") or "autonomous operation halted"),
            continue_later=False,
            stop_condition=str(history.get("stop_classification") or "operator_paused"),
        )
        return decision

    if production_hold_active(history):
        hold = evaluate_hold_run()
        decision["hold_run_kind"] = hold.get("run_kind") or ""
        decision["ledger_intact"] = bool(hold.get("ledger_intact"))
        decision["reopened_criteria"] = hold.get("reopened_criteria") or []
        if hold.get("run_kind") == "verify_only":
            decision.update(
                allow=False,
                classification="verify_only_healthy",
                why_run=str(hold.get("why") or "production hold: healthy verify_only"),
                continue_later=True,
                continue_reason="next scheduled health verification",
                stop_condition="verify_only_healthy",
            )
            return decision
        # repair mode: allow implementation of repair items only
        decision["why_run"] = str(hold.get("why") or "production hold: regression repair")

    if state.get("budget_blocked"):
        decision.update(
            allow=False,
            classification="budget_blocked",
            why_run="budget_blocked: " + str(state.get("budget_blocked_reason") or "budget_blocked"),
            continue_later=True,
            continue_reason="resume when budget block is cleared",
            stop_condition="budget_blocked",
        )
        return decision

    if str(state.get("blocked_classification") or "") == "verification_blocked" and not state.get("meaningful_product_progress"):
        # Production ops records honest blockers and continues; repeated regressions auto-pause.
        if not production_ops_active(history):
            decision.update(
                allow=False,
                classification="verification_blocked",
                why_run="previous cycle is verification_blocked: "
                + str(state.get("cycle_outcome_reason") or "verification_blocked"),
                continue_later=True,
                continue_reason="resume after repair or new blocker insight",
                stop_condition="verification_blocked",
            )
            return decision

    complete = evaluate_product_complete(state=state)
    history = load_autonomous_history()
    if live_soak_active(history):
        pass
    elif production_hold_active(history) and decision.get("hold_run_kind") == "repair":
        # Hold-mode repair must run even though criteria were previously complete.
        pass
    elif production_ops_active(history) or goal_delivery_active(history):
        # Production/goal-delivery continues until success criteria are actually complete.
        # In hold verify_only, we already returned above.
        if criteria_complete() and (complete.get("goal_realized") or complete.get("product_complete")):
            if production_hold_active(history):
                # Healthy hold is verify_only (handled earlier). If we reach here, stay idle.
                decision.update(
                    allow=False,
                    classification="verify_only_healthy",
                    why_run="production hold: criteria complete, no repair needed",
                    continue_later=True,
                    continue_reason="next scheduled health verification",
                    stop_condition="verify_only_healthy",
                )
                decision["hold_run_kind"] = "verify_only"
                decision["ledger_intact"] = True
                return decision
            decision.update(
                allow=False,
                classification="product_complete" if complete.get("reason") == "product_complete" else "goal_realized",
                why_run="all project_goals.md success criteria are complete",
                continue_later=False,
                stop_condition="goal_realized",
                product_complete=complete,
                goal_realized=complete,
                goal_realization_progress=complete.get("progress") or {},
            )
            return decision
    elif complete.get("goal_realized") or complete.get("product_complete"):
        decision.update(
            allow=False,
            classification="product_complete" if complete.get("reason") == "product_complete" else "goal_realized",
            why_run="purple_halo project goal is realized",
            continue_later=False,
            stop_condition="goal_realized",
            product_complete=complete,
            goal_realized=complete,
            goal_realization_progress=complete.get("progress") or {},
        )
        return decision

    if production_ops_active(history):
        try:
            from loop_cost_policy import monthly_token_status
            monthly = monthly_token_status(ceiling=int(schedule.get("monthly_token_ceiling") or 500_000))
            if monthly.get("at_ceiling"):
                decision.update(
                    allow=False,
                    classification="monthly_token_ceiling",
                    why_run="monthly token ceiling reached: "
                    + str(monthly.get("monthly_token_usage")) + "/" + str(monthly.get("monthly_token_ceiling")),
                    continue_later=True,
                    continue_reason="resume next month or raise monthly_token_ceiling",
                    stop_condition="monthly_token_ceiling",
                )
                return decision
        except Exception:
            pass

    has_work, work_reason = _has_meaningful_work(state)
    if not has_work:
        if work_reason == "no_criterion_linked_work" or goal_delivery_active(history):
            decision.update(
                allow=False,
                classification="no_criterion_linked_work",
                why_run="no selectable work is tied to an unmet project_goals.md success criterion",
                continue_later=True,
                continue_reason="resume when an unmet criterion has a deliverable step",
                stop_condition="no_criterion_linked_work",
            )
            return decision
        decision.update(
            allow=False,
            classification="no_meaningful_product_step",
            why_run="no meaningful purple_halo capability work is available",
            continue_later=True,
            continue_reason="resume when a new capability gap or backlog item appears",
            stop_condition="no_meaningful_product_step",
        )
        return decision

    max_runs = int(schedule.get("max_runs_per_day") or 4)
    today_count = len(runs_today(history))
    decision["runs_today"] = today_count
    if today_count >= max_runs:
        decision.update(
            allow=False,
            classification="max_runs_per_day",
            why_run=f"already executed {today_count} autonomous runs today (max {max_runs})",
            continue_later=True,
            continue_reason="resume next UTC day within schedule windows",
            stop_condition="max_runs_per_day",
        )
        return decision

    if continuity.get("resumed_prior_intent"):
        focus = continuity.get("active_gap_focus") or {}
        decision["why_selected"] = (
            "resume continuity focus "
            + str(focus.get("id") or continuity.get("next_intended_capability_step") or "prior_intent")
        )
        decision["why_run"] = "scheduled/manual autonomous run resuming carried-forward continuity"
    else:
        decision["why_selected"] = work_reason
        decision["why_run"] = "scheduled/manual autonomous run for next meaningful purple_halo capability step"

    decision["continue_later"] = True
    decision["continue_reason"] = "continue while incomplete capabilities or continuity focus remain"
    decision["stop_condition"] = "goal_realized|budget_blocked|verification_blocked|externally_blocked|anti_spin_halt|no_meaningful_product_step|operator_pause"
    decision["product_complete"] = complete
    return decision


def classify_sequence_outcome(
    *,
    ran: bool,
    decision: dict[str, Any],
    cycle_result: dict[str, Any] | None = None,
) -> str:
    if not ran:
        cls = str(decision.get("classification") or "no_work_skip")
        if cls == "budget_blocked":
            return "budget_skip"
        if cls in {"product_complete", "goal_realized"}:
            return "goal_realized"
        if cls == "anti_spin_halt":
            return "anti_spin_halt"
        if cls == "sequence_complete_for_review":
            return "sequence_complete_for_review"
        if cls == "no_meaningful_product_step":
            return "no_work_skip"
        if cls == "max_runs_per_day":
            return "budget_skip" if False else "no_work_skip"
        if cls == "no_due_slot":
            return "no_due_slot"
        if cls == "target_mode_active":
            return "target_mode_active"
        if cls == "verification_blocked":
            return "verification_blocked"
        return cls
    cycle_result = cycle_result or {}
    if cycle_result.get("meaningful_product_progress"):
        return "meaningful_product_progress"
    blocked = str(cycle_result.get("blocked_classification") or "")
    if blocked:
        return blocked
    if cycle_result.get("stopped"):
        return str(cycle_result.get("stop_reason") or "blocked")
    return "blocked"


def _new_blocker_insight(prev: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if not prev:
        return True
    prev_crit = str(prev.get("success_criterion_id") or "")
    cur_crit = str(current.get("success_criterion_id") or "")
    if cur_crit and cur_crit != prev_crit:
        return True
    prev_plan = str(prev.get("plan_id") or "")
    cur_plan = str(current.get("plan_id") or "")
    if cur_plan.startswith("deliver_") and cur_plan != prev_plan:
        return True
    prev_block = str(prev.get("blocked_classification") or prev.get("outcome_class") or "")
    cur_block = str(current.get("blocked_classification") or current.get("outcome_class") or "")
    if not cur_block:
        return False
    if cur_block != prev_block:
        return True
    prev_reason = str(prev.get("outcome_reason") or prev.get("reason") or "")
    cur_reason = str(current.get("outcome_reason") or current.get("reason") or "")
    return bool(cur_reason) and cur_reason != prev_reason




def apply_anti_spin(history: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    seq = list(history.get("sequence") or [])
    executed = [
        r
        for r in seq
        if r.get("ran")
        and str(r.get("outcome_class") or "")
        not in {
            "budget_skip",
            "no_work_skip",
            "product_complete_stop",
            "no_due_slot",
            "target_mode_active",
            "max_runs_per_day",
        }
    ]
    # entry is already appended to history["sequence"] before this call
    recent = executed[-ANTI_SPIN_STREAK:]
    if len(recent) < ANTI_SPIN_STREAK:
        return history
    if not all(not r.get("meaningful_product_progress") for r in recent):
        return history
    insight = False
    for i in range(1, len(recent)):
        if _new_blocker_insight(recent[i - 1], recent[i]):
            insight = True
            break
    if insight:
        return history
    history["autonomous_allowed"] = False
    history["stop_classification"] = "anti_spin_halt"
    history["stop_reason"] = (
        str(ANTI_SPIN_STREAK)
        + " consecutive autonomous runs produced no meaningful product progress and no new blocker insight"
    )
    entry["anti_spin_halt"] = True
    return history


def record_autonomous_run(
    *,
    decision: dict[str, Any],
    cycle_result: dict[str, Any] | None = None,
    ran: bool,
) -> dict[str, Any]:
    history = load_autonomous_history()
    cycle_result = cycle_result or {}
    outcome_class = classify_sequence_outcome(ran=ran, decision=decision, cycle_result=cycle_result)
    entry = {
        "id": str(decision.get("decision_id") or str(uuid.uuid4())[:8]),
        "started_at": decision.get("decided_at") or _now_iso(),
        "finished_at": _now_iso(),
        "trigger": decision.get("trigger"),
        "ran": ran,
        "outcome_class": outcome_class,
        "meaningful_product_progress": bool(cycle_result.get("meaningful_product_progress")),
        "blocked_classification": str(
            cycle_result.get("blocked_classification")
            or (decision.get("classification") if not ran else "")
            or ""
        ),
        "selected_capability": cycle_result.get("selected_capability") or "",
        "plan_id": cycle_result.get("plan_id") or cycle_result.get("backlog_work_id") or "",
        "success_criterion_id": cycle_result.get("success_criterion_id") or "",
        "evidence_will_move": cycle_result.get("evidence_will_move") or "",
        "task_type": cycle_result.get("task_type") or "",
        "local_only": bool(cycle_result.get("local_only", True)),
        "next_cycle_effect": cycle_result.get("next_cycle_effect") or "",
        "task_type": cycle_result.get("task_type") or "",
        "local_only": bool(cycle_result.get("local_only", True)),
        "next_cycle_effect": cycle_result.get("next_cycle_effect") or "",
        "task_type": cycle_result.get("task_type") or "",
        "local_only": bool(cycle_result.get("local_only", True)),
        "next_cycle_effect": cycle_result.get("next_cycle_effect") or "",
        "cycle_id": cycle_result.get("cycle_id"),
        "why_run": decision.get("why_run") or "",
        "why_selected": decision.get("why_selected") or str(cycle_result.get("cycle_outcome_reason") or ""),
        "research_used": cycle_result.get("research_used"),
        "research_reason": cycle_result.get("research_reason") or decision.get("research_reason") or "",
        "continue_later": bool(decision.get("continue_later")),
        "continue_reason": "",
        "stop_condition": decision.get("stop_condition") or "",
        "outcome_reason": cycle_result.get("cycle_outcome_reason") or decision.get("why_run") or outcome_class,
        "continuity_focus": (decision.get("continuity") or {}).get("active_gap_focus") or {},
        "continuity_influenced": bool((decision.get("continuity") or {}).get("resumed_prior_intent")),
        "goal_capability": cycle_result.get("selected_capability") or str(((decision.get("continuity") or {}).get("active_gap_focus") or {}).get("classification") or ""),
        "next_run_should": "",
        "worker_used": bool(cycle_result.get("worker_outcome")),
        "expensive_execution": bool(cycle_result.get("worker_outcome")),
    }
    if ran:
        complete = evaluate_product_complete()
        if cycle_result.get("meaningful_product_progress"):
            entry["continue_later"] = not complete.get("product_complete")
            entry["continue_reason"] = (
                "goal_realized" if complete.get("goal_realized") or complete.get("product_complete") else "more_capability_work_remains"
            )
        elif cycle_result.get("blocked_classification") == "budget_blocked":
            entry["continue_later"] = True
            entry["continue_reason"] = "resume_when_budget_cleared"
        elif cycle_result.get("blocked_classification") == "verification_blocked":
            entry["continue_later"] = True
            entry["continue_reason"] = "resume_with_repair_or_new_insight"
        else:
            entry["continue_later"] = True
            entry["continue_reason"] = "resume_if_new_work_or_insight"
        entry["next_run_should"] = entry.get("continue_reason") or "continue_autonomous_loop"
    else:
        entry["next_run_should"] = decision.get("continue_reason") or decision.get("stop_condition") or "paused"
    history.setdefault("sequence", []).append(entry)
    history["sequence"] = history["sequence"][-SEQUENCE_LIMIT:]
    history = apply_anti_spin(history, entry)
    from loop_production_ops import apply_production_entry, production_ops_active
    if outcome_class in {"product_complete_stop", "goal_realized"}:
        if production_ops_active(history) or live_soak_active(history):
            pass
        else:
            history["autonomous_allowed"] = False
            history["stop_classification"] = "goal_realized"
            history["stop_reason"] = entry.get("outcome_reason") or "goal_realized"
    history["last_run"] = entry
    history["updated_at"] = _now_iso()
    save_autonomous_history(history)
    if live_soak_active(history):
        history = apply_soak_entry(entry)
    elif production_ops_active(history):
        history = apply_production_entry(entry)
    try:
        from loop_goal_delivery import goal_delivery_active, record_delivery_selection
        if goal_delivery_active(history):
            entry["success_criterion_id"] = (
                cycle_result.get("success_criterion_id")
                or (cycle_result.get("plan") or {}).get("success_criterion_id")
                or entry.get("success_criterion_id")
                or ""
            )
            entry["evidence_will_move"] = (
                cycle_result.get("evidence_will_move")
                or (cycle_result.get("plan") or {}).get("evidence_will_move")
                or entry.get("evidence_will_move")
                or ""
            )
            # Derive criterion from plan_id deliver_* / improve_* mapping when missing.
            if not entry["success_criterion_id"]:
                pid = str(entry.get("plan_id") or "")
                if pid.startswith("deliver_"):
                    entry["success_criterion_id"] = pid[len("deliver_"):]
            history = record_delivery_selection(entry)
    except Exception:
        pass
    return entry


def build_run_decision(
    *,
    decision: dict[str, Any],
    cycle_result: dict[str, Any] | None = None,
    research: dict[str, Any] | None = None,
) -> dict[str, Any]:
    research = research or {}
    cycle_result = cycle_result or {}
    research_used = bool(
        research.get("research_call_made")
        or research.get("research_source") not in {None, "", "cached", "skipped"}
        or cycle_result.get("research_used")
    )
    research_reason = str(
        research.get("budget_decision_reason")
        or research.get("research_source")
        or ("fresh_research" if research_used else "cached_or_skipped")
    )
    return {
        **decision,
        "research_used": research_used,
        "research_reason": research_reason,
        "why_selected": decision.get("why_selected")
        or cycle_result.get("plan_id")
        or cycle_result.get("backlog_work_id")
        or "",
        "cycle_id": cycle_result.get("cycle_id"),
        "plan_id": cycle_result.get("plan_id"),
        "selected_capability": cycle_result.get("selected_capability"),
        "meaningful_product_progress": cycle_result.get("meaningful_product_progress"),
        "blocked_classification": cycle_result.get("blocked_classification") or "",
        "outcome_reason": cycle_result.get("cycle_outcome_reason") or "",
        "updated_at": _now_iso(),
    }


def mark_sequence_complete_for_review(*, reason: str = "", proof_runs: int = 0) -> dict[str, Any]:
    history = load_autonomous_history()
    history["autonomous_allowed"] = False
    history["stop_classification"] = "sequence_complete_for_review"
    history["stop_reason"] = reason or (
        "bounded multi-run autonomous proof complete ("
        + str(proof_runs)
        + " runs); operator review is the next step"
    )
    history["proof_complete"] = True
    history["proof_runs"] = proof_runs
    history["updated_at"] = _now_iso()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "started_at": _now_iso(),
        "finished_at": _now_iso(),
        "trigger": "proof_controller",
        "ran": False,
        "outcome_class": "sequence_complete_for_review",
        "meaningful_product_progress": False,
        "blocked_classification": "sequence_complete_for_review",
        "why_run": history["stop_reason"],
        "why_selected": "sequence_proof_complete",
        "continuity_influenced": False,
        "next_run_should": "operator_review",
        "worker_used": False,
        "expensive_execution": False,
        "selected_capability": "",
        "goal_capability": "",
        "plan_id": "",
        "cycle_id": None,
    }
    history.setdefault("sequence", []).append(entry)
    history["sequence"] = history["sequence"][-SEQUENCE_LIMIT:]
    history["last_run"] = entry
    save_autonomous_history(history)
    return entry


def sequence_proof_summary(*, limit: int = 5) -> dict[str, Any]:
    history = load_autonomous_history()
    seq = [r for r in (history.get("sequence") or []) if r.get("ran")]
    proof = seq[-limit:]
    continuity_hits = sum(1 for r in proof if r.get("continuity_influenced"))
    progress_hits = sum(1 for r in proof if r.get("meaningful_product_progress"))
    worker_hits = sum(1 for r in proof if r.get("worker_used") or r.get("expensive_execution"))
    return {
        "proof_runs": proof,
        "run_count": len(proof),
        "progress_count": progress_hits,
        "continuity_influenced_count": continuity_hits,
        "continuity_improving": continuity_hits >= 1,
        "worker_used_count": worker_hits,
        "expensive_execution_count": worker_hits,
        "honest_stops": [r.get("outcome_class") for r in proof if not r.get("meaningful_product_progress")],
        "stop_classification": history.get("stop_classification") or "",
        "stop_reason": history.get("stop_reason") or "",
        "repeated_operation_allowed": bool(history.get("autonomous_allowed", True)),
    }


def _production_status_fields(history: dict[str, Any]) -> dict[str, Any]:
    from loop_production_ops import production_status_fields
    return production_status_fields(history)


def autonomous_status(*, state: dict[str, Any] | None = None) -> dict[str, Any]:
    from loop_state import load_state

    state = state if state is not None else load_state()
    history = ensure_long_run_mode()
    schedule = load_schedule_config()
    last = history.get("last_run") or ((history.get("sequence") or [None])[-1] or {})
    continuity = _continuity_next_step(state)
    complete = evaluate_product_complete(state=state)
    progress = complete.get("progress") or {}
    allowed = bool(history.get("autonomous_allowed", True)) and not (
        complete.get("goal_realized") or complete.get("product_complete")
    )
    if complete.get("goal_realized") or complete.get("product_complete"):
        next_reason = "goal_realized: purple_halo project goal is operationally realized"
    elif complete.get("mechanics_complete"):
        why = complete.get("why_not_realized") or progress.get("why_not_realized") or []
        next_reason = "mechanics_complete_but_operationally_unproven: " + ";".join(str(x) for x in why[:4])
    elif not history.get("autonomous_allowed", True):
        next_reason = str(history.get("stop_reason") or history.get("stop_classification") or "halted")
    elif continuity.get("resumed_prior_intent"):
        focus = continuity.get("active_gap_focus") or {}
        next_reason = "resume continuity focus " + str(
            focus.get("id") or continuity.get("next_intended_capability_step")
        )
    else:
        has_work, work_reason = _has_meaningful_work(state)
        next_reason = work_reason if has_work else "no_meaningful_work"
    return {
        "autonomous_allowed": allowed,
        "stop_classification": history.get("stop_classification")
        or ("goal_realized" if (complete.get("goal_realized") or complete.get("product_complete")) else ""),
        "stop_reason": history.get("stop_reason")
        or (complete.get("reason") if (complete.get("goal_realized") or complete.get("product_complete")) else ""),
        "last_run_outcome": last,
        "sequence_tail": list(history.get("sequence") or [])[-5:],
        "runs_today": len(runs_today(history)),
        "max_runs_per_day": int(schedule.get("max_runs_per_day") or 4),
        "schedule_enabled": bool(schedule.get("enabled")),
        "schedule_windows": list(schedule.get("runs") or []),
        "next_planned_run_reason": next_reason,
        "continuity_drive": continuity,
        "product_complete": complete,
        "goal_realized": bool(complete.get("goal_realized") or complete.get("product_complete")),
        "mechanics_complete": bool(complete.get("mechanics_complete") or progress.get("mechanics_complete")),
        "operationally_realized": bool(complete.get("operationally_realized") or progress.get("operationally_realized")),
        "why_not_realized": complete.get("why_not_realized") or progress.get("why_not_realized") or [],
        "goal_realization_progress": progress,
        "long_run_mode": bool(history.get("long_run_mode")),
        "repeated_operation_allowed": allowed,
        "sequence_health": sequence_proof_summary(limit=5),
        "validation_window": {
            "results": history.get("validation_window_results") or [],
            "window_size": len(history.get("validation_window_results") or []),
            "qualifying_progress_count": sum(
                1
                for r in (history.get("validation_window_results") or [])
                if r.get("qualifying_progress")
            ),
            "continuity_influence_rate": (
                (
                    sum(1 for r in (history.get("validation_window_results") or []) if r.get("continuity_influenced") and r.get("qualifying_progress"))
                    / max(1, sum(1 for r in (history.get("validation_window_results") or []) if r.get("qualifying_progress")))
                )
                if any(r.get("qualifying_progress") for r in (history.get("validation_window_results") or []))
                else 0.0
            ),
            "worker_used_count": sum(1 for r in (history.get("validation_window_results") or []) if r.get("worker_used")),
            "operationally_realized": bool(progress.get("operationally_realized")),
            "why_not_realized": progress.get("why_not_realized") or [],
        },
        "soak_health": evaluate_soak(history),
        "live_soak_mode": bool(history.get("live_soak_mode")),
        "live_soak_passed": bool(history.get("live_soak_passed")),
        "production_candidate": bool(history.get("production_candidate")),
        **_production_status_fields(history),
    }


def self_check() -> None:
    ensure_long_run_mode()
    complete = evaluate_product_complete(state={"cycle_id": 1})
    assert "product_complete" in complete
    assert "goal_realized" in complete
    assert "mechanics_complete" in complete
    assert "operationally_realized" in complete
    assert "why_not_realized" in complete
    assert "progress" in complete
    assert isinstance(complete.get("incomplete"), list)
    progress = complete["progress"]
    assert "complete" in progress and "partial" in progress and "blocked" in progress
    assert "next_missing_capability" in progress
    assert "mechanics_complete" in progress and "operationally_realized" in progress
    assert "why_not_realized" in progress
    if progress.get("mechanics_complete") and not progress.get("operationally_realized"):
        assert progress.get("reason") == "mechanics_complete_but_operationally_unproven"
        assert complete.get("goal_realized") is False
    decision = decide_autonomous_run(trigger="manual", state={"cycle_id": 1})
    assert "allow" in decision
    assert "classification" in decision
    history = {
        "sequence": [
            {
                "ran": True,
                "meaningful_product_progress": False,
                "blocked_classification": "verification_blocked",
                "outcome_class": "verification_blocked",
                "outcome_reason": "same",
            }
        ],
        "autonomous_allowed": True,
        "stop_classification": "",
        "stop_reason": "",
    }
    entry = {
        "ran": True,
        "meaningful_product_progress": False,
        "blocked_classification": "verification_blocked",
        "outcome_class": "verification_blocked",
        "outcome_reason": "same",
    }
    history.setdefault("sequence", []).append(entry)
    history = apply_anti_spin(history, entry)
    assert history["autonomous_allowed"] is False
    assert history["stop_classification"] == "anti_spin_halt"
    status = autonomous_status(state={"cycle_id": 1})
    assert "repeated_operation_allowed" in status
    assert "next_planned_run_reason" in status
    assert "sequence_health" in status
    assert "goal_realization_progress" in status
    assert "long_run_mode" in status
    assert status.get("long_run_mode") is True
    grp = status["goal_realization_progress"]
    assert "mechanics_complete" in grp and "operationally_realized" in grp
    assert "why_not_realized" in grp
    proof = sequence_proof_summary(limit=5)
    assert "run_count" in proof
    assert "continuity_improving" in proof
    assert "soak_health" in status
    assert "live_soak_mode" in status
    assert "live_soak_passed" in status
    assert "production_candidate" in status
    assert "production_candidate_operations" in status
    assert "regression_health" in status
    assert "daily_schedule" in status
    assert "monthly_token" in status
    assert "auto_pause_reason" in status
    assert "goal_delivery_mode" in status or "goal_delivery_ledger" in status
    assert "production_hold_mode" in status
    bad = detect_soak_failures([
        {"plan_id": "x", "selected_capability": "plan_generation", "meaningful_progress": False,
         "honest_blocker": False, "continuity_influenced": False, "cheap_default_respected": True},
        {"plan_id": "x", "selected_capability": "plan_generation", "meaningful_progress": False,
         "honest_blocker": False, "continuity_influenced": False, "cheap_default_respected": True},
        {"plan_id": "x", "selected_capability": "plan_generation", "meaningful_progress": False,
         "honest_blocker": False, "continuity_influenced": False, "cheap_default_respected": True},
    ])
    assert any(f.startswith("repeated_low_value_work") for f in bad)
    assert any(f.startswith("repeated_same_capability_churn") for f in bad)
    cost = detect_soak_failures([{"plan_id": "operational_cheap_default_guard", "worker_used": True,
                                  "cheap_default_respected": False, "meaningful_progress": True,
                                  "verification_truthful": True}])
    assert "token_cost_regression" in cost
    soak = evaluate_soak({"live_soak_mode": True, "live_soak_passed": False, "soak_results": [],
                          "soak_min_runs": 5, "soak_target_days": 3, "soak_start_date": _today()})
    assert soak["live_soak_passed"] is False
    assert soak["window_complete"] is False
    print("loop-autonomous: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo autonomous run control")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--decide", action="store_true")
    parser.add_argument("--start-live-soak", action="store_true")
    parser.add_argument("--start-production-ops", action="store_true")
    parser.add_argument("--start-production-hold", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.start_live_soak:
        print(json.dumps(start_live_soak(), indent=2))
        return 0
    if args.start_production_ops:
        from loop_production_ops import start_production_candidate_operations
        print(json.dumps(start_production_candidate_operations(), indent=2))
        return 0
    if args.start_production_hold:
        from loop_production_hold import ensure_production_hold_mode, hold_status_fields
        ensure_production_hold_mode()
        print(json.dumps(hold_status_fields(), indent=2))
        return 0
    if args.status:
        print(json.dumps(autonomous_status(), indent=2))
        return 0
    if args.decide:
        print(json.dumps(decide_autonomous_run(trigger="manual"), indent=2))
        return 0
    parser.error("specify --self-check, --status, --decide, or --start-live-soak")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
