#!/usr/bin/env python3
"""Execute one bounded plan step for purple_halo loop cycles. Stdlib only."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from loop_target_workspace import product_root  # noqa: E402
from loop_dispatch import DISPATCHABLE_TYPES, dispatch_work_package  # noqa: E402

PRODUCT_ROOT = product_root()
from loop_product_slices import apply_slice  # noqa: E402
from loop_worker_bridge import run_worker_bridge, should_use_worker_bridge  # noqa: E402

MARKER_SUFFIXES = ("_marker.txt", "_maintenance.txt", "self_check.txt")
DOC_ONLY_PREFIXES = ("project_learning/",)
DOC_ONLY_FILES = frozenset({"project_status.md"})


def _write_file(rel_path: str, content: str) -> str:
    path = PRODUCT_ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    return rel_path


def _append_file(rel_path: str, content: str) -> str:
    path = PRODUCT_ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    merged = existing.rstrip() + "\n" + content.lstrip("\n")
    path.write_text(merged if merged.endswith("\n") else merged + "\n", encoding="utf-8")
    return rel_path


def _ensure_section(rel_path: str, heading: str, content: str) -> str:
    path = PRODUCT_ROOT / rel_path
    text = path.read_text(encoding="utf-8") if path.is_file() else f"# {path.name}\n"
    block = f"{heading}\n\n{content.strip()}\n"
    if heading in text:
        head, _sep, _tail = text.partition(heading)
        text = head.rstrip() + "\n\n" + block
    else:
        text = text.rstrip() + "\n\n" + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return rel_path


def _run_command(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=PRODUCT_ROOT, capture_output=True, text=True)
    return {
        "command": cmd,
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _is_marker_only(paths: list[str]) -> bool:
    if not paths:
        return True
    # ponytail: economy live proof marker is the intended worker-bridge delta, not a noop touch
    if paths == ["project_memory/runtime/live_proof_marker.txt"]:
        return False
    return all(any(path.endswith(suffix) for suffix in MARKER_SUFFIXES) for path in paths)


def _is_doc_only(paths: list[str]) -> bool:
    if not paths:
        return True
    return all(path.startswith(DOC_ONLY_PREFIXES) or path in DOC_ONLY_FILES for path in paths)


def _is_product_code(paths: list[str]) -> bool:
    return any(path.startswith("scripts/") and path.endswith(".py") for path in paths)


def _actions_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefer work package execution_steps over bare backlog/plan actions."""
    pkg = plan.get("work_package") or {}
    bounded = plan.get("bounded_step") or {}
    return list(
        pkg.get("execution_steps")
        or plan.get("execution_steps")
        or bounded.get("execution_steps")
        or bounded.get("actions")
        or plan.get("actions")
        or []
    )


