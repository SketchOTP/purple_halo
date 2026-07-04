#!/usr/bin/env python3
"""Canonical runtime path integration for purple_halo loop. Stdlib only."""
from __future__ import annotations
import argparse
import importlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

READINESS = ("not_present", "present_unverified", "verified_minimal", "canonical_in_use")

RUNTIME_SPECS: dict[str, dict[str, str]] = {
    "goal_parser_runtime": {"path": "scripts/goal_parser_runtime.py", "module": "goal_parser_runtime", "stage": "goal_parsing"},
    "research_fetch_runtime": {"path": "scripts/research_fetch_runtime.py", "module": "research_fetch_runtime", "stage": "research_fetch"},
    "plan_generator_runtime": {"path": "scripts/plan_generator_runtime.py", "module": "plan_generator_runtime", "stage": "plan_generation"},
    "verification_runner_runtime": {"path": "scripts/verification_runner_runtime.py", "module": "verification_runner_runtime", "stage": "verification_execution"},
    "resume_continuity_runtime": {"path": "scripts/resume_continuity_runtime.py", "module": "resume_continuity_runtime", "stage": "resume_continuity"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _self_check_passes(work_id: str) -> bool:
    spec = RUNTIME_SPECS[work_id]
    path = ROOT / spec["path"]
    if not path.is_file():
        return False
    proc = subprocess.run([sys.executable, str(path)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def assess_readiness(work_id: str, *, canonical_in_use: set[str]) -> str:
    if work_id not in RUNTIME_SPECS:
        return "not_present"
    if not (ROOT / RUNTIME_SPECS[work_id]["path"]).is_file():
        return "not_present"
    if work_id in canonical_in_use:
        return "canonical_in_use"
    if _self_check_passes(work_id):
        return "verified_minimal"
    return "present_unverified"


def runtime_readiness_map(*, canonical_in_use: set[str] | None = None) -> dict[str, str]:
    canon = canonical_in_use or set()
    return {wid: assess_readiness(wid, canonical_in_use=canon) for wid in RUNTIME_SPECS}


def _load_module(work_id: str):
    return importlib.import_module(RUNTIME_SPECS[work_id]["module"])


def _generate_runtime_repair(work_id: str, *, failure_evidence: list[dict[str, Any]], cycle_id: int) -> str | None:
    from loop_backlog import load_backlog, save_backlog

    repair_id = f"{work_id}_integration_repair"
    backlog = load_backlog()
    existing = {str(i.get("work_id")) for i in backlog.get("product_work_items") or []}
    if repair_id in existing:
        return repair_id
    spec = RUNTIME_SPECS[work_id]
    item = {
        "work_id": repair_id,
        "title": f"Repair canonical runtime integration: {work_id}",
        "capability": "implementation_dispatch",
        "goal_gap_addressed": "capability_implementation_dispatch",
        "task_type": "code_implementation",
        "priority": 2,
        "status": "open",
        "objective": f"Fix canonical runtime failure for {work_id} in live loop path.",
        "why_now": "Canonical runtime module failed during cycle integration.",
        "parent_work_id": work_id,
        "failure_evidence": failure_evidence,
        "target_files": [spec["path"]],
        "proposed_repo_delta": [spec["path"]],
        "expected_outputs": [spec["path"]],
        "execution_steps": [],
        "verification_commands": [[sys.executable, spec["path"]]],
        "done_when": ["file exists: " + spec["path"],],
        "generated_from": "runtime_integration_failure",
        "cycle_id": cycle_id,
        "created_at": _now_iso(),
    }
    backlog.setdefault("product_work_items", []).append(item)
    backlog.setdefault("runtime_repairs", []).append({"work_id": repair_id, "parent": work_id, "cycle_id": cycle_id, "created_at": _now_iso()})
    save_backlog(backlog)
    return repair_id


def invoke_stage(
    work_id: str,
    *,
    canonical_in_use: set[str],
    cycle_id: int,
    canonical_call: Callable[[], Any],
    legacy_call: Callable[[], Any],
    validate: Callable[[Any], bool] | None = None,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    readiness = assess_readiness(work_id, canonical_in_use=canonical_in_use)
    use_canonical = readiness in {"verified_minimal", "canonical_in_use"}
    record: dict[str, Any] = {
        "work_id": work_id,
        "stage": RUNTIME_SPECS[work_id]["stage"],
        "readiness_before": readiness,
        "source": None,
        "fallback_used": False,
        "error": None,
        "repair_item": None,
    }
    if use_canonical:
        try:
            output = canonical_call()
            ok = validate(output) if validate else output is not None
            if not ok:
                raise ValueError(f"invalid output from {work_id}")
            record["source"] = "canonical"
            record["output"] = output
            record["readiness_after"] = "canonical_in_use"
            return record
        except Exception as exc:
            record["error"] = str(exc)
            evidence = [{"kind": "runtime_integration", "work_id": work_id, "detail": str(exc)}]
            record["repair_item"] = _generate_runtime_repair(work_id, failure_evidence=evidence, cycle_id=cycle_id)
            if not allow_fallback:
                record["source"] = "failed"
                return record
            record["fallback_used"] = True
    output = legacy_call()
    record["source"] = "legacy" if record.get("fallback_used") or not use_canonical else "legacy"
    record["output"] = output
    record["readiness_after"] = readiness if not record.get("fallback_used") else readiness
    if record.get("error") and record.get("fallback_used"):
        record["fallback_visible"] = True
    return record


def _load_json_brief() -> dict[str, Any]:
    from loop_artifact_inputs import load_verification_brief

    brief, _ = load_verification_brief(allow_stale=True)
    return brief


def integrate_cycle_runtime(
    *,
    cycle_id: int,
    state: dict[str, Any],
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
    legacy_research_fn: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from loop_backlog import backlog_summary, load_backlog, open_items
    from loop_research import resolve_research

    backlog = load_backlog()
    summary = backlog_summary(backlog)
    open_list = open_items(backlog)
    selected_item = summary.get("current_in_progress") or (open_list[0] if open_list else {})

    canonical = set(state.get("runtime_canonical") or [])
    stages: dict[str, Any] = {}
    repairs: list[str] = []
    canonical_used: set[str] = set()

    from loop_artifact_inputs import ensure_goal_model

    persisted_goal_model, goal_model_meta = ensure_goal_model(goal_text)
    gp = invoke_stage(
        "goal_parser_runtime",
        canonical_in_use=canonical,
        cycle_id=cycle_id,
        canonical_call=lambda: _load_module("goal_parser_runtime").parse_goals(goal_text),
        legacy_call=lambda: {"capabilities": [goal_text[:200]], "constraints": [], "raw_line_count": 0, "legacy": True},
        validate=lambda o: isinstance(o, dict) and bool(o.get("capabilities") is not None),
    )
    stages["goal_parsing"] = gp
    goal_model = dict(persisted_goal_model)
    runtime_parse = gp.get("output") or {}
    if isinstance(runtime_parse, dict):
        goal_model.setdefault("raw_line_count", runtime_parse.get("raw_line_count"))
    goal_model["goal_model_source"] = "goal_model.json"
    goal_model["goal_model_meta"] = goal_model_meta
    if gp.get("source") == "canonical":
        canonical_used.add("goal_parser_runtime")
    if gp.get("repair_item"):
        repairs.append(gp["repair_item"])

    rc = invoke_stage(
        "resume_continuity_runtime",
        canonical_in_use=canonical,
        cycle_id=cycle_id,
        canonical_call=lambda: _load_module("resume_continuity_runtime").build_resume_context(
            cycle_id=cycle_id,
            state=state,
            last_worker=state.get("last_worker"),
            goal_model=goal_model,
            verification_brief=(_load_json_brief()),
        ),
        legacy_call=lambda: {"cycle_id": cycle_id, "resume_reason": "legacy resume", "legacy": True},
        validate=lambda o: isinstance(o, dict) and o.get("cycle_id") == cycle_id,
    )
    stages["resume_continuity"] = rc
    resume_context = rc["output"]
    if rc.get("source") == "canonical":
        canonical_used.add("resume_continuity_runtime")
    if rc.get("repair_item"):
        repairs.append(rc["repair_item"])

    def _research_canonical():
        return _load_module("research_fetch_runtime").fetch_research_context(
            goal_excerpt=goal_text[:800], status_excerpt=status_text[:800], goal_model=goal_model
        )

    research_budget_meta: dict[str, Any] = {}

    def _research_legacy():
        if legacy_research_fn is not None:
            return legacy_research_fn()
        findings, meta = resolve_research(
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot=repo_snapshot,
            state=state,
            work_item=selected_item if isinstance(selected_item, dict) else None,
        )
        research_budget_meta.update(meta)
        return findings

    rf = invoke_stage(
        "research_fetch_runtime",
        canonical_in_use=canonical,
        cycle_id=cycle_id,
        canonical_call=_research_canonical,
        legacy_call=_research_legacy,
        validate=lambda o: isinstance(o, dict) and bool(o.get("summary") or o.get("goal_gap_addressed")),
    )
    stages["research_fetch"] = rf
    research = dict(rf["output"])
    research["goal_model"] = goal_model
    research["resume_context"] = resume_context
    research["runtime_stage"] = rf
    if rf.get("source") == "legacy" and isinstance(research, dict):
        research.setdefault("runtime_fallback", "research_fetch_runtime")
    if rf.get("source") == "canonical":
        canonical_used.add("research_fetch_runtime")
        if not research.get("goal_gap_addressed"):
            research["goal_gap_addressed"] = "capability_research_synthesis"
    if rf.get("repair_item"):
        repairs.append(rf["repair_item"])

    return {
        "cycle_id": cycle_id,
        "stages": stages,
        "goal_model": goal_model,
        "resume_context": resume_context,
        "research": research,
        "research_budget": research_budget_meta,
        "canonical_used": sorted(canonical_used),
        "repairs_generated": repairs,
        "readiness": runtime_readiness_map(canonical_in_use=canonical | canonical_used),
        "integrated_at": _now_iso(),
    }


def enrich_plan_with_runtime(*, plan: dict[str, Any], research: dict[str, Any], state: dict[str, Any], cycle_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical = set(state.get("runtime_canonical") or [])
    goal_model = research.get("goal_model") or {}
    backlog_item = {"work_id": plan.get("backlog_work_id"), "title": plan.get("focus"), "objective": plan.get("description")}

    pg = invoke_stage(
        "plan_generator_runtime",
        canonical_in_use=canonical,
        cycle_id=cycle_id,
        canonical_call=lambda: _load_module("plan_generator_runtime").generate_plan_brief(research=research, backlog_item=backlog_item, goal_model=goal_model),
        legacy_call=lambda: {"focus": plan.get("focus"), "objective": plan.get("description"), "research_summary": research.get("summary"), "legacy": True},
        validate=lambda o: isinstance(o, dict) and bool(o.get("focus") or o.get("objective")),
    )
    brief = pg["output"]
    merged = dict(plan)
    if pg.get("source") == "canonical":
        merged["focus"] = brief.get("focus") or merged.get("focus")
        merged["description"] = brief.get("objective") or merged.get("description")
        merged["research_summary"] = brief.get("research_summary") or merged.get("research_summary")
        merged["runtime_plan_brief"] = brief
        merged["runtime_plan_source"] = "canonical"
    else:
        merged["runtime_plan_source"] = "legacy"
        if pg.get("fallback_used"):
            merged["runtime_plan_fallback"] = True
    resume = research.get("resume_context") or {}
    merged["resume_context"] = resume
    merged["goal_model"] = goal_model
    return merged, pg


def run_runtime_verification_commands(*, commands: list[list[str]], state: dict[str, Any], cycle_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not commands:
        return [], {"skipped": True}
    canonical = set(state.get("runtime_canonical") or [])

    def _legacy_cmds():
        import subprocess as sp
        out = []
        for cmd in commands:
            proc = sp.run(cmd, cwd=ROOT, capture_output=True, text=True)
            out.append({"command": cmd, "exit_code": proc.returncode, "passed": proc.returncode == 0, "legacy": True})
        return out

    vr = invoke_stage(
        "verification_runner_runtime",
        canonical_in_use=canonical,
        cycle_id=cycle_id,
        canonical_call=lambda: _load_module("verification_runner_runtime").run_verification_suite(commands),
        legacy_call=_legacy_cmds,
        validate=lambda o: isinstance(o, list),
    )
    return list(vr["output"]), vr


def runtime_status_summary(*, state: dict[str, Any], backlog: dict[str, Any] | None = None) -> dict[str, Any]:
    canonical = set(state.get("runtime_canonical") or [])
    readiness = runtime_readiness_map(canonical_in_use=canonical)
    canonical_active = [wid for wid, r in readiness.items() if r == "canonical_in_use"]
    repairs = list((backlog or {}).get("runtime_repairs") or [])
    last_int = state.get("last_runtime_integration") or {}
    return {
        "readiness": readiness,
        "canonical_in_use": canonical_active,
        "last_integration": {
            "cycle_id": last_int.get("cycle_id"),
            "canonical_used": last_int.get("canonical_used") or [],
            "repairs_generated": last_int.get("repairs_generated") or [],
        },
        "runtime_repairs": repairs,
    }


def self_check() -> None:
    canon: set[str] = set()
    readiness = runtime_readiness_map(canonical_in_use=canon)
    assert readiness["goal_parser_runtime"] == "verified_minimal"
    assert "goal_parser_runtime" in RUNTIME_SPECS
    gp = invoke_stage(
        "goal_parser_runtime",
        canonical_in_use=canon,
        cycle_id=0,
        canonical_call=lambda: _load_module("goal_parser_runtime").parse_goals("- test goal"),
        legacy_call=lambda: {"capabilities": [], "constraints": []},
    )
    assert gp["source"] == "canonical"
    summary = runtime_status_summary(state={"runtime_canonical": ["goal_parser_runtime"]})
    assert summary["readiness"]["goal_parser_runtime"] == "canonical_in_use"
    print("loop-runtime-path: PASS")


def main() -> int:
    p = argparse.ArgumentParser(description="purple_halo runtime path integration")
    p.add_argument("--self-check", action="store_true")
    a = p.parse_args()
    if a.self_check:
        self_check()
        return 0
    p.error("specify --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
