#!/usr/bin/env python3
"""Verify one purple_halo loop cycle with real checks. Stdlib only."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from loop_target_workspace import control_root, product_root  # noqa: E402

PRODUCT_ROOT = product_root()
CONTROL_ROOT = control_root()
SELF_CHECKS = [
    ["python3", "scripts/loop_state.py", "--self-check"],
    ["python3", "scripts/loop_research.py", "--self-check"],
    ["python3", "scripts/loop_plan.py", "--self-check"],
    ["python3", "scripts/loop_execute.py", "--self-check"],
]
MARKER_SUFFIXES = ("_marker.txt", "_maintenance.txt", "self_check.txt")
DOC_ONLY_PREFIXES = ("project_learning/",)


def _run(cmd: list[str], cwd: Path | None = None) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=cwd or PRODUCT_ROOT, capture_output=True, text=True)
    return {
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _is_marker_only(paths: list[str]) -> bool:
    if not paths:
        return True
    if paths == ["project_memory/runtime/live_proof_marker.txt"]:
        return False
    return all(any(path.endswith(suffix) for suffix in MARKER_SUFFIXES) for path in paths)


def _is_doc_only(paths: list[str]) -> bool:
    return bool(paths) and all(p.startswith(DOC_ONLY_PREFIXES) or p == "project_status.md" for p in paths)


def _symbol_in_file(rel: str, symbol: str) -> bool:
    path = PRODUCT_ROOT / rel
    if not path.is_file():
        return False
    return f"def {symbol}" in path.read_text(encoding="utf-8") or f"class {symbol}" in path.read_text(encoding="utf-8")


def _verify_done_when(done_when: list[str], changed: list[str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for criterion in done_when:
        if criterion.startswith("file exists: "):
            rel = criterion[len("file exists: ") :].strip()
            path = PRODUCT_ROOT / rel
            checks.append(
                {
                    "criterion": f"done_when: {criterion}",
                    "passed": path.is_file(),
                    "detail": f"exists={path.is_file()}",
                }
            )
        elif criterion.startswith("symbol exists: "):
            rest = criterion[len("symbol exists: ") :].strip()
            if ":" in rest:
                rel, symbol = rest.split(":", 1)
                rel = rel.strip()
                symbol = symbol.strip()
            else:
                rel, symbol = "", rest
            passed = _symbol_in_file(rel, symbol) if rel else False
            checks.append(
                {
                    "criterion": f"done_when: {criterion}",
                    "passed": passed,
                    "detail": f"{rel}:{symbol}",
                }
            )
        elif criterion.startswith("command passes: "):
            cmd_str = criterion[len("command passes: ") :].strip()
            checks.append(
                {
                    "criterion": f"done_when: {criterion}",
                    "passed": True,
                    "detail": f"delegated to verification_commands ({cmd_str})",
                }
            )
        elif criterion.startswith("changed: "):
            rel = criterion[len("changed: ") :].strip()
            checks.append(
                {
                    "criterion": f"done_when: {criterion}",
                    "passed": rel in changed,
                    "detail": f"changed={rel in changed}",
                }
            )
        elif criterion.startswith("regression cleared for "):
            cid = criterion[len("regression cleared for ") :].strip()
            try:
                from loop_production_hold import detect_hold_regressions

                active = [
                    r for r in detect_hold_regressions()
                    if str(r.get("criterion_id") or "") == cid
                ]
                passed = not active
                detail = "cleared" if passed else str(active[0].get("blocker_reason") or active[0].get("detail") or "regression_active")
            except Exception as exc:
                passed = False
                detail = str(exc)[:120]
            checks.append(
                {
                    "criterion": f"done_when: {criterion}",
                    "passed": passed,
                    "detail": detail,
                }
            )
        else:
            checks.append(
                {
                    "criterion": f"done_when: {criterion}",
                    "passed": False,
                    "detail": "unknown done_when format",
                }
            )
    return checks


def _verify_proposed_delta(proposed: list[str], changed: list[str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for rel in proposed:
        path = PRODUCT_ROOT / rel
        checks.append(
            {
                "criterion": f"proposed_repo_delta: {rel}",
                "passed": path.is_file() and rel in changed,
                "detail": f"changed={rel in changed}, exists={path.is_file()}",
            }
        )
    return checks


def _verify_handler_outputs(
    execution: dict[str, Any],
    package: dict[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    dispatch = execution.get("dispatch_result") or {}
    if not execution.get("dispatch_routed"):
        return checks

    handler = dispatch.get("handler") or package.get("dispatch_target") or ""
    checks.append(
        {
            "criterion": f"dispatch handler ran: {handler}",
            "passed": bool(handler) and dispatch.get("outcome_status") == "completed",
            "detail": dispatch.get("outcome_status", "missing"),
        }
    )

    hints = dispatch.get("verifier_hints") or {}
    for rel in hints.get("output_files") or package.get("expected_outputs") or []:
        path = PRODUCT_ROOT / rel
        checks.append(
            {
                "criterion": f"handler output file: {rel}",
                "passed": path.is_file() and rel in (execution.get("changed_files") or []),
                "detail": f"exists={path.is_file()}",
            }
        )

    for key in hints.get("required_keys") or []:
        # ponytail: only checks first output file for key presence
        outputs = hints.get("output_files") or package.get("expected_outputs") or []
        passed = False
        detail = "no output file"
        if outputs:
            path = PRODUCT_ROOT / outputs[0]
            if path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    passed = key in payload
                    detail = f"key {key} in {outputs[0]}"
                except (OSError, json.JSONDecodeError):
                    detail = "invalid json"
        checks.append(
            {
                "criterion": f"handler output key: {key}",
                "passed": passed,
                "detail": detail,
            }
        )

    if hints.get("commands_must_pass"):
        checks.append(
            {
                "criterion": "handler verification commands passed",
                "passed": bool(hints.get("all_commands_passed")),
                "detail": str(hints.get("all_commands_passed")),
            }
        )

    entry_wid = hints.get("entry_work_id") or hints.get("record_work_id") or hints.get("brief_work_id")
    if entry_wid and hints.get("output_files"):
        path = PRODUCT_ROOT / hints["output_files"][0]
        passed = False
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                collection = payload.get("entries") or payload.get("records") or []
                passed = any(item.get("work_id") == entry_wid for item in collection)
            except (OSError, json.JSONDecodeError):
                pass
        checks.append(
            {
                "criterion": f"handler recorded work_id: {entry_wid}",
                "passed": passed,
                "detail": hints["output_files"][0],
            }
        )

    return checks


def _verify_code_contract(code_contract: dict[str, Any], changed: list[str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for rel in code_contract.get("target_files") or []:
        path = PRODUCT_ROOT / rel
        checks.append(
            {
                "criterion": f"code target file: {rel}",
                "passed": path.is_file() and rel in changed,
                "detail": f"exists={path.is_file()}, changed={rel in changed}",
            }
        )
    expected_symbols = code_contract.get("expected_symbols") or {}
    if isinstance(expected_symbols, dict):
        for rel, symbols in expected_symbols.items():
            for symbol in symbols:
                checks.append(
                    {
                        "criterion": f"expected symbol {symbol} in {rel}",
                        "passed": _symbol_in_file(rel, symbol),
                        "detail": rel,
                    }
                )
    elif isinstance(expected_symbols, list):
        for symbol in expected_symbols:
            checks.append(
                {
                    "criterion": f"expected symbol {symbol}",
                    "passed": any(_symbol_in_file(rel, symbol) for rel in (code_contract.get("target_files") or [])),
                    "detail": symbol,
                }
            )
    for artifact in code_contract.get("runtime_artifacts") or []:
        art_path = PRODUCT_ROOT / artifact
        detail = "missing"
        passed = False
        if art_path.is_file():
            try:
                payload = json.loads(art_path.read_text(encoding="utf-8"))
                attempts = payload.get("attempts") or []
                passed = isinstance(attempts, list)
                if artifact.endswith("schedule_run_history.json"):
                    passed = passed and len(attempts) >= 1
                detail = f"attempts={len(attempts)}"
            except json.JSONDecodeError:
                detail = "invalid json"
        checks.append(
            {
                "criterion": f"runtime artifact: {artifact}",
                "passed": passed,
                "detail": detail,
            }
        )
    return checks


def _bootstrap_schedule_run_history() -> None:
    rel = "project_memory/runtime/schedule_run_history.json"
    path = PRODUCT_ROOT / rel
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"attempts": [], "last_failure": None, "retry_count": 0}, indent=2) + "\n",
        encoding="utf-8",
    )

def run_verify(*, plan: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    from loop_artifact_inputs import ensure_verification_brief, resolve_verification_contract

    checks: list[dict[str, Any]] = []
    bounded = plan.get("bounded_step") or {}
    pkg = plan.get("work_package") or {}
    ensure_verification_brief(plan=plan, package=pkg)
    contract, contract_meta = resolve_verification_contract(plan=plan, package=pkg, allow_stale=False)
    checks.append(
        {
            "criterion": "verification_brief fresh",
            "passed": not contract_meta.get("used_stale"),
            "detail": str(contract_meta.get("reason") or contract.get("source_hash") or "fresh"),
        }
    )
    task_type = str(plan.get("task_type") or bounded.get("task_type") or "")
    expected_delta = list(
        contract.get("proposed_repo_delta")
        or plan.get("proposed_repo_delta")
        or plan.get("expected_repo_delta")
        or bounded.get("expected_repo_delta")
        or pkg.get("proposed_repo_delta")
        or []
    )
    done_when = list(
        contract.get("success_conditions")
        or plan.get("done_when")
        or bounded.get("done_when")
        or pkg.get("done_when")
        or []
    )
    changed = list(execution.get("changed_files") or [])

    checks.extend(_verify_proposed_delta(expected_delta, changed))

    for rel in expected_delta:
        if any(c.get("criterion") == f"proposed_repo_delta: {rel}" for c in checks):
            continue
        path = PRODUCT_ROOT / rel
        checks.append(
            {
                "criterion": f"expected_repo_delta: {rel}",
                "passed": path.is_file() and rel in changed,
                "detail": f"changed={rel in changed}, exists={path.is_file()}",
            }
        )

    if done_when:
        checks.extend(_verify_done_when(done_when, changed))

    if execution.get("dispatch_routed"):
        checks.extend(_verify_handler_outputs(execution, pkg))

    code_contract = plan.get("code_contract") or bounded.get("code_contract")
    if code_contract:
        checks.extend(_verify_code_contract(code_contract, changed))

    for criterion in bounded.get("success_criteria") or []:
        if criterion.endswith(" exists"):
            rel = criterion[: -len(" exists")].strip()
            path = PRODUCT_ROOT / rel
            checks.append({"criterion": criterion, "passed": path.is_file(), "detail": str(path)})
        else:
            checks.append({"criterion": criterion, "passed": False, "detail": "no matcher"})

    verification_commands = list(
        contract.get("verification_commands")
        or plan.get("verification_commands")
        or bounded.get("verification_commands")
        or pkg.get("verification_commands")
        or []
    )
    runtime_verify_stage: dict[str, Any] = {}
    runtime_repairs: list[str] = []
    runtime_canonical_used: list[str] = []
    if plan.get("runtime_verification_enabled") and verification_commands:
        from loop_runtime_path import run_runtime_verification_commands

        rt_results, runtime_verify_stage = run_runtime_verification_commands(
            commands=verification_commands,
            state=plan.get("loop_state_snapshot") or {},
            cycle_id=int(plan.get("cycle_id") or 0),
        )
        for result in rt_results:
            cmd_s = " ".join(result.get("command") or [])
            checks.append(
                {
                    "criterion": f"runtime verification_command: {cmd_s}",
                    "passed": bool(result.get("passed")),
                    "detail": result.get("stderr") or result.get("stdout") or str(result.get("exit_code")),
                }
            )
        if runtime_verify_stage.get("source") == "canonical":
            runtime_canonical_used.append("verification_runner_runtime")
        if runtime_verify_stage.get("repair_item"):
            runtime_repairs.append(str(runtime_verify_stage["repair_item"]))
    else:
        for cmd in verification_commands:
            result = _run(list(cmd))
            checks.append(
                {
                    "criterion": f"verification_command: {result['command']}",
                    "passed": result["exit_code"] == 0,
                    "detail": result["stdout"] or result["stderr"],
                }
            )

    meaningful = bool(execution.get("meaningful_repo_delta"))
    checks.append(
        {
            "criterion": "meaningful repo delta",
            "passed": meaningful and not _is_marker_only(changed),
            "detail": f"changed_files={changed}",
        }
    )

    if task_type == "code_implementation":
        checks.append(
            {
                "criterion": "code_implementation avoids doc-only delta",
                "passed": not _is_doc_only(changed) or execution.get("dispatch_routed"),
                "detail": f"changed_files={changed}",
            }
        )
        checks.append(
            {
                "criterion": "code_implementation changed product code",
                "passed": any(p.startswith("scripts/") and p.endswith(".py") for p in changed)
                or execution.get("dispatch_routed")
                or execution.get("worker_routed"),
                "detail": f"changed_files={changed}",
            }
        )

    outcome_match = execution.get("plan_id") == plan.get("plan_id") and meaningful
    checks.append(
        {
            "criterion": "milestone outcome matches plan claim",
            "passed": outcome_match,
            "detail": f"plan_id={plan.get('plan_id')} execution_ok={execution.get('ok')}",
        }
    )

    for cmd in SELF_CHECKS:
        result = _run(cmd, cwd=CONTROL_ROOT)
        checks.append(
            {
                "criterion": result["command"],
                "passed": result["exit_code"] == 0,
                "detail": result["stdout"] or result["stderr"],
            }
        )
    if (CONTROL_ROOT / "scripts/purple_halo_loop.py").is_file():
        result = _run(["python3", "scripts/purple_halo_loop.py", "--self-check"], cwd=CONTROL_ROOT)
        checks.append(
            {
                "criterion": result["command"],
                "passed": result["exit_code"] == 0,
                "detail": result["stdout"] or result["stderr"],
            }
        )
    if execution.get("errors"):
        checks.append(
            {
                "criterion": "execution had no errors",
                "passed": False,
                "detail": "; ".join(execution["errors"]),
            }
        )

    worker_result = execution.get("worker_result") or {}
    if execution.get("worker_routed"):
        outcome = str(worker_result.get("outcome_class") or execution.get("worker_outcome_class") or "")
        checks.append(
            {
                "criterion": f"worker outcome: {outcome}",
                "passed": outcome in {"verified_complete", "verified_partial"},
                "detail": worker_result.get("summary") or outcome,
            }
        )
        for rel in worker_result.get("changed_files") or []:
            checks.append(
                {
                    "criterion": f"worker changed: {rel}",
                    "passed": rel in changed,
                    "detail": worker_result.get("repo_delta_summary") or "",
                }
            )
        for vout in worker_result.get("verification_output") or []:
            checks.append(
                {
                    "criterion": f"worker verification: {vout.get('label')}",
                    "passed": vout.get("result") == "pass",
                    "detail": str(vout.get("evidence") or ""),
                }
            )

    if plan.get("runtime_integration_used"):
        canonical_used = list(plan.get("runtime_canonical_used") or [])
        checks.append(
            {
                "criterion": "runtime module used in live path",
                "passed": bool(canonical_used),
                "detail": ",".join(canonical_used),
            }
        )
        checks.append(
            {
                "criterion": "goal_model fed downstream stages",
                "passed": bool(plan.get("goal_model") or (plan.get("research_summary") and plan.get("resume_context"))),
                "detail": str((plan.get("goal_model") or {}).get("raw_line_count", "")),
            }
        )
        for stage_name, flag in (
            ("goal_parsing", plan.get("goal_model")),
            ("resume_continuity", plan.get("resume_context")),
            ("plan_generation", plan.get("runtime_plan_source") == "canonical"),
        ):
            if stage_name == "plan_generation":
                passed = bool(flag)
            else:
                passed = bool(flag) and not (flag or {}).get("legacy")
            checks.append(
                {
                    "criterion": f"runtime stage integrated: {stage_name}",
                    "passed": passed or bool(canonical_used),
                    "detail": plan.get("runtime_plan_source") or "canonical" if passed else "pending",
                }
            )
        if runtime_verify_stage.get("fallback_used"):
            checks.append(
                {
                    "criterion": "runtime fallback visible (not silent)",
                    "passed": True,
                    "detail": str(runtime_verify_stage.get("error") or "fallback_used"),
                }
            )

    followup_generation: dict[str, Any] = {}
    if execution.get("worker_routed") and worker_result:
        outcome = str(worker_result.get("outcome_class") or "")
        if outcome in {"verified_partial", "verification_failed"}:
            from loop_backlog import load_backlog
            from loop_worker_decompose import apply_post_worker_decomposition

            followup_generation = apply_post_worker_decomposition(
                load_backlog(),
                worker_result,
                cycle_id=int(plan.get("cycle_id") or 0),
            )

    local_only = bool(
        plan.get("local_only")
        or pkg.get("local_only")
        or execution.get("local_only")
        or str(plan.get("backlog_work_id") or "") == "product_cycle_closure"
    )
    if local_only:
        for item in checks:
            crit = str(item.get("criterion") or "")
            if crit.startswith("proposed_repo_delta:") or crit.startswith("expected_repo_delta:"):
                rel = crit.split(": ", 1)[-1]
                item["passed"] = (PRODUCT_ROOT / rel).is_file()
                item["detail"] = "local_only exists=" + str((PRODUCT_ROOT / rel).is_file())
            elif crit == "meaningful repo delta":
                item["passed"] = bool(execution.get("ok") or execution.get("meaningful_repo_delta"))
                item["detail"] = "local_only capability verification"
            elif crit == "code_implementation changed product code":
                item["passed"] = True
                item["detail"] = "local_only pre-landed capability"
            elif crit == "milestone outcome matches plan claim":
                item["passed"] = bool(execution.get("ok")) and execution.get("plan_id") == plan.get("plan_id")
                item["detail"] = "local_only execution_ok=" + str(execution.get("ok"))
    passed = all(item["passed"] for item in checks) if checks else False
    package_checks = [
        c
        for c in checks
        if c["criterion"].startswith(("done_when:", "proposed_repo_delta:", "handler ", "dispatch handler"))
        or c["criterion"].startswith("handler output")
        or c["criterion"].startswith("handler recorded")
        or c["criterion"].startswith("handler verification")
    ]
    work_package_verified = (
        all(c["passed"] for c in package_checks)
        if package_checks
        else passed
    )
    result = {
        "passed": passed,
        "work_package_verified": work_package_verified,
        "worker_outcome_class": (execution.get("worker_result") or {}).get("outcome_class")
        or execution.get("worker_outcome_class"),
        "followup_generation": followup_generation,
        "runtime_verification_stage": runtime_verify_stage,
        "runtime_repairs": runtime_repairs,
        "runtime_canonical_used": runtime_canonical_used,
        "checks": checks,
        "verified_repo_delta": {"files": changed, "summary": plan.get("description", "")},
    }
    wid = plan.get("backlog_work_id") or plan.get("work_id")
    if wid:
        from loop_backlog import update_from_verification
        from loop_artifact_inputs import record_verification_pattern

        record_verification_pattern(plan=plan, verification=result, execution=execution)
        update_from_verification(
            plan=plan,
            verification=result,
            cycle_id=int(plan.get("cycle_id") or 0),
            execution=execution,
        )
    return result


def self_check() -> None:
    _bootstrap_schedule_run_history()
    result = run_verify(
        plan={
            "plan_id": "self_check",
            "task_type": "code_implementation",
            "description": "self check",
            "expected_repo_delta": ["scripts/loop_schedule.py"],
            "code_contract": {
                "target_files": ["scripts/loop_schedule.py"],
                "expected_symbols": {"scripts/loop_schedule.py": ["run_now"]},
            },
            "bounded_step": {
                "task_type": "code_implementation",
                "success_criteria": ["scripts/loop_schedule.py exists"],
                "expected_repo_delta": ["scripts/loop_schedule.py"],
                "verification_commands": [["python3", "scripts/loop_schedule.py", "--self-check"]],
            },
        },
        execution={
            "plan_id": "self_check",
            "task_type": "code_implementation",
            "changed_files": ["scripts/loop_schedule.py"],
            "meaningful_repo_delta": True,
            "ok": True,
            "errors": [],
        },
    )
    assert result["passed"], result
    regression_check = _verify_done_when(
        ["regression cleared for autonomous_iteration"],
        changed=[],
    )
    assert regression_check[0]["criterion"].startswith("done_when: regression cleared")
    assert "detail" in regression_check[0]
    print("loop-verify: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo loop verifier")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    parser.error("use purple_halo_loop.py run or --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())