def _run_actions(actions: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    changed: list[str] = []
    command_results: list[dict[str, Any]] = []
    errors: list[str] = []
    for action in actions:
        kind = action.get("type")
        try:
            if kind == "write_file":
                changed.append(_write_file(str(action["path"]), str(action["content"])))
            elif kind == "append_file":
                changed.append(_append_file(str(action["path"]), str(action["content"])))
            elif kind == "ensure_section":
                changed.append(
                    _ensure_section(str(action["path"]), str(action["heading"]), str(action["content"]))
                )
            elif kind == "apply_code_slice":
                changed.extend(apply_slice(str(action["slice"])))
            elif kind == "run_command":
                result = _run_command(list(action["command"]))
                command_results.append(result)
                if result["exit_code"] != 0:
                    errors.append(f"run_command failed: {' '.join(result['command'])}")
            elif kind == "touch_marker":
                errors.append("touch_marker is not allowed for goal-driven milestones")
            else:
                errors.append(f"unknown action type: {kind!r}")
        except Exception as exc:
            errors.append(f"{kind} failed for {action.get('path') or action.get('slice')}: {exc}")
    return changed, command_results, errors


def _should_dispatch(plan: dict[str, Any], work_package: dict[str, Any]) -> bool:
    if work_package.get("force_worker_bridge") or plan.get("force_worker_bridge"):
        return False
    if work_package.get("local_only") or plan.get("local_only"):
        return False
    if should_use_worker_bridge(plan, work_package):
        return False
    task_type = str(plan.get("task_type") or "")
    if work_package.get("dispatch_target"):
        return True
    return task_type in DISPATCHABLE_TYPES and bool(work_package)


def _execution_from_worker(
    *,
    plan: dict[str, Any],
    work_package: dict[str, Any],
    worker_result: dict[str, Any],
) -> dict[str, Any]:
    task_type = str(plan.get("task_type") or "")
    backlog_work_id = plan.get("backlog_work_id") or plan.get("work_id")
    outcome = str(worker_result.get("outcome_class") or "")
    changed = list(worker_result.get("changed_files") or [])
    errors = list(worker_result.get("errors") or [])
    if outcome == "worker_unavailable":
        errors.append(f"worker_unavailable: {worker_result.get('summary', '')}")
        if backlog_work_id:
            from loop_backlog import load_backlog, mark_item_status

            mark_item_status(
                load_backlog(),
                str(backlog_work_id),
                "open",
                failure_reason="worker_unavailable",
                cycle_id=int(plan.get("cycle_id") or 0) or None,
            )
    elif outcome in {"execution_failed", "verification_failed"}:
        errors.append(worker_result.get("summary") or outcome)
        if backlog_work_id:
            from loop_backlog import load_backlog, mark_item_status

            mark_item_status(
                load_backlog(),
                str(backlog_work_id),
                "open",
                failure_reason=outcome,
                cycle_id=int(plan.get("cycle_id") or 0) or None,
            )

    meaningful = bool(changed) and not _is_marker_only(changed)
    ok = outcome in {"verified_complete", "verified_partial"} and not errors

    return {
        "plan_id": plan.get("plan_id"),
        "task_type": task_type,
        "backlog_work_id": backlog_work_id,
        "work_id": work_package.get("work_id") or backlog_work_id,
        "work_package_id": work_package.get("work_id"),
        "worker_routed": True,
        "worker_result": worker_result,
        "worker_outcome_class": outcome,
        "budget_gate_blocked": bool(worker_result.get("budget_gate_blocked")),
        "budget_decision_reason": worker_result.get("budget_decision_reason") or "",
        "dispatch_target": None,
        "dispatch_result": None,
        "dispatch_routed": False,
        "changed_files": changed,
        "command_results": worker_result.get("commands_run") or [],
        "meaningful_repo_delta": meaningful,
        "errors": errors,
        "ok": ok,
    }


def run_execute(plan: dict[str, Any]) -> dict[str, Any]:
    bounded = plan.get("bounded_step") or {}
    task_type = str(plan.get("task_type") or bounded.get("task_type") or "")
    backlog_work_id = plan.get("backlog_work_id") or plan.get("work_id")
    cycle_id = plan.get("cycle_id")
    work_package = plan.get("work_package") or {}
    local_only = bool(plan.get("local_only") or work_package.get("local_only") or str(backlog_work_id or "") in {"product_cycle_closure"} or str(backlog_work_id or "").startswith("product_gap_") or str(backlog_work_id or "").startswith("operational_") or str(backlog_work_id or "").startswith("deliver_") or str(backlog_work_id or "").startswith("improve_"))
    from loop_artifact_inputs import resolve_verification_contract

    _contract, verification_brief_meta = resolve_verification_contract(plan=plan, package=work_package)

    if backlog_work_id and cycle_id:
        from loop_backlog import mark_in_progress

        mark_in_progress(str(backlog_work_id), int(cycle_id))

    if local_only:
        changed, command_results, errors = _run_actions(_actions_from_plan(plan))
        meaningful = (not errors) and (
            any(int(r.get("exit_code", r.get("returncode", 1))) == 0 for r in command_results)
            or (
                not command_results
                and (
                    str(backlog_work_id or "").startswith("deliver_")
                    or plan.get("success_criterion_id")
                )
            )
        )
        return {
            "plan_id": plan.get("plan_id"),
            "task_type": task_type,
            "backlog_work_id": backlog_work_id,
            "work_id": work_package.get("work_id") or backlog_work_id,
            "work_package_id": work_package.get("work_id"),
            "dispatch_target": "",
            "dispatch_result": None,
            "dispatch_routed": False,
            "verification_brief_meta": verification_brief_meta,
            "verification_brief_used": verification_brief_meta.get("verification_brief_used", False),
            "local_only": True,
            "worker_routed": False,
            "worker_result": None,
            "worker_outcome_class": None,
            "changed_files": changed,
            "command_results": command_results,
            "meaningful_repo_delta": meaningful,
            "errors": errors,
            "ok": bool(meaningful and not errors),
        }

    if should_use_worker_bridge(plan, work_package) and not local_only:
        from loop_cost_policy import allow_task_execution, record_expensive_action
        from loop_state import cycle_artifact_dir

        allowed, gate_reason = allow_task_execution("code_implementation", plan=plan)
        if not allowed:
            blocked = {
                "work_id": work_package.get("work_id") or backlog_work_id,
                "outcome_class": "execution_failed",
                "changed_files": [],
                "commands_run": [],
                "verification_output": [],
                "repo_delta_summary": "",
                "summary": f"budget_gate_blocked: {gate_reason}",
                "trace_id": "",
                "errors": [f"budget_gate_blocked: {gate_reason}"],
                "budget_gate_blocked": True,
                "budget_decision_reason": gate_reason,
            }
            return _execution_from_worker(plan=plan, work_package=work_package, worker_result=blocked)
        wp_path = str((cycle_artifact_dir(int(cycle_id)) / "work_package.json").relative_to(PRODUCT_ROOT)) if cycle_id else ""
        worker_result = run_worker_bridge(
            plan=plan,
            work_package=work_package,
            cycle_id=int(cycle_id or 0),
            work_package_path=wp_path,
        )
        if not worker_result.get("budget_gate_blocked"):
            record_expensive_action("worker_session", cycle_id=int(cycle_id or 0), reason=gate_reason)
            record_expensive_action("code_implementation", cycle_id=int(cycle_id or 0), reason=gate_reason)
        return _execution_from_worker(plan=plan, work_package=work_package, worker_result=worker_result)

    dispatch_result: dict[str, Any] | None = None
    dispatch_routed = False
    changed: list[str] = []
    command_results: list[dict[str, Any]] = []
    errors: list[str] = []

    if _should_dispatch(plan, work_package):
        dispatch_result = dispatch_work_package(work_package)
        dispatch_routed = True
        outcome = str(dispatch_result.get("outcome_status") or "")
        if outcome == "dispatch_handler_missing":
            errors.append(f"dispatch_handler_missing: {dispatch_result.get('message', '')}")
            if backlog_work_id:
                from loop_backlog import load_backlog, mark_item_status

                mark_item_status(
                    load_backlog(),
                    str(backlog_work_id),
                    "open",
                    failure_reason="dispatch_handler_missing",
                    cycle_id=int(cycle_id) if cycle_id else None,
                )
        elif outcome == "not_implemented":
            if task_type == "code_implementation" and not local_only:
                errors.append("code_implementation cannot fall back to local actions without worker bridge")
            elif task_type == "code_implementation" and local_only:
                fallback_changed, fallback_cmds, fallback_errors = _run_actions(_actions_from_plan(plan))
                changed.extend(fallback_changed)
                command_results.extend(fallback_cmds)
                errors.extend(fallback_errors)
            else:
                fallback_changed, fallback_cmds, fallback_errors = _run_actions(_actions_from_plan(plan))
                changed.extend(fallback_changed)
                command_results.extend(fallback_cmds)
                errors.extend(fallback_errors)
        elif outcome in {"completed", "success"}:
            changed.extend(dispatch_result.get("files_touched") or [])
            command_results.extend(dispatch_result.get("commands_run") or [])
        else:
            errors.append(f"dispatch failed: {dispatch_result.get('message', outcome)}")
            changed.extend(dispatch_result.get("files_touched") or [])
            command_results.extend(dispatch_result.get("commands_run") or [])
    else:
        if task_type == "code_implementation" and not local_only:
            errors.append("code_implementation requires governed worker bridge or explicit dispatch routing")
        else:
            changed, command_results, errors = _run_actions(_actions_from_plan(plan))

    if task_type == "code_implementation" and not dispatch_routed and not local_only:
        if not _is_product_code(changed):
            errors.append("code_implementation requires product code file changes under scripts/")
        if _is_doc_only(changed):
            errors.append("code_implementation cannot use doc-only changes")

    if local_only:
        meaningful = (not errors) and any(int(r.get("exit_code", r.get("returncode", 1))) == 0 for r in command_results)
    else:
        meaningful = bool(changed) and not _is_marker_only(changed)
        if task_type == "code_implementation" and not dispatch_routed:
            meaningful = meaningful and _is_product_code(changed)
    if (not local_only) and dispatch_routed and dispatch_result and dispatch_result.get("outcome_status") == "completed":
        meaningful = meaningful and bool(changed)

    if local_only:
        ok = not errors and meaningful
    else:
        ok = not errors and meaningful and not (
            dispatch_result and dispatch_result.get("outcome_status") == "dispatch_handler_missing"
        )

    return {
        "plan_id": plan.get("plan_id"),
        "task_type": task_type,
        "backlog_work_id": backlog_work_id,
        "work_id": work_package.get("work_id") or backlog_work_id,
        "work_package_id": work_package.get("work_id"),
        "dispatch_target": (dispatch_result or {}).get("dispatch_target") or work_package.get("dispatch_target"),
        "dispatch_result": dispatch_result,
        "dispatch_routed": dispatch_routed,
        "verification_brief_meta": verification_brief_meta,
        "verification_brief_used": verification_brief_meta.get("verification_brief_used", False),
        "local_only": local_only,
        "worker_routed": False,
        "worker_result": None,
        "worker_outcome_class": None,
        "changed_files": changed,
        "command_results": command_results,
        "meaningful_repo_delta": meaningful,
        "errors": errors,
        "ok": ok,
    }


def self_check() -> None:
    plan = {
        "plan_id": "self_check_dispatch",
        "cycle_id": 0,
        "task_type": "verification_hardening",
        "work_package": {
            "work_id": "exec_dispatch_test",
            "cycle_id": 0,
            "task_type": "verification_hardening",
            "dispatch_target": "research_synthesis",
            "capability": "research_synthesis",
            "objective": "dispatch test",
            "inputs_used": {"goal_excerpt": "test", "research_summary": "test"},
            "handler_inputs": {},
            "expected_outputs": ["project_memory/runtime/research_synthesis_log.json"],
            "execution_steps": [],
        },
    }
    dispatch_result = run_execute(plan)
    assert dispatch_result.get("dispatch_routed")

    worker_plan = {
        "plan_id": "self_check_worker",
        "cycle_id": 0,
        "budget_bypass": True,
        "task_type": "code_implementation",
        "work_package": {
            "work_id": "worker_exec_test",
            "cycle_id": 0,
            "task_type": "code_implementation",
            "objective": "worker exec test",
            "target_files": ["scripts/loop_worker_proof.py"],
            "proposed_repo_delta": ["scripts/loop_worker_proof.py"],
            "execution_steps": [],
            "verification_commands": [],
            "goal_inputs": {},
            "research_inputs": {},
            "verification_basis": {},
        },
    }
    worker_exec = run_execute(worker_plan)
    assert worker_exec.get("worker_routed")
    assert worker_exec.get("worker_outcome_class")
    print("loop-execute: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo loop executor")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    parser.error("use purple_halo_loop.py run or --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
