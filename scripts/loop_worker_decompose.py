#!/usr/bin/env python3
"""Post-worker decomposition: follow-up and repair backlog items. Stdlib only."""
from __future__ import annotations
import argparse, json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def _slug(rel: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", rel.lower()).strip("_")[:40]
def analyze_output_completion(expected_outputs, changed_files):
    completed, missing = [], []
    for rel in expected_outputs:
        if (ROOT / rel).is_file() or rel in changed_files:
            completed.append(rel)
        else:
            missing.append(rel)
    return completed, missing

def build_followup_candidates(*, parent_work_id, missing_outputs, worker_result):
    out = []
    for rel in missing_outputs:
        wid = f"{parent_work_id}_followup_{_slug(rel)}"
        out.append({"work_id": wid, "title": f"Complete missing output: {rel}", "capability": worker_result.get("capability") or "implementation_dispatch", "goal_gap_addressed": worker_result.get("goal_gap_addressed") or "capability_implementation_dispatch", "task_type": "code_implementation", "priority": 4, "status": "open", "objective": f"Finish partial worker run for {parent_work_id}: deliver {rel}.", "why_now": f"Worker verified_partial; missing {rel}.", "parent_work_id": parent_work_id, "worker_evidence": {"outcome_class": worker_result.get("outcome_class"), "trace_id": worker_result.get("trace_id"), "completed_outputs": worker_result.get("completed_outputs") or []}, "target_files": [rel], "proposed_repo_delta": [rel], "expected_outputs": [rel], "execution_steps": [], "verification_commands": worker_result.get("verification_commands") or [], "done_when": [f"file exists: {rel}", f"changed: {rel}"], "generated_from": "verified_partial", "created_at": _now_iso()})
    return out
def build_repair_item(*, parent_work_id, failure_evidence, worker_result):
    targets = list(worker_result.get("target_files") or worker_result.get("changed_files") or worker_result.get("missing_outputs") or worker_result.get("expected_outputs") or [])
    return {"work_id": f"{parent_work_id}_repair", "title": f"Repair failed worker run: {parent_work_id}", "capability": worker_result.get("capability") or "implementation_dispatch", "goal_gap_addressed": worker_result.get("goal_gap_addressed") or "capability_implementation_dispatch", "task_type": "code_implementation", "priority": 3, "status": "open", "objective": f"Fix verification_failed worker run for {parent_work_id}.", "why_now": "Worker verification failed.", "parent_work_id": parent_work_id, "failure_evidence": failure_evidence, "worker_evidence": {"outcome_class": worker_result.get("outcome_class"), "trace_id": worker_result.get("trace_id"), "summary": worker_result.get("summary"), "errors": worker_result.get("errors") or []}, "target_files": targets, "proposed_repo_delta": targets, "expected_outputs": targets, "execution_steps": [], "verification_commands": worker_result.get("verification_commands") or [], "done_when": [f"file exists: {t}" for t in targets[:3]], "generated_from": "verification_failed", "created_at": _now_iso()}

def enrich_worker_result(result, *, expected_outputs, target_files=None):
    changed = list(result.get("changed_files") or [])
    expected = list(expected_outputs or target_files or [])
    completed, missing = analyze_output_completion(expected, changed)
    failure_evidence = [v for v in result.get("verification_output") or [] if v.get("result") == "fail" or v.get("exit_code", 0) != 0]
    if result.get("errors"):
        failure_evidence.append({"kind": "impl_error", "detail": "; ".join(result["errors"][:5])})
    parent = str(result.get("work_id") or "")
    outcome = str(result.get("outcome_class") or "")
    followups = []
    if outcome == "verified_partial" and missing:
        followups = build_followup_candidates(parent_work_id=parent, missing_outputs=missing, worker_result={**result, "expected_outputs": expected})
    elif outcome == "verification_failed":
        followups = [build_repair_item(parent_work_id=parent, failure_evidence=failure_evidence, worker_result={**result, "target_files": list(target_files or expected), "expected_outputs": expected})]
    result["completed_outputs"] = completed
    result["missing_outputs"] = missing
    result["failure_evidence"] = failure_evidence
    from loop_artifact_inputs import load_verification_brief

    brief, brief_meta = load_verification_brief(allow_stale=True)
    if brief.get("verification_commands") and not result.get("verification_commands"):
        result["verification_commands"] = list(brief["verification_commands"])
    if brief.get("success_conditions") and not result.get("done_when"):
        result["done_when"] = list(brief["success_conditions"])
    result["verification_brief_basis"] = {
        "source_hash": brief.get("source_hash"),
        "used_stale": brief_meta.get("used_stale", False),
        "work_id": brief.get("work_id"),
    }
    result["followup_candidates"] = followups
    return result
def _merge_generated_items(backlog, items):
    existing = {str(i.get("work_id")) for i in backlog.get("product_work_items") or []}
    added = []
    for item in items:
        wid = str(item.get("work_id") or "")
        if not wid or wid in existing:
            continue
        backlog.setdefault("product_work_items", []).append(item)
        existing.add(wid)
        added.append(wid)
    if added:
        backlog["updated_at"] = _now_iso()
    return added

def apply_post_worker_decomposition(backlog, worker_result, *, cycle_id):
    from loop_backlog import save_backlog
    outcome = str(worker_result.get("outcome_class") or "")
    candidates = list(worker_result.get("followup_candidates") or [])
    if not candidates:
        return {"generated": [], "outcome_class": outcome}
    for item in candidates:
        item["cycle_id"] = cycle_id
        item["source_cycle_id"] = cycle_id
    added = _merge_generated_items(backlog, candidates)
    if added:
        backlog["last_followup_generation"] = {"cycle_id": cycle_id, "parent_work_id": worker_result.get("work_id"), "outcome_class": outcome, "generated_work_ids": added, "generated_at": _now_iso()}
        save_backlog(backlog)
    return {"generated": added, "outcome_class": outcome, "parent_work_id": worker_result.get("work_id")}
def self_check():
    result = enrich_worker_result({"work_id": "goal_parser_runtime", "outcome_class": "verified_partial", "changed_files": ["scripts/goal_parser_runtime.py"], "verification_output": [{"result": "pass"}], "trace_id": "t"}, expected_outputs=["scripts/goal_parser_runtime.py", "project_memory/runtime/_self_check_missing_output.json"])
    assert result["followup_candidates"]
    fail = enrich_worker_result({"work_id": "plan_generator_runtime", "outcome_class": "verification_failed", "changed_files": ["scripts/plan_generator_runtime.py"], "verification_output": [{"result": "fail"}], "errors": ["x"], "target_files": ["scripts/plan_generator_runtime.py"]}, expected_outputs=["scripts/plan_generator_runtime.py"])
    assert fail["followup_candidates"][0]["work_id"] == "plan_generator_runtime_repair"
    added = _merge_generated_items({"product_work_items": []}, result["followup_candidates"])
    assert added
    print("loop-worker-decompose: PASS")
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--self-check", action="store_true")
    a = p.parse_args()
    if a.self_check:
        self_check()
        return 0
    p.error("specify --self-check")
    return 2
if __name__ == "__main__":
    raise SystemExit(main())
