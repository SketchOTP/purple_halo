#!/usr/bin/env python3
"""Bounded implementation dispatch for purple_halo work packages. Stdlib only."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "project_memory" / "runtime"

HandlerFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(rel: str, payload: dict[str, Any]) -> str:
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return rel


def _run_command(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return {
        "command": cmd,
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _base_result(
    *,
    handler: str,
    package: dict[str, Any],
    outcome_status: str,
    actions_performed: list[str] | None = None,
    files_touched: list[str] | None = None,
    commands_run: list[dict[str, Any]] | None = None,
    verifier_hints: dict[str, Any] | None = None,
    message: str = "",
) -> dict[str, Any]:
    return {
        "work_id": package.get("work_id"),
        "dispatch_target": handler,
        "handler": handler,
        "outcome_status": outcome_status,
        "actions_performed": actions_performed or [],
        "files_touched": files_touched or [],
        "commands_run": commands_run or [],
        "verifier_hints": verifier_hints or {},
        "message": message,
        "dispatched_at": _now_iso(),
    }


def handle_goal_ingestion(package: dict[str, Any], handler_inputs: dict[str, Any]) -> dict[str, Any]:
    """Persist structured goal ingestion record for the active work package."""
    rel = "project_memory/runtime/goal_ingestion_index.json"
    path = ROOT / rel
    existing: dict[str, Any] = {"entries": []}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {"entries": []}
    inputs = package.get("inputs_used") or {}
    entry = {
        "work_id": package.get("work_id"),
        "cycle_id": package.get("cycle_id"),
        "objective": package.get("objective"),
        "capability": package.get("capability") or handler_inputs.get("capability_area"),
        "goal_excerpt": inputs.get("goal_excerpt") or handler_inputs.get("goal_excerpt", ""),
        "ingested_at": _now_iso(),
    }
    entries = [e for e in existing.get("entries") or [] if e.get("work_id") != entry["work_id"]]
    entries.append(entry)
    goal_text = ""
    goals_path = ROOT / "project_goals.md"
    if goals_path.is_file():
        goal_text = goals_path.read_text(encoding="utf-8")
    from loop_artifact_inputs import extract_goal_model, persist_goal_model_file

    model = extract_goal_model(goal_text, existing)
    payload = {
        "version": 1,
        "updated_at": _now_iso(),
        "entries": entries[-100:],
        "capabilities": model["capabilities"],
        "constraints": model["constraints"],
        "completion_criteria": model["completion_criteria"],
    }
    touched = [_write_json(rel, payload)]
    if goal_text:
        persist_goal_model_file(goal_text)
        touched.append("project_memory/runtime/goal_model.json")
    return _base_result(
        handler="goal_ingestion",
        package=package,
        outcome_status="completed",
        actions_performed=[f"append goal ingestion entry for {entry['work_id']}"],
        files_touched=touched,
        verifier_hints={
            "output_files": touched,
            "required_keys": ["entries"],
            "entry_work_id": entry["work_id"],
            "entry_fields": ["work_id", "cycle_id", "objective", "goal_excerpt"],
            "capabilities_extracted": model["capabilities"],
            "goal_model_path": "project_memory/runtime/goal_model.json",
        },
        message="Goal ingestion index and goal_model.json updated",
    )


def handle_research_synthesis(package: dict[str, Any], handler_inputs: dict[str, Any]) -> dict[str, Any]:
    """Bind research facts to capability area in durable synthesis log."""
    rel = "project_memory/runtime/research_synthesis_log.json"
    path = ROOT / rel
    existing: dict[str, Any] = {"records": []}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {"records": []}
    inputs = package.get("inputs_used") or {}
    record = {
        "work_id": package.get("work_id"),
        "cycle_id": package.get("cycle_id"),
        "capability_area": handler_inputs.get("capability_area") or inputs.get("research_capability_area") or package.get("capability"),
        "research_summary": inputs.get("research_summary") or handler_inputs.get("research_summary", ""),
        "synthesized_at": _now_iso(),
    }
    records = [r for r in existing.get("records") or [] if r.get("work_id") != record["work_id"]]
    records.append(record)
    payload = {"version": 1, "updated_at": _now_iso(), "records": records[-100:]}
    touched = [_write_json(rel, payload)]
    return _base_result(
        handler="research_synthesis",
        package=package,
        outcome_status="completed",
        actions_performed=[f"synthesize research for {record['work_id']}"],
        files_touched=touched,
        verifier_hints={
            "output_files": touched,
            "required_keys": ["records"],
            "record_work_id": record["work_id"],
            "required_capability_area": record["capability_area"],
        },
        message="Research synthesis log updated",
    )


def handle_verification_dispatch(package: dict[str, Any], handler_inputs: dict[str, Any]) -> dict[str, Any]:
    """Write verification brief and optionally run package verification commands."""
    rel = "project_memory/runtime/verification_dispatch_registry.json"
    brief_rel = "project_memory/runtime/verification_brief.json"
    commands_run: list[dict[str, Any]] = []
    cmd_results: list[dict[str, Any]] = []
    for cmd in package.get("verification_commands") or []:
        result = _run_command(list(cmd))
        commands_run.append(result)
        cmd_results.append({"command": result["command"], "passed": result["exit_code"] == 0})

    from loop_artifact_inputs import build_structured_verification_brief, persist_verification_brief_file

    brief = build_structured_verification_brief(package=package)
    brief["command_results"] = cmd_results
    # Persist the exact brief that includes command evidence.
    brief_path = ROOT / brief_rel
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(json.dumps(brief, indent=2) + "\n", encoding="utf-8")
    touched = [
        brief_rel,
    ]
    path = ROOT / rel
    existing: dict[str, Any] = {"entries": []}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {"entries": []}
    entry = {
        "work_id": package.get("work_id"),
        "cycle_id": package.get("cycle_id"),
        "brief_path": brief_rel,
        "commands_passed": all(r["passed"] for r in cmd_results) if cmd_results else True,
        "registered_at": _now_iso(),
    }
    entries = [e for e in existing.get("entries") or [] if e.get("work_id") != entry["work_id"]]
    entries.append(entry)
    touched.append(_write_json(rel, {"version": 1, "updated_at": _now_iso(), "entries": entries[-100:]}))

    all_cmds_pass = all(r["passed"] for r in cmd_results) if cmd_results else True
    return _base_result(
        handler="verification_dispatch",
        package=package,
        outcome_status="completed" if all_cmds_pass else "failed",
        actions_performed=[f"register verification brief for {package.get('work_id')}"],
        files_touched=touched,
        commands_run=commands_run,
        verifier_hints={
            "output_files": touched,
            "required_keys": ["entries"],
            "brief_path": brief_rel,
            "brief_work_id": package.get("work_id"),
            "commands_must_pass": bool(package.get("verification_commands")),
            "all_commands_passed": all_cmds_pass,
        },
        message="Verification dispatch registry updated",
    )


HANDLERS: dict[str, HandlerFn] = {
    "goal_ingestion": handle_goal_ingestion,
    "research_synthesis": handle_research_synthesis,
    "verification_dispatch": handle_verification_dispatch,
}

DISPATCHABLE_TYPES = frozenset({"code_implementation", "verification_hardening"})


def resolve_dispatch_target(package: dict[str, Any]) -> str:
    explicit = str(package.get("dispatch_target") or "").strip()
    if explicit:
        return explicit
    capability = str(package.get("capability") or "").strip()
    if capability in HANDLERS:
        return capability
    inputs = package.get("inputs_used") or {}
    area = str(inputs.get("research_capability_area") or "").strip()
    if area in HANDLERS:
        return area
    task_type = str(package.get("task_type") or "")
    if task_type == "verification_hardening":
        return "verification_dispatch"
    return ""


def dispatch_work_package(package: dict[str, Any]) -> dict[str, Any]:
    """Route work package to a bounded handler by dispatch_target / capability."""
    target = resolve_dispatch_target(package)
    task_type = str(package.get("task_type") or "")
    if not target:
        if task_type in DISPATCHABLE_TYPES:
            return _base_result(
                handler="",
                package=package,
                outcome_status="dispatch_handler_missing",
                message="No dispatch_target or capability mapping for executable work package",
                verifier_hints={"failure_reason": "dispatch_handler_missing"},
            )
        return _base_result(
            handler="",
            package=package,
            outcome_status="not_implemented",
            message="No dispatch routing required for this task type",
        )
    handler_fn = HANDLERS.get(target)
    if not handler_fn:
        return _base_result(
            handler=target,
            package=package,
            outcome_status="dispatch_handler_missing",
            message=f"No handler registered for dispatch_target={target!r}",
            verifier_hints={"failure_reason": "dispatch_handler_missing", "dispatch_target": target},
        )
    handler_inputs = dict(package.get("handler_inputs") or {})
    try:
        return handler_fn(package, handler_inputs)
    except Exception as exc:
        return _base_result(
            handler=target,
            package=package,
            outcome_status="failed",
            message=str(exc),
            verifier_hints={"failure_reason": str(exc)},
        )


def self_check() -> None:
    import tempfile
    state_paths = [RUNTIME / "goal_ingestion_index.json", RUNTIME / "goal_model.json"]
    snapshots = {p: p.read_bytes() for p in state_paths if p.is_file()}
    pkg = {
        "work_id": "self_check_goal",
        "cycle_id": 0,
        "task_type": "code_implementation",
        "capability": "goal_ingestion",
        "dispatch_target": "goal_ingestion",
        "objective": "self check goal ingestion",
        "inputs_used": {"goal_excerpt": "Product Goal test", "research_summary": "test"},
        "handler_inputs": {"capability_area": "goal_ingestion"},
        "expected_outputs": ["project_memory/runtime/goal_ingestion_index.json"],
    }
    try:
      result = dispatch_work_package(pkg)
    finally:
      for p in state_paths:
        if p in snapshots: p.write_bytes(snapshots[p])
        else: p.unlink(missing_ok=True)
    assert result["outcome_status"] == "completed", result
    assert result["handler"] == "goal_ingestion"
    assert result["files_touched"]

    pkg2 = {
        "work_id": "self_check_missing",
        "task_type": "code_implementation",
        "dispatch_target": "nonexistent_handler",
    }
    missing = dispatch_work_package(pkg2)
    assert missing["outcome_status"] == "dispatch_handler_missing"

    assert resolve_dispatch_target({"capability": "research_synthesis"}) == "research_synthesis"
    print("loop-dispatch: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo implementation dispatch")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    parser.error("specify --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
