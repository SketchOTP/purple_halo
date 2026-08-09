#!/usr/bin/env python3
"""Work package writer for purple_halo autonomous builder cycles. Stdlib only."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

DISPATCHABLE_TYPES = frozenset({"code_implementation", "verification_hardening"})
LOCAL_ONLY_WORK_PREFIXES = ("product_gap_", "operational_", "improve_", "deliver_", "repair_")


def is_local_only_item(item: dict[str, Any]) -> bool:
    wid = str(item.get("work_id") or "")
    return bool(
        item.get("local_only")
        or wid == "product_cycle_closure"
        or wid.startswith(LOCAL_ONLY_WORK_PREFIXES)
        or str(item.get("generated_from") or "") == "production_hold_repair"
    )


def resolve_dispatch_target(item: dict[str, Any], *, research: dict[str, Any] | None = None) -> str:
    if is_local_only_item(item):
        return ""
    explicit = str(item.get("dispatch_target") or "").strip()
    if explicit:
        return explicit
    capability = str(item.get("capability") or "").strip()
    if capability in {"goal_ingestion", "research_synthesis", "verification_dispatch"}:
        return capability
    research = research or {}
    area = str(research.get("capability_area") or "").strip()
    if area in {"goal_ingestion", "research_synthesis", "verification_dispatch"}:
        return area
    task_type = str(item.get("task_type") or "")
    if task_type == "verification_hardening":
        return "verification_dispatch"
    return ""


def _default_handler_inputs(
    item: dict[str, Any],
    *,
    cycle_id: int,
    research: dict[str, Any],
    goal_text: str,
) -> dict[str, Any]:
    return {
        "cycle_id": cycle_id,
        "capability_area": item.get("capability") or research.get("capability_area") or "",
        "goal_excerpt": goal_text[:400],
        "research_summary": str(research.get("summary") or "")[:400],
        "work_id": item.get("work_id"),
    }


def _default_expected_outputs(item: dict[str, Any], dispatch_target: str) -> list[str]:
    explicit = list(item.get("expected_outputs") or [])
    if explicit:
        return explicit
    defaults = {
        "goal_ingestion": ["project_memory/runtime/goal_ingestion_index.json"],
        "research_synthesis": ["project_memory/runtime/research_synthesis_log.json"],
        "verification_dispatch": [
            "project_memory/runtime/verification_dispatch_registry.json",
            "project_memory/runtime/verification_brief.json",
        ],
    }
    return list(defaults.get(dispatch_target) or item.get("proposed_repo_delta") or [])


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_work_package(
    item: dict[str, Any],
    *,
    cycle_id: int,
    research: dict[str, Any],
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
) -> dict[str, Any]:
    wid = str(item["work_id"])
    target_files = list(item.get("target_files") or item.get("expected_repo_delta") or [])
    proposed = list(item.get("proposed_repo_delta") or item.get("expected_repo_delta") or [])
    steps = list(item.get("execution_steps") or item.get("actions") or [])
    vcmds = [list(c) for c in item.get("verification_commands") or []]
    done_when = list(item.get("done_when") or [])
    if not done_when:
        done_when = [f"file exists: {p}" for p in proposed]
        for cmd in vcmds:
            done_when.append(f"command passes: {' '.join(cmd)}")
    force_worker_bridge = bool(item.get("force_worker_bridge"))
    local_only = is_local_only_item(item)
    dispatch_target = "" if (force_worker_bridge or local_only) else resolve_dispatch_target(item, research=research)
    handler_inputs = dict(item.get("handler_inputs") or _default_handler_inputs(
        item, cycle_id=cycle_id, research=research, goal_text=goal_text
    ))
    expected_outputs = _default_expected_outputs(item, dispatch_target)
    if dispatch_target and f"dispatch output: {dispatch_target}" not in " ".join(done_when):
        for out in expected_outputs:
            if f"file exists: {out}" not in done_when:
                done_when.append(f"file exists: {out}")
    from loop_artifact_inputs import build_package_lineage

    lineage = build_package_lineage(item, goal_text=goal_text, research=research)
    package: dict[str, Any] = {
        "work_id": wid,
        "cycle_id": cycle_id,
        "objective": item.get("objective") or item.get("description") or item.get("title") or wid,
        "task_type": item["task_type"],
        "capability": item.get("capability") or item.get("goal_gap_addressed") or "",
        "local_only": local_only,
        "dispatch_target": dispatch_target,
        "handler_inputs": handler_inputs,
        "expected_outputs": expected_outputs,
        "why_now": item.get("why_now") or f"Advances {item.get('goal_gap_addressed', 'product goal')}",
        "inputs_used": {
            "goal_excerpt": goal_text[:400],
            "status_excerpt": status_text[:400],
            "research_summary": str(research.get("summary") or "")[:400],
            "research_capability_area": str(research.get("capability_area") or ""),
            "repo_key_paths": repo_snapshot.get("key_paths_present") or {},
        },
        "target_files": target_files,
        "proposed_repo_delta": proposed,
        "execution_steps": steps,
        "verification_commands": vcmds,
        "done_when": done_when,
        "goal_inputs": lineage["goal_inputs"],
        "research_inputs": lineage["research_inputs"],
        "verification_basis": lineage["verification_basis"],
        "selection_rationale": lineage["selection_rationale"],
        "evidence_backed": lineage["evidence_backed"],
        "created_at": _now_iso(),
    }
    if force_worker_bridge:
        package["force_worker_bridge"] = True
    from loop_artifact_inputs import persist_verification_brief_file

    brief = persist_verification_brief_file(package=package)
    package["verification_brief_path"] = "project_memory/runtime/verification_brief.json"
    package["verification_brief_hash"] = brief.get("source_hash")
    return package


def persist_work_package(cycle_id: int, package: dict[str, Any]) -> Path:
    from loop_state import cycle_artifact_dir

    path = cycle_artifact_dir(cycle_id) / "work_package.json"
    path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    return path


def load_work_package(cycle_id: int) -> dict[str, Any] | None:
    from loop_state import cycle_artifact_dir

    path = cycle_artifact_dir(cycle_id) / "work_package.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_latest_work_package() -> dict[str, Any] | None:
    cycles_dir = ROOT / "project_memory" / "runtime" / "loop_cycles"
    if not cycles_dir.is_dir():
        return None
    dirs = sorted(cycles_dir.glob("cycle_*"), reverse=True)
    for d in dirs:
        pkg_path = d / "work_package.json"
        if pkg_path.is_file():
            return json.loads(pkg_path.read_text(encoding="utf-8"))
    return None


def package_to_plan_fields(package: dict[str, Any]) -> dict[str, Any]:
    """Map work package fields onto plan/bounded_step for executor and verifier."""
    from loop_artifact_inputs import resolve_verification_contract

    contract, contract_meta = resolve_verification_contract(package=package)
    fields: dict[str, Any] = {
        "work_package": package,
        "expected_repo_delta": list(package.get("proposed_repo_delta") or []),
        "verification_commands": [list(c) for c in package.get("verification_commands") or []],
        "done_when": list(package.get("done_when") or []),
        "target_files": list(package.get("target_files") or []),
        "bounded_step": {
            "id": package.get("work_id"),
            "task_type": package.get("task_type"),
            "actions": list(package.get("execution_steps") or []),
            "expected_repo_delta": list(package.get("proposed_repo_delta") or []),
            "verification_commands": [list(c) for c in package.get("verification_commands") or []],
            "success_criteria": [f"{p} exists" for p in package.get("proposed_repo_delta") or []],
            "done_when": list(package.get("done_when") or []),
            "dispatch_target": package.get("dispatch_target") or "",
            "expected_outputs": list(package.get("expected_outputs") or []),
        },
    }
    if package.get("force_worker_bridge"):
        fields["force_worker_bridge"] = True
    return fields


def self_check() -> None:
    sample_item = {
        "work_id": "test_item",
        "title": "Test",
        "task_type": "code_implementation",
        "goal_gap_addressed": "test",
        "target_files": ["scripts/loop_work_package.py"],
        "proposed_repo_delta": ["scripts/loop_work_package.py"],
        "execution_steps": [],
        "verification_commands": [["python3", "scripts/loop_work_package.py", "--self-check"]],
        "done_when": ["file exists: scripts/loop_work_package.py"],
        "objective": "test package",
    }
    pkg = build_work_package(
        sample_item,
        cycle_id=1,
        research={"summary": "t", "capability_area": "test"},
        goal_text="Product Goal\n",
        status_text="status",
        repo_snapshot={},
    )
    assert pkg["work_id"] == "test_item"
    assert pkg["done_when"]
    assert "dispatch_target" in pkg
    assert pkg.get("expected_outputs")
    assert pkg.get("goal_inputs") is not None
    assert pkg.get("research_inputs") is not None
    assert pkg.get("verification_basis") is not None
    assert pkg["execution_steps"] == []
    fields = package_to_plan_fields(pkg)
    assert fields["work_package"]["work_id"] == "test_item"
    repair = build_work_package(
        {
            "work_id": "repair_token_efficiency_repair_autonomous_iteration",
            "title": "Hold repair",
            "task_type": "verification_hardening",
            "local_only": True,
            "generated_from": "production_hold_repair",
            "goal_gap_addressed": "autonomous_iteration",
            "target_files": [],
            "proposed_repo_delta": [],
            "execution_steps": [{"type": "run_command", "command": ["python3", "scripts/loop_cost_policy.py", "--self-check"]}],
            "verification_commands": [["python3", "scripts/loop_cost_policy.py", "--self-check"]],
            "done_when": ["regression cleared for autonomous_iteration"],
            "objective": "repair",
        },
        cycle_id=62,
        research={"summary": "test", "capability_area": "research_synthesis"},
        goal_text="Product Goal\n",
        status_text="status",
        repo_snapshot={},
    )
    assert repair["local_only"] is True
    assert repair["dispatch_target"] == ""
    print("loop-work-package: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo work package utilities")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    parser.error("specify --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
