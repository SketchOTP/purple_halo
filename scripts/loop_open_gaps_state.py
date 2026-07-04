#!/usr/bin/env python3
"""Hydrate and serve canonical open_gaps_state.json for the purple_halo loop. Stdlib only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from loop_target_workspace import goal_path, runtime_root, status_path  # noqa: E402

OPEN_GAPS_STATE_PATH = runtime_root() / "open_gaps_state.json"
STATE_PATH = runtime_root() / "loop_state.json"
GOAL_MODEL_PATH = runtime_root() / "goal_model.json"
VERIFY_BRIEF_PATH = runtime_root() / "verification_brief.json"

GAP_CLASSIFICATIONS: dict[str, str] = {
    "gap_scaffold_planner": "plan_generation",
    "gap_verification_evidence": "verification_dispatch",
    "gap_executor_actions": "implementation_dispatch",
    "gap_research_goal_binding": "research_synthesis",
    "gap_research_artifact_binding": "research_synthesis",
    "gap_continuity_open_gaps": "persistence_resume",
    "gap_verify_schedule": "schedule_control",
    "gap_scheduler_status": "schedule_control",
    "gap_status_open_gaps": "repo_status_analysis",
    "gap_product_realization": "plan_generation",
    "gap_scheduled_execution": "schedule_control",
}

UNBLOCK_BY_GAP: dict[str, str] = {
    "gap_verification_evidence": "Verification proves repo delta and runs plan verification_commands.",
    "gap_scheduled_execution": "Schedule runner executes loop cycles with recorded history.",
    "gap_continuity_open_gaps": "Loop state and open_gaps_state.json persist gaps between cycles.",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _path_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _latest_verification_outcome(state: dict[str, Any] | None = None) -> dict[str, Any]:
    st = state if isinstance(state, dict) else _load_json(STATE_PATH)
    last = st.get("last_cycle") if isinstance(st.get("last_cycle"), dict) else {}
    delta = st.get("last_verified_repo_delta") if isinstance(st.get("last_verified_repo_delta"), dict) else {}
    return {
        "verification_passed": bool(last.get("verification_passed")),
        "plan_id": str(last.get("plan_id") or ""),
        "artifact_dir": str(last.get("artifact_dir") or ""),
        "repo_delta_summary": str(delta.get("summary") or ""),
    }


def compute_source_hash(
    *,
    goal_text: str = "",
    status_text: str = "",
    plan: dict[str, Any] | None = None,
    verification_outcome: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    plan = plan or {}
    verification_outcome = verification_outcome if verification_outcome is not None else {}
    paths = {
        "project_goals.md": goal_path(),
        "project_status.md": status_path(),
        "goal_model.json": GOAL_MODEL_PATH,
        "verification_brief.json": VERIFY_BRIEF_PATH,
        "loop_state.json": STATE_PATH,
    }
    source_inputs: dict[str, Any] = {}
    for name, path in paths.items():
        source_inputs[name] = {"path": _rel(path), "hash": _path_hash(path)}
    plan_payload = {
        "plan_id": plan.get("plan_id"),
        "backlog_work_id": plan.get("backlog_work_id"),
        "goal_gap_addressed": plan.get("goal_gap_addressed"),
    }
    source_inputs["plan"] = {
        "plan_id": str(plan.get("plan_id") or ""),
        "hash": hashlib.sha256(json.dumps(plan_payload, sort_keys=True).encode()).hexdigest()[:16],
    }
    source_inputs["verification_outcome"] = verification_outcome
    source_hash = hashlib.sha256(json.dumps(source_inputs, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return source_hash, source_inputs


def merge_gaps(existing: list[dict[str, Any]], inferred: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for gap in existing:
        if isinstance(gap, dict) and gap.get("id"):
            by_id[str(gap["id"])] = dict(gap)
    for gap in inferred:
        if not isinstance(gap, dict) or not gap.get("id"):
            continue
        gid = str(gap["id"])
        prev = by_id.get(gid) or {}
        merged = {**prev, **gap}
        merged["blocker_history"] = list(prev.get("blocker_history") or gap.get("blocker_history") or [])
        merged["retry_count"] = int(prev.get("retry_count") or gap.get("retry_count") or 0)
        by_id[gid] = merged
    return sorted(by_id.values(), key=lambda g: int(g.get("priority") or 99))


def _enrich_gap(raw: dict[str, Any], *, source_inputs_used: list[str]) -> dict[str, Any]:
    gap_id = str(raw.get("id") or "")
    classification = str(raw.get("classification") or GAP_CLASSIFICATIONS.get(gap_id, "repo_status_analysis"))
    return {
        "id": gap_id,
        "description": str(raw.get("description") or gap_id),
        "classification": classification,
        "priority": int(raw.get("priority") or 99),
        "blocker_reason": str(raw.get("blocker_reason") or raw.get("description") or gap_id),
        "unblock_condition": str(raw.get("unblock_condition") or UNBLOCK_BY_GAP.get(gap_id, "Close via targeted product work.")),
        "linked_work_items": list(raw.get("linked_work_items") or []),
        "source_inputs_used": list(source_inputs_used),
        "blocker_history": list(raw.get("blocker_history") or []),
        "retry_count": int(raw.get("retry_count") or 0),
    }


def hydrate_open_gaps_state(
    *,
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
    state: dict[str, Any],
    research: dict[str, Any],
    plan: dict[str, Any] | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from loop_plan import analyze_goal_gaps

    existing_doc = existing if existing is not None else load_open_gaps_state()
    verification_outcome = _latest_verification_outcome(state)
    source_hash, source_inputs = compute_source_hash(
        goal_text=goal_text,
        status_text=status_text,
        plan=plan or {},
        verification_outcome=verification_outcome,
    )
    source_names = list(source_inputs.keys())
    inferred_raw = analyze_goal_gaps(
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=repo_snapshot,
        state=state,
        research=research,
    )
    inferred = [_enrich_gap(g, source_inputs_used=source_names) for g in inferred_raw]
    merged = merge_gaps(list(existing_doc.get("open_gaps") or []), inferred)
    gap_counts: dict[str, int] = {}
    for gap in merged:
        cls = str(gap.get("classification") or "unknown")
        gap_counts[cls] = gap_counts.get(cls, 0) + 1
    top = merged[0] if merged else {}
    return {
        "version": 1,
        "open_gaps": merged,
        "gap_counts_by_class": gap_counts,
        "top_gap": {
            "id": str(top.get("id") or ""),
            "reason": str(top.get("blocker_reason") or top.get("description") or ""),
        },
        "source_hash": source_hash,
        "source_inputs": source_inputs,
        "last_refreshed_at": _now_iso(),
        "freshness": "fresh",
    }


def open_gaps_state_freshness(
    *,
    goal_text: str = "",
    status_text: str = "",
    plan: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_hash, _ = compute_source_hash(
        goal_text=goal_text,
        status_text=status_text,
        plan=plan or {},
        verification_outcome=_latest_verification_outcome(state),
    )
    if not OPEN_GAPS_STATE_PATH.is_file():
        return {
            "status": "missing",
            "fresh": False,
            "stale": True,
            "present": False,
            "source_hash": None,
            "current_source_hash": current_hash,
            "hash_match": False,
            "gap_counts_by_class": {},
            "top_gap": {"id": "", "reason": ""},
        }
    payload = _load_json(OPEN_GAPS_STATE_PATH)
    stored = str(payload.get("source_hash") or "")
    hash_match = bool(stored and stored == current_hash)
    status = "fresh" if hash_match else "stale"
    return {
        "status": status,
        "fresh": hash_match,
        "stale": not hash_match,
        "present": True,
        "source_hash": stored or None,
        "current_source_hash": current_hash,
        "hash_match": hash_match,
        "last_refreshed_at": payload.get("last_refreshed_at"),
        "gap_counts_by_class": dict(payload.get("gap_counts_by_class") or {}),
        "top_gap": dict(payload.get("top_gap") or {"id": "", "reason": ""}),
    }


def load_open_gaps_state() -> dict[str, Any]:
    if not OPEN_GAPS_STATE_PATH.is_file():
        return {"version": 1, "open_gaps": [], "freshness": "missing"}
    return _load_json(OPEN_GAPS_STATE_PATH)


def save_open_gaps_state(state: dict[str, Any]) -> Path:
    OPEN_GAPS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPEN_GAPS_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return OPEN_GAPS_STATE_PATH


def gaps_for_planning(
    *,
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
    state: dict[str, Any],
    research: dict[str, Any],
    plan: dict[str, Any] | None = None,
    allow_stale: bool = True,
    regenerate: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    freshness = open_gaps_state_freshness(goal_text=goal_text, status_text=status_text, plan=plan, state=state)
    meta: dict[str, Any] = {
        "source": "open_gaps_state.json",
        "freshness": freshness,
        "used_stale": False,
        "regenerated": False,
    }
    if regenerate or not freshness.get("present") or not freshness.get("hash_match"):
        if not regenerate and freshness.get("present") and allow_stale:
            payload = load_open_gaps_state()
            gaps = [g for g in (payload.get("open_gaps") or []) if isinstance(g, dict)]
            if gaps:
                meta["used_stale"] = True
                meta["reason"] = "source_hash_mismatch"
                return gaps, meta
        hydrated = hydrate_open_gaps_state(
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot=repo_snapshot,
            state=state,
            research=research,
            plan=plan,
        )
        save_open_gaps_state(hydrated)
        meta["regenerated"] = True
        meta["freshness"] = open_gaps_state_freshness(goal_text=goal_text, status_text=status_text, plan=plan, state=state)
        return list(hydrated.get("open_gaps") or []), meta
    payload = load_open_gaps_state()
    return list(payload.get("open_gaps") or []), meta


def self_check() -> None:
    goal_text = "autonomous loop product\n"
    status_text = "# status\n"
    state = {"cycle_id": 0, "open_gaps": []}
    research = {"goal_gap_addressed": "gap_product_realization"}
    repo_snapshot = {"key_paths_present": {}}
    hydrated = hydrate_open_gaps_state(
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=repo_snapshot,
        state=state,
        research=research,
    )
    assert hydrated.get("open_gaps") is not None
    assert hydrated.get("source_hash")
    assert "top_gap" in hydrated
    save_open_gaps_state(hydrated)
    assert OPEN_GAPS_STATE_PATH.is_file()
    gaps, meta = gaps_for_planning(
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=repo_snapshot,
        state=state,
        research=research,
    )
    assert isinstance(gaps, list)
    assert meta["source"] == "open_gaps_state.json"
    merged = merge_gaps(
        [{"id": "gap_test", "retry_count": 2, "blocker_history": ["prior"]}],
        [
            {
                "id": "gap_test",
                "description": "updated",
                "priority": 1,
                "blocker_reason": "blocked",
                "unblock_condition": "done",
                "linked_work_items": [],
                "source_inputs_used": ["plan"],
                "classification": "plan_generation",
            }
        ],
    )
    assert merged[0]["retry_count"] == 2
    assert merged[0]["blocker_history"] == ["prior"]
    print("loop-open-gaps-state: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="open_gaps_state hydrator")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    parser.error("specify --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
