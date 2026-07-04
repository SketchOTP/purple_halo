#!/usr/bin/env python3
"""Governed implementation worker bridge for purple_halo loop cycles. Stdlib only."""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from loop_target_workspace import active_contract, control_root, product_root  # noqa: E402

CONTROL_ROOT = control_root()
PRODUCT_ROOT = product_root()
OUTCOME_CLASSES = frozenset({"verified_complete", "verified_partial", "verification_failed", "execution_failed", "worker_unavailable"})
WORKER_EXEMPT_WORK_IDS = frozenset({"product_dispatch_goal_index"})
WORKER_HEALTH_CACHE_PATH = CONTROL_ROOT / "project_memory/runtime/worker_health_cache.json"

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_worker_health_cache() -> dict[str, Any]:
    if not WORKER_HEALTH_CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(WORKER_HEALTH_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_worker_health_cache(payload: dict[str, Any]) -> None:
    WORKER_HEALTH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WORKER_HEALTH_CACHE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def invalidate_worker_health_cache() -> None:
    if WORKER_HEALTH_CACHE_PATH.is_file():
        WORKER_HEALTH_CACHE_PATH.unlink(missing_ok=True)


def _cheap_worker_probe() -> tuple[bool, str]:
    session_path = CONTROL_ROOT / "scripts/cursor_session.py"
    if not session_path.is_file():
        return False, "cursor_session.py missing"
    # ponytail: existence-only probe; upgrade path = lightweight import/ping without governed self-check
    return True, ""


def _expensive_worker_probe() -> tuple[bool, str]:
    session_path = CONTROL_ROOT / "scripts/cursor_session.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(session_path), "--self-check"],
            cwd=CONTROL_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or "cursor_session self-check failed").strip()[:200]
        return True, ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def worker_available(*, force_refresh: bool = False, expensive_check: bool = False) -> tuple[bool, str]:
    from loop_cost_policy import load_policy

    policy = load_policy()
    ttl = int(policy.get("worker_health_ttl_seconds") or 3600)
    cache = _load_worker_health_cache()
    if not force_refresh and cache.get("checked_at"):
        try:
            checked = datetime.fromisoformat(str(cache["checked_at"]).replace("Z", "+00:00"))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - checked).total_seconds()
            if age < ttl:
                return bool(cache.get("available")), str(cache.get("reason") or "")
        except ValueError:
            pass
    probe = "expensive" if expensive_check else "cheap"
    ok, reason = _expensive_worker_probe() if expensive_check else _cheap_worker_probe()
    _save_worker_health_cache(
        {"available": ok, "reason": reason, "checked_at": _now_iso(), "probe": probe}
    )
    return ok, reason
def should_use_worker_bridge(plan: dict[str, Any], work_package: dict[str, Any]) -> bool:
    if work_package.get("local_only") or plan.get("local_only"):
        return False
    wid0 = str(work_package.get("work_id") or plan.get("backlog_work_id") or "")
    if wid0 == "product_cycle_closure" or wid0.startswith("product_gap_") or wid0.startswith("operational_"):
        return False
    if work_package.get("force_worker_bridge") or plan.get("force_worker_bridge"):
        return True
    task_type = str(plan.get("task_type") or work_package.get("task_type") or "")
    if task_type != "code_implementation":
        return False
    wid = str(work_package.get("work_id") or plan.get("backlog_work_id") or "")
    if wid in WORKER_EXEMPT_WORK_IDS:
        return False
    dispatch_target = str(work_package.get("dispatch_target") or "")
    targets = list(work_package.get("target_files") or work_package.get("proposed_repo_delta") or [])
    if dispatch_target and targets and all(t.startswith("project_memory/") for t in targets):
        return False
    return True

def build_worker_contract(work_package, *, work_package_path, resume_context=None):
    goal_inputs = work_package.get("goal_inputs") or {}
    constraints = list(goal_inputs.get("constraints") or [])[:6]
    contract = {
        "work_id": work_package.get("work_id"),
        "work_package_path": work_package_path,
        "objective": work_package.get("objective") or work_package.get("work_id"),
        "constraints": constraints,
        "target_files": list(work_package.get("target_files") or []),
        "verification_commands": [list(c) for c in work_package.get("verification_commands") or []],
        "expected_outputs": list(work_package.get("expected_outputs") or work_package.get("proposed_repo_delta") or []),
        "resume_context": resume_context or {
            "cycle_id": work_package.get("cycle_id"),
            "selection_rationale": work_package.get("selection_rationale") or "",
            "goal_inputs": goal_inputs,
            "research_inputs": work_package.get("research_inputs") or {},
            "verification_basis": work_package.get("verification_basis") or {},
        },
        "execution_steps": list(work_package.get("execution_steps") or []),
        "created_at": _now_iso(),
    }
    ac = active_contract()
    if ac:
        contract["target_repo_path"] = ac["target_repo_path"]
        contract["target_repo_slug"] = ac["target_repo_slug"]
    return contract
def _classify_outcome(*, impl_errors, changed_files, verification_output, expected_outputs):
    if impl_errors:
        return "execution_failed"
    verify_pass = [v for v in verification_output if v.get("result") == "pass"]
    verify_fail = [v for v in verification_output if v.get("result") == "fail"]
    outputs_met = all((PRODUCT_ROOT / rel).is_file() or rel in changed_files for rel in expected_outputs) if expected_outputs else bool(changed_files)
    if verification_output and not verify_fail and outputs_met and changed_files:
        return "verified_complete"
    if verification_output and verify_pass and changed_files:
        return "verified_partial"
    if changed_files and verify_fail:
        return "verification_failed"
    if not changed_files:
        return "execution_failed"
    return "verification_failed"
def _finalize_worker_result(result, contract):
    from loop_worker_decompose import enrich_worker_result
    return enrich_worker_result(result, expected_outputs=list(contract.get("expected_outputs") or []), target_files=list(contract.get("target_files") or []))
def _persist_worker_artifact(cycle_id, name, payload):
    from loop_state import cycle_artifact_dir
    path = cycle_artifact_dir(cycle_id) / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path

def _run_local_step(step):
    from loop_product_slices import apply_slice
    kind = step.get("type")
    try:
        if kind == "write_file":
            path = PRODUCT_ROOT / str(step["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            content = str(step["content"])
            path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
            return [str(step["path"])], None, None
        if kind == "apply_code_slice":
            return apply_slice(str(step["slice"])), None, None
        if kind == "run_command":
            proc = subprocess.run(list(step["command"]), cwd=PRODUCT_ROOT, capture_output=True, text=True)
            result = {"command": list(step["command"]), "exit_code": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
            err = None if proc.returncode == 0 else "command failed: " + " ".join(result["command"])
            return [], result, err
        return [], None, f"unknown step type: {kind!r}"
    except Exception as exc:
        return [], None, str(exc)

def run_worker_bridge(*, plan, work_package, cycle_id, work_package_path, dry_run=False):
    if os.environ.get("PURPLE_HALO_WORKER_DRY_RUN") == "1":
        dry_run = True
    contract = build_worker_contract(work_package, work_package_path=work_package_path, resume_context=plan.get("resume_context") or {})
    _persist_worker_artifact(cycle_id, "worker_contract.json", contract)
    ok, reason = worker_available()
    if not ok and not dry_run:
        result = _finalize_worker_result({"work_id": contract["work_id"], "outcome_class": "worker_unavailable", "changed_files": [], "commands_run": [], "verification_output": [], "repo_delta_summary": "", "summary": reason or "worker unavailable", "trace_id": "", "errors": [reason or "worker unavailable"]}, contract)
        _persist_worker_artifact(cycle_id, "worker_result.json", result)
        return result
    impl_errors, commands_run, changed_files, verification_output, session_trace_id = [], [], [], [], ""
    if dry_run:
        changed_files = list(contract.get("target_files") or [])
        verification_output = [{"label": "dry-run", "command": "dry-run", "result": "pass", "evidence": "worker dry run"}]
        outcome = "verified_complete" if changed_files else "execution_failed"
        result = _finalize_worker_result({"work_id": contract["work_id"], "outcome_class": outcome, "changed_files": changed_files, "commands_run": commands_run, "verification_output": verification_output, "repo_delta_summary": f"dry-run touched {len(changed_files)} target(s)", "summary": "worker dry run", "trace_id": ""}, contract)
        _persist_worker_artifact(cycle_id, "worker_result.json", result)
        return result

    old_mimir = os.environ.pop("MIMIR_ENDPOINT", None)
    try:
        from cursor_session import _clear_active_session, _default_metadata, _execute_with_tracking, _load_or_start_session, _runtime_shell_command, _verification_result_from_command
        project_slug = str(contract.get("target_repo_slug") or "purple_halo")
        orchestrator = _load_or_start_session(task=f"purple_halo worker: {contract['objective']}", route="bounded", project=project_slug, fail_closed_navigation=False, new_session=True)
        session_trace_id = str(orchestrator.trace_id or "")
        metadata = _default_metadata()
        for step in contract.get("execution_steps") or []:
            if step.get("type") == "run_command":
                cmd = list(step["command"])
                cmd_text = _runtime_shell_command(" ".join(cmd))
                tracked = _execute_with_tracking(orchestrator=orchestrator, metadata=metadata, tool_name="shell.worker", command=cmd_text, action_class="worker_run_command", target_paths=list(contract.get("target_files") or []))
                commands_run.append({"command": cmd, "exit_code": tracked.get("exit_code", 1), "ok": bool(tracked.get("ok"))})
                if not tracked.get("ok"):
                    impl_errors.append("run_command failed: " + " ".join(cmd))
            else:
                local_changed, cmd_result, err = _run_local_step(step)
                for rel in local_changed:
                    if rel not in changed_files:
                        changed_files.append(rel)
                if cmd_result:
                    commands_run.append(cmd_result)
                if err:
                    impl_errors.append(err)
        for rel in metadata.get("changed_files") or []:
            if rel not in changed_files:
                changed_files.append(rel)

        for cmd in contract.get("verification_commands") or []:
            cmd_text = _runtime_shell_command(" ".join(cmd))
            tracked = _execute_with_tracking(orchestrator=orchestrator, metadata=metadata, tool_name="shell.verify", command=cmd_text, action_class="worker_verify", target_paths=list(changed_files or contract.get("target_files") or []))
            commands_run.append({"command": cmd, "exit_code": tracked.get("exit_code", 1), "ok": bool(tracked.get("ok"))})
            verification_output.append(_verification_result_from_command(" ".join(cmd), tracked, blocked_reason=str(tracked.get("error") or "") if tracked.get("blocked") else None))
        for rel in metadata.get("changed_files") or []:
            if rel not in changed_files:
                changed_files.append(rel)
        outcome = _classify_outcome(impl_errors=impl_errors, changed_files=changed_files, verification_output=verification_output, expected_outputs=list(contract.get("expected_outputs") or []))
        orchestrator.session_end(final_status="complete" if outcome.startswith("verified") else "failed")
        _clear_active_session()
    except Exception as exc:
        outcome = "worker_unavailable"
        impl_errors.append(str(exc))
    finally:
        if old_mimir is not None:
            os.environ["MIMIR_ENDPOINT"] = old_mimir
    summary = "; ".join(impl_errors) if impl_errors else f"worker outcome={outcome}"
    delta = ", ".join(changed_files[:8])
    result = _finalize_worker_result({"work_id": contract["work_id"], "outcome_class": outcome, "changed_files": changed_files, "commands_run": commands_run, "verification_output": verification_output, "repo_delta_summary": f"changed {len(changed_files)} file(s): {delta}", "summary": summary, "trace_id": session_trace_id, "errors": impl_errors, "verification_commands": contract.get("verification_commands") or []}, contract)
    _persist_worker_artifact(cycle_id, "worker_result.json", result)
    return result

def self_check():
    invalidate_worker_health_cache()
    ok, reason = worker_available()
    assert ok, reason
    ok2, _ = worker_available()
    assert ok2
    pkg = {"work_id": "worker_self_check", "cycle_id": 0, "objective": "worker bridge self check", "target_files": ["scripts/loop_worker_proof.py"], "proposed_repo_delta": ["scripts/loop_worker_proof.py"], "verification_commands": [["python3", "scripts/loop_worker_bridge.py", "--self-check"]], "execution_steps": [], "goal_inputs": {"constraints": ["minimal loop"]}, "research_inputs": {}, "verification_basis": {}}
    contract = build_worker_contract(pkg, work_package_path="project_memory/runtime/loop_cycles/cycle_0000/work_package.json")
    assert contract["work_id"] == "worker_self_check"
    assert should_use_worker_bridge({"task_type": "code_implementation"}, pkg)
    outcome = _classify_outcome(impl_errors=[], changed_files=["scripts/x.py"], verification_output=[{"result": "pass"}], expected_outputs=["scripts/x.py"])
    assert outcome == "verified_complete"
    dry = run_worker_bridge(plan={"task_type": "code_implementation"}, work_package=pkg, cycle_id=0, work_package_path=contract["work_package_path"], dry_run=True)
    assert dry["outcome_class"] in OUTCOME_CLASSES
    assert "completed_outputs" in dry
    print("loop-worker-bridge: PASS")
def main():
    p = argparse.ArgumentParser(description="purple_halo worker bridge")
    p.add_argument("--self-check", action="store_true")
    a = p.parse_args()
    if a.self_check:
        self_check()
        return 0
    p.error("specify --self-check")
    return 2
if __name__ == "__main__":
    raise SystemExit(main())
