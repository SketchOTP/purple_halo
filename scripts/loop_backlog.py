#!/usr/bin/env python3
"""Goal backlog: durable product work queue for purple_halo. Stdlib only."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import loop_target_workspace as ltw

ROOT = Path(__file__).resolve().parent.parent


def backlog_file_path() -> Path:
    return ltw.backlog_path()
STATUSES = frozenset({"open", "in_progress", "verified", "rejected"})
EXECUTABLE_TYPES = frozenset({"code_implementation", "verification_hardening"})
NOTE_ONLY_TYPES = frozenset({"docs_update"})
EMPTY_REASONS = frozenset(
    {
        "goal_underspecified",
        "repo_blocked",
        "research_missing",
        "implementation_blocked",
        "product_complete",
        "missing_verification_basis",
        "dependency_unready",
        "no_meaningful_product_step",
        "externally_blocked",
        "budget_blocked",
        "verification_blocked",
    }
)
MEANINGFUL_CAPABILITIES = frozenset(
    {
        "goal_ingestion",
        "repo_status_analysis",
        "implementation_dispatch",
        "verification_dispatch",
        "persistence_resume",
        "schedule_control",
        "plan_generation",
        "research_synthesis",
    }
)
VAGUE_MIN_LEN = 12
PROOF_WORK_IDS = frozenset({
    "product_worker_bridge_proof",
    "economy_proof_target_slice",
    "economy_shadow_worker_gate",
})
PROOF_TARGET_MARKERS = frozenset({
    "project_memory/runtime/live_proof_marker.txt",
})
REAL_PRODUCT_CAPABILITIES = frozenset(
    {
        "goal_parser_runtime",
        "research_fetch_runtime",
        "plan_generator_runtime",
        "verification_runner_runtime",
        "resume_continuity_runtime",
    }
)


def is_external_target_active() -> bool:
    return ltw.is_external_target()


def is_proof_work_item(item: dict[str, Any]) -> bool:
    wid = str(item.get("work_id") or "")
    if wid in PROOF_WORK_IDS:
        return True
    if wid.startswith("economy_proof") or "_proof_slice" in wid:
        return True
    paths: list[str] = []
    for key in ("target_files", "proposed_repo_delta", "expected_outputs", "expected_repo_delta"):
        paths.extend(str(p) for p in (item.get(key) or []))
    if any(p in PROOF_TARGET_MARKERS or p.endswith("/live_proof_marker.txt") for p in paths):
        return True
    return bool(item.get("proof_work"))


def proof_selection_allowed() -> bool:
    if not is_external_target_active():
        return True
    if ltw.force_proof_mode():
        return True
    return not ltw.target_proof_satisfied()


def worker_bridge_validated() -> bool:
    if (ROOT / "scripts/loop_worker_proof.py").is_file():
        return True
    if (ROOT / "scripts/loop_worker_bridge.py").is_file() and (ROOT / "scripts/loop_worker_decompose.py").is_file():
        return True
    return False


def is_real_product_capability(work_id: str) -> bool:
    wid = str(work_id or "")
    if wid in REAL_PRODUCT_CAPABILITIES:
        return True
    if "_followup_" in wid or wid.endswith("_repair"):
        return True
    return False


def is_worker_backed_code_item(item: dict[str, Any]) -> bool:
    if str(item.get("task_type") or "") != "code_implementation":
        return False
    wid0 = str(item.get("work_id") or "")
    if item.get("local_only") or wid0 == "product_cycle_closure" or wid0.startswith("product_gap_"):
        return False
    if is_proof_work_item(item):
        return False
    wid = str(item.get("work_id") or "")
    if item.get("dispatch_target") and all(
        str(t).startswith("project_memory/") for t in (item.get("target_files") or [])
    ):
        return False
    if ltw.is_project_mode():
        if str(item.get("generated_from") or "") == "mission_backlog_refresh":
            paths = [str(p) for p in (item.get("target_files") or item.get("proposed_repo_delta") or [])]
            return bool(paths) and not all(ltw.is_control_plane_path(p) for p in paths)
        return False
    if is_external_target_active():
        if item.get("generated_from") == "target_backlog_refresh":
            return True
        if ltw.classify_work_item(item) != ltw.ROUTING_TARGET:
            return False
        paths = [str(p) for p in (item.get("target_files") or item.get("proposed_repo_delta") or [])]
        if paths and all(ltw.is_control_plane_path(p) for p in paths):
            return False
        return bool(paths)
    return is_real_product_capability(wid) or bool(item.get("generated_from"))


def _runtime_py_header() -> str:
    return "#!/usr/bin/env python3\n\"\"\"Product runtime module for purple_halo autonomous loop. Stdlib only.\"\"\"\n\nfrom __future__ import annotations\n\nfrom typing import Any\n\n"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _file_text(rel: str) -> str:
    p = Path(rel)
    if p.is_absolute():
        path = p
    elif ltw.is_control_plane_path(rel):
        path = ROOT / rel
    else:
        path = ltw.product_root() / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _schedule_enabled() -> bool:
    for rel in (
        "project_memory/runtime/schedule.json",
        "project_memory/runtime/schedule.default.json",
    ):
        path = ROOT / rel
        if path.is_file():
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("enabled"):
                    return True
            except json.JSONDecodeError:
                pass
    return False


def _scheduler_implemented() -> bool:
    src = _file_text("scripts/loop_schedule.py")
    return "def run_now" in src and (ROOT / "scripts/loop_runner.py").is_file()


def _default_schedule_json() -> str:
    return json.dumps(
        {"enabled": True, "timezone": "UTC", "runs": [{"at": "09:00", "label": "daily loop"}]},
        indent=2,
    ) + "\n"


def _dispatch_stub() -> str:
    return (
        '#!/usr/bin/env python3\n'
        '"""Minimal implementation dispatch stub for purple_halo. Stdlib only."""\n\n'
        "from __future__ import annotations\n\n"
        "import argparse\n"
        "import json\n"
        "from pathlib import Path\n"
        "from typing import Any\n\n"
        "ROOT = Path(__file__).resolve().parent.parent\n\n\n"
        "def dispatch_work_package(package: dict[str, Any]) -> dict[str, Any]:\n"
        '    """ponytail: single-process stub; upgrade path = agent subprocess dispatch."""\n'
        "    return {\n"
        '        "work_id": package.get("work_id"),\n'
        '        "status": "accepted",\n'
        '        "message": "Dispatch stub records package for future agent execution.",\n'
        "    }\n\n\n"
        "def self_check() -> None:\n"
        "    result = dispatch_work_package({\"work_id\": \"test\"})\n"
        '    assert result["status"] == "accepted"\n'
        '    print("loop-dispatch: PASS")\n\n\n'
        "def main() -> int:\n"
        '    parser = argparse.ArgumentParser(description="purple_halo dispatch stub")\n'
        '    parser.add_argument("--self-check", action="store_true")\n'
        "    args = parser.parse_args()\n"
        "    if args.self_check:\n"
        "        self_check()\n"
        "        return 0\n"
        '    parser.error("specify --self-check")\n'
        "    return 2\n\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )


def _real_product_runtime_specs() -> list[dict[str, Any]]:
    hdr = _runtime_py_header()
    return [
        {"work_id": "goal_parser_runtime", "title": "Goal parser runtime module", "capability": "goal_ingestion", "goal_gap_addressed": "capability_goal_ingestion", "task_type": "code_implementation", "priority": 2, "objective": "Implement goal_parser_runtime.py used by the loop to parse project goals.", "why_now": "Worker-backed cycles must build real goal ingestion runtime.", "detect_open": lambda: "def parse_goals" not in _file_text("scripts/goal_parser_runtime.py"), "target_files": ["scripts/goal_parser_runtime.py"], "proposed_repo_delta": ["scripts/goal_parser_runtime.py"], "expected_outputs": ["scripts/goal_parser_runtime.py"], "execution_steps": [{"type": "write_file", "path": "scripts/goal_parser_runtime.py", "content": hdr + "def parse_goals(text: str) -> dict[str, Any]:\n    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(\"#\")]\n    return {\"capabilities\": lines[:20], \"constraints\": [], \"raw_line_count\": len(lines)}\n\ndef self_check() -> None:\n    assert parse_goals(\"- build loop\")[\"capabilities\"]\n    print(\"goal-parser-runtime: PASS\")\n\nif __name__ == \"__main__\":\n    self_check()\n"}, {"type": "run_command", "command": ["python3", "scripts/goal_parser_runtime.py"]}], "verification_commands": [["python3", "scripts/goal_parser_runtime.py"]], "done_when": ["file exists: scripts/goal_parser_runtime.py", "symbol exists: scripts/goal_parser_runtime.py:parse_goals"]},
        {"work_id": "research_fetch_runtime", "title": "Research fetch runtime module", "capability": "research_synthesis", "goal_gap_addressed": "capability_research_synthesis", "task_type": "code_implementation", "priority": 3, "objective": "Implement research_fetch_runtime.py for planning research context.", "why_now": "Research synthesis needs a dedicated runtime module.", "detect_open": lambda: "def fetch_research_context" not in _file_text("scripts/research_fetch_runtime.py"), "target_files": ["scripts/research_fetch_runtime.py"], "proposed_repo_delta": ["scripts/research_fetch_runtime.py"], "expected_outputs": ["scripts/research_fetch_runtime.py"], "execution_steps": [{"type": "write_file", "path": "scripts/research_fetch_runtime.py", "content": hdr + "def fetch_research_context(*, goal_excerpt: str, status_excerpt: str) -> dict[str, Any]:\n    return {\"summary\": goal_excerpt[:200], \"status_hint\": status_excerpt[:200], \"capability_area\": \"research_synthesis\"}\n\ndef self_check() -> None:\n    assert fetch_research_context(goal_excerpt=\"g\", status_excerpt=\"s\")[\"summary\"]\n    print(\"research-fetch-runtime: PASS\")\n\nif __name__ == \"__main__\":\n    self_check()\n"}, {"type": "run_command", "command": ["python3", "scripts/research_fetch_runtime.py"]}], "verification_commands": [["python3", "scripts/research_fetch_runtime.py"]], "done_when": ["file exists: scripts/research_fetch_runtime.py", "symbol exists: scripts/research_fetch_runtime.py:fetch_research_context"]},
        {"work_id": "plan_generator_runtime", "title": "Plan generator runtime module", "capability": "plan_generation", "goal_gap_addressed": "capability_plan_generation", "task_type": "code_implementation", "priority": 4, "objective": "Implement plan_generator_runtime.py to turn research into plan briefs.", "why_now": "Planning must use a real runtime generator module.", "detect_open": lambda: "def generate_plan_brief" not in _file_text("scripts/plan_generator_runtime.py"), "target_files": ["scripts/plan_generator_runtime.py"], "proposed_repo_delta": ["scripts/plan_generator_runtime.py"], "expected_outputs": ["scripts/plan_generator_runtime.py"], "execution_steps": [{"type": "write_file", "path": "scripts/plan_generator_runtime.py", "content": hdr + "def generate_plan_brief(*, research: dict[str, Any], backlog_item: dict[str, Any]) -> dict[str, Any]:\n    return {\"focus\": backlog_item.get(\"title\") or \"plan\", \"objective\": backlog_item.get(\"objective\") or \"\", \"research_summary\": research.get(\"summary\") or \"\"}\n\ndef self_check() -> None:\n    assert generate_plan_brief(research={\"summary\": \"r\"}, backlog_item={\"title\": \"t\"})[\"focus\"]\n    print(\"plan-generator-runtime: PASS\")\n\nif __name__ == \"__main__\":\n    self_check()\n"}, {"type": "run_command", "command": ["python3", "scripts/plan_generator_runtime.py"]}], "verification_commands": [["python3", "scripts/plan_generator_runtime.py"]], "done_when": ["file exists: scripts/plan_generator_runtime.py", "symbol exists: scripts/plan_generator_runtime.py:generate_plan_brief"]},
        {"work_id": "verification_runner_runtime", "title": "Verification runner runtime module", "capability": "verification_dispatch", "goal_gap_addressed": "capability_verification_dispatch", "task_type": "code_implementation", "priority": 5, "objective": "Implement verification_runner_runtime.py to run verification suites.", "why_now": "Verification dispatch needs a reusable runner runtime.", "detect_open": lambda: "def run_verification_suite" not in _file_text("scripts/verification_runner_runtime.py"), "target_files": ["scripts/verification_runner_runtime.py"], "proposed_repo_delta": ["scripts/verification_runner_runtime.py"], "expected_outputs": ["scripts/verification_runner_runtime.py"], "execution_steps": [{"type": "write_file", "path": "scripts/verification_runner_runtime.py", "content": hdr + "import subprocess\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parent.parent\n\ndef run_verification_suite(commands: list[list[str]]) -> list[dict[str, object]]:\n    out = []\n    for cmd in commands:\n        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)\n        out.append({\"command\": cmd, \"exit_code\": proc.returncode, \"passed\": proc.returncode == 0})\n    return out\n\ndef self_check() -> None:\n    assert run_verification_suite([[\"python3\", \"scripts/loop_worker_decompose.py\", \"--self-check\"]])[0][\"passed\"]\n    print(\"verification-runner-runtime: PASS\")\n\nif __name__ == \"__main__\":\n    self_check()\n"}, {"type": "run_command", "command": ["python3", "scripts/verification_runner_runtime.py"]}], "verification_commands": [["python3", "scripts/verification_runner_runtime.py"]], "done_when": ["file exists: scripts/verification_runner_runtime.py", "symbol exists: scripts/verification_runner_runtime.py:run_verification_suite"]},
        {"work_id": "resume_continuity_runtime", "title": "Resume continuity runtime module", "capability": "persistence_resume", "goal_gap_addressed": "capability_persistence_resume", "task_type": "code_implementation", "priority": 6, "objective": "Implement resume_continuity_runtime.py for cross-cycle resume context.", "why_now": "Persistence/resume needs a dedicated runtime module.", "detect_open": lambda: "def build_resume_context" not in _file_text("scripts/resume_continuity_runtime.py"), "target_files": ["scripts/resume_continuity_runtime.py"], "proposed_repo_delta": ["scripts/resume_continuity_runtime.py"], "expected_outputs": ["scripts/resume_continuity_runtime.py"], "execution_steps": [{"type": "write_file", "path": "scripts/resume_continuity_runtime.py", "content": hdr + "def build_resume_context(*, cycle_id: int, last_worker: dict[str, object] | None = None) -> dict[str, object]:\n    return {\"cycle_id\": cycle_id, \"last_worker\": last_worker or {}, \"resume_reason\": \"continue product capability work\"}\n\ndef self_check() -> None:\n    assert build_resume_context(cycle_id=1)[\"cycle_id\"] == 1\n    print(\"resume-continuity-runtime: PASS\")\n\nif __name__ == \"__main__\":\n    self_check()\n"}, {"type": "run_command", "command": ["python3", "scripts/resume_continuity_runtime.py"]}], "verification_commands": [["python3", "scripts/resume_continuity_runtime.py"]], "done_when": ["file exists: scripts/resume_continuity_runtime.py", "symbol exists: scripts/resume_continuity_runtime.py:build_resume_context"]},
    ]


def _self_mode_open_gaps_missing() -> bool:
    if is_external_target_active():
        return False
    from loop_open_gaps_state import OPEN_GAPS_STATE_PATH
    if OPEN_GAPS_STATE_PATH.is_file():
        return False
    try:
        from loop_state import load_state

        state = load_state()
        control = state.get("control_state") or state
    except (OSError, json.JSONDecodeError, ImportError):
        return False
    if int(control.get("cycle_id") or 0) <= 0:
        return False
    return not (control.get("open_gaps") or [])


def _self_mode_continuity_missing() -> bool:
    if is_external_target_active():
        return False
    from loop_continuity_state import CONTINUITY_STATE_PATH
    if not CONTINUITY_STATE_PATH.is_file():
        return True
    src = _file_text("scripts/loop_continuity_state.py")
    loop_src = _file_text("scripts/purple_halo_loop.py")
    return (
        "def resume_from_continuity" not in src
        or "def write_continuity_after_cycle" not in src
        or "resume_from_continuity" not in loop_src
        or "write_continuity_after_cycle" not in loop_src
    )


_GAP_CAPABILITY_MAP: dict[str, str] = {
    "gap_scheduled_execution": "schedule_control",
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
}


def _production_self_improvement_specs() -> list[dict[str, Any]]:
    try:
        from loop_goal_delivery import delivery_work_specs, ensure_goal_delivery_mode, linked_improve_specs
        ensure_goal_delivery_mode()
        # Rank: unmet criteria delivery first, then linked improve_* unblockers only.
        return delivery_work_specs() + linked_improve_specs()
    except Exception:
        try:
            from loop_production_ops import self_improvement_specs
            return self_improvement_specs()
        except Exception:
            return []


def _operational_validation_specs() -> list[dict[str, Any]]:
    try:
        from loop_autonomous import evaluate_product_complete, live_soak_active
        from loop_production_ops import production_ops_active
        assessment = evaluate_product_complete()
        soak = live_soak_active()
        if production_ops_active():
            return []
    except Exception:
        return []
    if not assessment.get("mechanics_complete"):
        return []
    # Keep operational_* available during live soak even after operational realization.
    if assessment.get("operationally_realized") and not soak:
        return []
    specs: list[dict[str, Any]] = []
    specs.append({
        "work_id": "operational_useful_work_selection",
        "title": "Prefer useful end-goal work over gap-chore revalidation",
        "capability": "plan_generation",
        "goal_gap_addressed": "operational_useful_selection",
        "task_type": "code_implementation",
        "priority": 4,
        "local_only": True,
        "objective": "Planning must select operational improvements, not product_gap_ bookkeeping loops.",
        "why_now": "Operational realization requires useful selected work across sustained runs.",
        "detect_open": lambda: True,
        "target_files": ["scripts/loop_backlog.py", "scripts/purple_halo_loop.py"],
        "proposed_repo_delta": ["scripts/loop_backlog.py", "scripts/purple_halo_loop.py"],
        "execution_steps": [{"type": "run_command", "command": ["python3", "scripts/loop_backlog.py", "--self-check"]}],
        "verification_commands": [["python3", "scripts/loop_backlog.py", "--self-check"]],
        "done_when": [
            "symbol exists: scripts/loop_backlog.py:is_proof_revalidation_item",
            "symbol exists: scripts/purple_halo_loop.py:is_bookkeeping_plan",
        ],
        "generated_from": "operational_validation",
    })
    specs.append({
        "work_id": "operational_continuity_steering",
        "title": "Strengthen continuity-driven follow-up selection",
        "capability": "persistence_resume",
        "goal_gap_addressed": "operational_continuity_quality",
        "task_type": "code_implementation",
        "priority": 5,
        "local_only": True,
        "objective": "Continuity must steer follow-up selection on sustained autonomous runs.",
        "why_now": "Operational realization requires continuity-influenced progress.",
        "detect_open": lambda: True,
        "target_files": ["scripts/loop_continuity_state.py", "scripts/loop_autonomous.py"],
        "proposed_repo_delta": ["scripts/loop_continuity_state.py", "scripts/loop_autonomous.py"],
        "execution_steps": [{"type": "run_command", "command": ["python3", "scripts/loop_continuity_state.py", "--self-check"]}],
        "verification_commands": [["python3", "scripts/loop_continuity_state.py", "--self-check"]],
        "done_when": [
            "symbol exists: scripts/loop_continuity_state.py:resume_from_continuity",
            "symbol exists: scripts/loop_autonomous.py:_operational_assessment",
        ],
        "generated_from": "operational_validation",
    })
    specs.append({
        "work_id": "operational_cheap_default_guard",
        "title": "Keep autonomous runs inside cheap_default token policy",
        "capability": "schedule_control",
        "goal_gap_addressed": "operational_token_efficiency",
        "task_type": "code_implementation",
        "priority": 6,
        "local_only": True,
        "objective": "Autonomous long-run mode must remain cheap_default without worker spend.",
        "why_now": "Operational realization forbids automatic expensive worker execution.",
        "detect_open": lambda: True,
        "target_files": ["scripts/loop_autonomous.py", "scripts/loop_cost_policy.py"],
        "proposed_repo_delta": ["scripts/loop_autonomous.py", "scripts/loop_cost_policy.py"],
        "execution_steps": [{"type": "run_command", "command": ["python3", "scripts/loop_autonomous.py", "--self-check"]}],
        "verification_commands": [["python3", "scripts/loop_autonomous.py", "--self-check"]],
        "done_when": [
            "symbol exists: scripts/loop_autonomous.py:_operational_assessment",
            "symbol exists: scripts/loop_cost_policy.py:budget_status",
        ],
        "generated_from": "operational_validation",
    })
    specs.append({
        "work_id": "operational_verification_truthfulness",
        "title": "Keep verification honest for local operational improvements",
        "capability": "verification_dispatch",
        "goal_gap_addressed": "operational_verification_truth",
        "task_type": "verification_hardening",
        "priority": 5,
        "local_only": True,
        "objective": "Verification must accept only truthful local operational progress.",
        "why_now": "Operational realization requires trustworthy verification outcomes.",
        "detect_open": lambda: True,
        "target_files": ["scripts/loop_verify.py", "scripts/loop_backlog.py"],
        "proposed_repo_delta": ["scripts/loop_verify.py", "scripts/loop_backlog.py"],
        "execution_steps": [{"type": "run_command", "command": ["python3", "scripts/loop_verify.py", "--self-check"]}],
        "verification_commands": [["python3", "scripts/loop_verify.py", "--self-check"]],
        "done_when": [
            "symbol exists: scripts/loop_verify.py:run_verify",
            "symbol exists: scripts/loop_backlog.py:update_from_verification",
        ],
        "generated_from": "operational_validation",
    })
    return specs


def _self_loop_integration_specs() -> list[dict[str, Any]]:
    """Phase-2 purple_halo product work after scaffold runtimes are verified."""
    return [
        {
            "work_id": "product_goal_model_artifact",
            "title": "Persist runtime goal_model.json for autonomous goal analysis",
            "capability": "goal_ingestion",
            "goal_gap_addressed": "capability_goal_ingestion",
            "task_type": "code_implementation",
            "priority": 14,
            "dispatch_target": "goal_ingestion",
            "objective": "Write project_memory/runtime/goal_model.json from parsed project goals each cycle.",
            "why_now": "Goal analysis must leave a durable model artifact, not only the ingestion index.",
            "detect_open": lambda: not (ROOT / "project_memory/runtime/goal_model.json").is_file(),
            "target_files": [
                "project_memory/runtime/goal_model.json",
                "scripts/loop_artifact_inputs.py",
            ],
            "proposed_repo_delta": [
                "project_memory/runtime/goal_model.json",
                "scripts/loop_artifact_inputs.py",
            ],
            "expected_outputs": ["project_memory/runtime/goal_model.json"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_artifact_inputs.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_artifact_inputs.py", "--self-check"]],
            "done_when": [
                "file exists: project_memory/runtime/goal_model.json",
                "symbol exists: scripts/loop_artifact_inputs.py:persist_goal_model_file",
            ],
            "generated_from": "self_loop_refresh",
        },
        {
            "work_id": "product_verification_brief_artifact",
            "title": "Persist verification_brief.json for verification dispatch",
            "capability": "verification_dispatch",
            "goal_gap_addressed": "capability_verification_dispatch",
            "task_type": "verification_hardening",
            "priority": 15,
            "dispatch_target": "verification_dispatch",
            "objective": "Verification dispatch must persist project_memory/runtime/verification_brief.json.",
            "why_now": "Verification evidence chain needs a brief artifact paired with the registry.",
            "detect_open": lambda: not (ROOT / "project_memory/runtime/verification_brief.json").is_file(),
            "target_files": [
                "project_memory/runtime/verification_brief.json",
                "scripts/loop_dispatch.py",
            ],
            "proposed_repo_delta": [
                "project_memory/runtime/verification_brief.json",
                "scripts/loop_dispatch.py",
            ],
            "expected_outputs": ["project_memory/runtime/verification_brief.json"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_dispatch.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_dispatch.py", "--self-check"]],
            "done_when": [
                "file exists: project_memory/runtime/verification_brief.json",
                "symbol exists: scripts/loop_artifact_inputs.py:persist_verification_brief_file",
            ],
            "generated_from": "self_loop_refresh",
        },
        {
            "work_id": "product_open_gaps_state_hydrate",
            "title": "Hydrate loop state open_gaps after each analyzed cycle",
            "capability": "persistence_resume",
            "goal_gap_addressed": "capability_persistence_resume",
            "task_type": "code_implementation",
            "priority": 16,
            "objective": "Backlog refresh and cycle end must persist non-empty open_gaps when analyze_goal_gaps finds work.",
            "why_now": "Planning, backlog, resume, and status need canonical open_gaps_state.json.",
            "detect_open": _self_mode_open_gaps_missing,
            "target_files": ["scripts/loop_open_gaps_state.py", "project_memory/runtime/open_gaps_state.json"],
            "proposed_repo_delta": ["scripts/loop_open_gaps_state.py", "project_memory/runtime/open_gaps_state.json"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_backlog.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_backlog.py", "--self-check"]],
            "done_when": [
                "file exists: project_memory/runtime/open_gaps_state.json",
                "symbol exists: scripts/loop_open_gaps_state.py:hydrate_open_gaps_state",
            ],
            "generated_from": "self_loop_refresh",
        },
        {
            "work_id": "product_continuity_state_resume",
            "title": "Persist and resume open-gap continuity across cycles",
            "capability": "persistence_resume",
            "goal_gap_addressed": "gap_continuity_open_gaps",
            "task_type": "code_implementation",
            "priority": 12,
            "objective": "Write continuity_state.json at cycle end and resume carried-forward focus at cycle start.",
            "why_now": "Planning must prefer unresolved carried-forward top gaps over ad hoc rediscovery.",
            "detect_open": _self_mode_continuity_missing,
            "target_files": [
                "scripts/loop_continuity_state.py",
                "project_memory/runtime/continuity_state.json",
            ],
            "proposed_repo_delta": [
                "scripts/loop_continuity_state.py",
                "project_memory/runtime/continuity_state.json",
            ],
            "expected_outputs": ["project_memory/runtime/continuity_state.json"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_continuity_state.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_continuity_state.py", "--self-check"]],
            "done_when": [
                "file exists: project_memory/runtime/continuity_state.json",
                "symbol exists: scripts/loop_continuity_state.py:resume_from_continuity",
            ],
            "generated_from": "self_loop_refresh",
        },
        {
            "work_id": "product_cycle_closure",
            "title": "Close real autonomous cycle with meaningful progress or honest block",
            "capability": "verification_dispatch",
            "local_only": True,
            "goal_gap_addressed": "gap_product_realization",
            "task_type": "code_implementation",
            "priority": 8,
            "objective": "Each self-mode cycle must load goal/open-gaps/continuity, plan one bounded step, execute, verify, persist, and record schedule outcome.",
            "why_now": "Stop scaffolding; prove the purple_halo loop completes its intended product cycle.",
            "detect_open": lambda: not bool(
                ((__import__("loop_state", fromlist=["load_state"]).load_state().get("last_cycle") or {}).get("meaningful_product_progress"))
            ),
            "target_files": ["scripts/purple_halo_loop.py", "scripts/loop_backlog.py", "scripts/loop_plan.py"],
            "proposed_repo_delta": ["scripts/purple_halo_loop.py", "scripts/loop_backlog.py", "scripts/loop_plan.py"],
            "expected_outputs": ["scripts/purple_halo_loop.py"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/purple_halo_loop.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/purple_halo_loop.py", "--self-check"]],
            "done_when": [
                "symbol exists: scripts/purple_halo_loop.py:evaluate_cycle_outcome",
                "symbol exists: scripts/purple_halo_loop.py:_record_cycle_schedule",
            ],
            "generated_from": "self_loop_refresh",
        },
    ]


def _gap_driven_product_specs(
    capability_gaps: list[dict[str, Any]],
    *,
    research: dict[str, Any],
    existing_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if is_external_target_active() or not capability_gaps:
        return []
    verified_gap_ids = {
        str(i.get("goal_gap_addressed") or "")
        for i in existing_items
        if i.get("status") == "verified"
    }
    existing_ids = {str(i.get("work_id") or "") for i in existing_items}
    specs: list[dict[str, Any]] = []
    for gap in sorted(capability_gaps, key=lambda g: int(g.get("priority") or 99)):
        gap_id = str(gap.get("id") or "")
        if not gap_id or gap_id in verified_gap_ids:
            continue
        work_id = f"product_gap_{gap_id.removeprefix('gap_')}"
        if work_id in existing_ids:
            continue
        capability = _GAP_CAPABILITY_MAP.get(gap_id, "plan_generation")
        specs.append(
            {
                "work_id": work_id,
                "title": f"Close loop gap: {gap.get('description') or gap_id}",
                "capability": capability,
                "goal_gap_addressed": gap_id,
                "task_type": "code_implementation",
                "priority": max(1, int(gap.get("priority") or 50)),
                "objective": str(gap.get("description") or gap_id),
                "why_now": f"Highest-value purple_halo loop gap under construction: {gap_id}.",
                "detect_open": lambda _gid=gap_id: True,
                "target_files": ["scripts/purple_halo_loop.py"],
                "proposed_repo_delta": ["scripts/purple_halo_loop.py", "project_learning/active.md"],
                "expected_outputs": ["project_learning/active.md"],
                "execution_steps": [
                    {
                        "type": "run_command",
                        "command": ["python3", "scripts/purple_halo_loop.py", "--self-check"],
                    }
                ],
                "verification_commands": [["python3", "scripts/purple_halo_loop.py", "--self-check"]],
                "done_when": ["file exists: scripts/purple_halo_loop.py"],
                "generated_from": "self_loop_gap_refresh",
                "local_only": True,
            }
        )
        if len(specs) >= 3:
            break
    return specs


def _seed_self_loop_gap_work(
    backlog: dict[str, Any],
    *,
    capability_gaps: list[dict[str, Any]],
    research: dict[str, Any],
) -> dict[str, Any]:
    if is_external_target_active() or not capability_gaps:
        return backlog
    existing = backlog.get("product_work_items") or []
    if open_items(backlog):
        return backlog
    gap_specs = _gap_driven_product_specs(capability_gaps, research=research, existing_items=existing)
    if not gap_specs:
        return backlog
    fresh = [_spec_to_item(spec, research=research) for spec in gap_specs]
    fresh = [i for i in fresh if i.get("status") == "open"]
    if not fresh:
        return backlog
    merged = _merge_items(existing, fresh)
    backlog["product_work_items"] = merged
    backlog["empty_reason"] = ""
    backlog["updated_at"] = _now_iso()
    return backlog


def _product_capability_catalog() -> list[dict[str, Any]]:
    """Bounded product work tied to purple_halo autonomous loop capabilities."""
    loop_src = _file_text("scripts/purple_halo_loop.py")
    backlog_src = _file_text("scripts/loop_backlog.py")
    execute_src = _file_text("scripts/loop_execute.py")
    verify_src = _file_text("scripts/loop_verify.py")
    research_src = _file_text("scripts/loop_research.py")
    pkg_src = _file_text("scripts/loop_work_package.py")
    dispatch_src = _file_text("scripts/loop_dispatch.py")

    return _real_product_runtime_specs() + _self_loop_integration_specs() + _operational_validation_specs() + _production_self_improvement_specs() + [
        {
            "work_id": "product_goal_snapshot",
            "title": "Persist goal snapshot artifact each cycle",
            "capability": "goal_ingestion",
            "goal_gap_addressed": "capability_goal_ingestion",
            "task_type": "code_implementation",
            "priority": 5,
            "objective": "Write goal_snapshot.json into each cycle artifact dir before execution.",
            "why_now": "Goal ingestion must be traceable per cycle for resume and audit.",
            "detect_open": lambda: "goal_snapshot.json" not in loop_src,
            "target_files": ["scripts/purple_halo_loop.py"],
            "proposed_repo_delta": ["scripts/purple_halo_loop.py"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/purple_halo_loop.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/purple_halo_loop.py", "--self-check"]],
            "done_when": [
                "file exists: scripts/purple_halo_loop.py",
                "symbol exists: scripts/purple_halo_loop.py:_write_goal_snapshot",
            ],
        },
        {
            "work_id": "product_work_package_writer",
            "title": "Build and persist work package before execution",
            "capability": "plan_generation",
            "goal_gap_addressed": "capability_plan_generation",
            "task_type": "code_implementation",
            "priority": 6,
            "objective": "Selected backlog items become work_package.json artifacts before execute.",
            "why_now": "Planning must produce an execution brief, not a bare backlog row.",
            "detect_open": lambda: "def build_work_package" not in pkg_src,
            "target_files": ["scripts/loop_work_package.py"],
            "proposed_repo_delta": ["scripts/loop_work_package.py"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_work_package.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_work_package.py", "--self-check"]],
            "done_when": [
                "file exists: scripts/loop_work_package.py",
                "symbol exists: scripts/loop_work_package.py:build_work_package",
            ],
        },
        {
            "work_id": "product_executor_package_consumer",
            "title": "Executor consumes work package execution steps",
            "capability": "implementation_dispatch",
            "goal_gap_addressed": "capability_implementation_dispatch",
            "task_type": "code_implementation",
            "priority": 7,
            "objective": "loop_execute runs work_package.execution_steps instead of bare backlog actions.",
            "why_now": "Implementation dispatch must follow the persisted execution brief.",
            "detect_open": lambda: "work_package" not in execute_src or "execution_steps" not in execute_src,
            "target_files": ["scripts/loop_execute.py"],
            "proposed_repo_delta": ["scripts/loop_execute.py"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_execute.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_execute.py", "--self-check"]],
            "done_when": [
                "file exists: scripts/loop_execute.py",
                "symbol exists: scripts/loop_execute.py:_actions_from_plan",
            ],
        },
        {
            "work_id": "product_verifier_done_when",
            "title": "Verifier judges done_when and proposed repo delta",
            "capability": "verification_dispatch",
            "goal_gap_addressed": "capability_verification_dispatch",
            "task_type": "verification_hardening",
            "priority": 8,
            "objective": "Verification passes only when done_when, repo delta, and commands all succeed.",
            "why_now": "Backlog items must not verify without concrete completion evidence.",
            "detect_open": lambda: "_verify_done_when" not in verify_src,
            "target_files": ["scripts/loop_verify.py"],
            "proposed_repo_delta": ["scripts/loop_verify.py"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_verify.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_verify.py", "--self-check"]],
            "done_when": [
                "file exists: scripts/loop_verify.py",
                "symbol exists: scripts/loop_verify.py:_verify_done_when",
            ],
        },
        {
            "work_id": "product_backlog_quality_rules",
            "title": "Backlog quality rules reject vague and note-only items",
            "capability": "plan_generation",
            "goal_gap_addressed": "capability_plan_generation",
            "task_type": "verification_hardening",
            "priority": 9,
            "objective": "Backlog refresh rejects vague, duplicate, and unverifiable work items.",
            "why_now": "Product backlog must stay executable, not padded with maintenance notes.",
            "detect_open": lambda: "_apply_quality_rules" not in backlog_src,
            "target_files": ["scripts/loop_backlog.py"],
            "proposed_repo_delta": ["scripts/loop_backlog.py"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_backlog.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_backlog.py", "--self-check"]],
            "done_when": [
                "file exists: scripts/loop_backlog.py",
                "symbol exists: scripts/loop_backlog.py:_apply_quality_rules",
            ],
        },
        {
            "work_id": "product_cycle_index_writer",
            "title": "Maintain loop_cycles index for persistence and resume",
            "capability": "persistence_resume",
            "goal_gap_addressed": "capability_persistence_resume",
            "task_type": "code_implementation",
            "priority": 10,
            "objective": "Update loop_cycles/index.json after each cycle with work package metadata.",
            "why_now": "Resume requires a durable index of recent cycle artifacts.",
            "detect_open": lambda: "_update_cycle_index" not in loop_src,
            "target_files": ["scripts/purple_halo_loop.py"],
            "proposed_repo_delta": ["scripts/purple_halo_loop.py", "project_memory/runtime/loop_cycles/index.json"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/purple_halo_loop.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/purple_halo_loop.py", "--self-check"]],
            "done_when": [
                "file exists: scripts/purple_halo_loop.py",
                "symbol exists: scripts/purple_halo_loop.py:_update_cycle_index",
            ],
        },
        {
            "work_id": "product_research_capability_area",
            "title": "Research output binds capability area for synthesis",
            "capability": "research_synthesis",
            "goal_gap_addressed": "capability_research_synthesis",
            "task_type": "verification_hardening",
            "priority": 11,
            "objective": "Research artifacts include capability_area aligned to product loop capabilities.",
            "why_now": "Research synthesis must feed plan generation with capability context.",
            "detect_open": lambda: "capability_area" not in research_src,
            "target_files": ["scripts/loop_research.py"],
            "proposed_repo_delta": ["scripts/loop_research.py"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_research.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_research.py", "--self-check"]],
            "done_when": [
                "file exists: scripts/loop_research.py",
                "symbol exists: scripts/loop_research.py:_capability_area_for_gap",
            ],
        },
        {
            "work_id": "product_status_backlog_health",
            "title": "Status exposes backlog health and latest work package",
            "capability": "repo_status_analysis",
            "goal_gap_addressed": "capability_repo_status_analysis",
            "task_type": "verification_hardening",
            "priority": 12,
            "objective": "Loop status reports selected work, latest package, and backlog health.",
            "why_now": "Operators need visibility into executable backlog quality.",
            "detect_open": lambda: "backlog_health" not in loop_src,
            "target_files": ["scripts/purple_halo_loop.py"],
            "proposed_repo_delta": ["scripts/purple_halo_loop.py"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/purple_halo_loop.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/purple_halo_loop.py", "--self-check"]],
            "done_when": [
                "file exists: scripts/purple_halo_loop.py",
                "symbol exists: scripts/purple_halo_loop.py:_backlog_health",
            ],
        },
        {
            "work_id": "product_real_dispatcher",
            "title": "Real bounded dispatch handlers for implementation routing",
            "capability": "implementation_dispatch",
            "goal_gap_addressed": "capability_implementation_dispatch",
            "task_type": "code_implementation",
            "priority": 13,
            "dispatch_target": "",
            "objective": "Replace dispatch stub with handlers for goal_ingestion, research_synthesis, verification_dispatch.",
            "why_now": "Work packages must route through named handlers, not a generic stub.",
            "detect_open": lambda: "def handle_goal_ingestion" not in dispatch_src,
            "target_files": ["scripts/loop_dispatch.py"],
            "proposed_repo_delta": ["scripts/loop_dispatch.py"],
            "expected_outputs": ["scripts/loop_dispatch.py"],
            "execution_steps": [
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_dispatch.py", "--self-check"],
                }
            ],
            "verification_commands": [["python3", "scripts/loop_dispatch.py", "--self-check"]],
            "done_when": [
                "file exists: scripts/loop_dispatch.py",
                "symbol exists: scripts/loop_dispatch.py:handle_goal_ingestion",
            ],
        },
        {
            "work_id": "product_dispatch_goal_index",
            "title": "Build goal ingestion index via dispatch handler",
            "capability": "goal_ingestion",
            "goal_gap_addressed": "capability_goal_ingestion",
            "task_type": "code_implementation",
            "priority": 15,
            "dispatch_target": "goal_ingestion",
            "objective": "Route work package through goal_ingestion handler to persist goal_ingestion_index.json.",
            "why_now": "Goal ingestion must produce durable runtime artifacts via dispatch.",
            "detect_open": lambda: not (ROOT / "project_memory/runtime/goal_ingestion_index.json").is_file(),
            "target_files": ["project_memory/runtime/goal_ingestion_index.json"],
            "proposed_repo_delta": ["project_memory/runtime/goal_ingestion_index.json"],
            "expected_outputs": ["project_memory/runtime/goal_ingestion_index.json"],
            "execution_steps": [],
            "verification_commands": [["python3", "scripts/loop_dispatch.py", "--self-check"]],
            "done_when": ["file exists: project_memory/runtime/goal_ingestion_index.json"],
        },
        {
            "work_id": "product_dispatch_research_log",
            "title": "Build research synthesis log via dispatch handler",
            "capability": "research_synthesis",
            "goal_gap_addressed": "capability_research_synthesis",
            "task_type": "verification_hardening",
            "priority": 16,
            "dispatch_target": "research_synthesis",
            "objective": "Route work package through research_synthesis handler to persist synthesis log.",
            "why_now": "Research synthesis must bind capability_area to durable runtime state.",
            "detect_open": lambda: not (ROOT / "project_memory/runtime/research_synthesis_log.json").is_file(),
            "target_files": ["project_memory/runtime/research_synthesis_log.json"],
            "proposed_repo_delta": ["project_memory/runtime/research_synthesis_log.json"],
            "expected_outputs": ["project_memory/runtime/research_synthesis_log.json"],
            "execution_steps": [],
            "verification_commands": [["python3", "scripts/loop_dispatch.py", "--self-check"]],
            "done_when": ["file exists: project_memory/runtime/research_synthesis_log.json"],
        },
        {
            "work_id": "product_dispatch_verification_registry",
            "title": "Build verification dispatch registry via handler",
            "capability": "verification_dispatch",
            "goal_gap_addressed": "capability_verification_dispatch",
            "task_type": "verification_hardening",
            "priority": 17,
            "dispatch_target": "verification_dispatch",
            "objective": "Route work package through verification_dispatch handler to persist registry and brief.",
            "why_now": "Verification dispatch must record handler-specific outputs before marking verified.",
            "detect_open": lambda: not (ROOT / "project_memory/runtime/verification_dispatch_registry.json").is_file(),
            "target_files": ["project_memory/runtime/verification_dispatch_registry.json"],
            "proposed_repo_delta": [
                "project_memory/runtime/verification_dispatch_registry.json",
                "project_memory/runtime/verification_brief.json",
            ],
            "expected_outputs": [
                "project_memory/runtime/verification_dispatch_registry.json",
                "project_memory/runtime/verification_brief.json",
            ],
            "execution_steps": [],
            "verification_commands": [["python3", "scripts/loop_dispatch.py", "--self-check"]],
            "done_when": [
                "file exists: project_memory/runtime/verification_dispatch_registry.json",
                "file exists: project_memory/runtime/verification_brief.json",
            ],
        },
        {
            "work_id": "product_worker_bridge_proof",
            "title": "Prove governed worker bridge implements product code",
            "capability": "implementation_dispatch",
            "goal_gap_addressed": "capability_implementation_dispatch",
            "task_type": "code_implementation",
            "priority": 99,
            "dispatch_target": "",
            "objective": "Create loop_worker_proof.py through the governed worker bridge.",
            "why_now": "Implementation dispatch must use the governed coding worker for product code.",
            "detect_open": lambda: (not worker_bridge_validated()) and (not (ROOT / "scripts/loop_worker_proof.py").is_file()),
            "target_files": ["scripts/loop_worker_proof.py"],
            "proposed_repo_delta": ["scripts/loop_worker_proof.py"],
            "expected_outputs": ["scripts/loop_worker_proof.py"],
            "execution_steps": [
                {
                    "type": "write_file",
                    "path": "scripts/loop_worker_proof.py",
                    "content": (
                        '#!/usr/bin/env python3\n'
                        '"""Product proof module written via governed worker bridge."""\n\n'
                        "from __future__ import annotations\n\n\n"
                        "def worker_proof() -> str:\n"
                        '    return "governed worker bridge proof"\n\n\n'
                        "def self_check() -> None:\n"
                        '    assert worker_proof() == "governed worker bridge proof"\n'
                        '    print("loop-worker-proof: PASS")\n\n\n'
                        'if __name__ == "__main__":\n'
                        "    self_check()\n"
                    ),
                },
                {
                    "type": "run_command",
                    "command": ["python3", "scripts/loop_worker_proof.py"],
                },
            ],
            "verification_commands": [["python3", "scripts/loop_worker_proof.py"]],
            "done_when": [
                "file exists: scripts/loop_worker_proof.py",
                "symbol exists: scripts/loop_worker_proof.py:worker_proof",
            ],
        },
        {
            "work_id": "product_schedule_enabled",
            "title": "Enable operator schedule for autonomous runs",
            "capability": "schedule_control",
            "goal_gap_addressed": "capability_schedule_control",
            "task_type": "scheduler_integration",
            "priority": 14,
            "objective": "Turn on schedule.enabled so scheduled runs can fire.",
            "why_now": "Schedule control must be active before daily autonomous execution.",
            "detect_open": lambda: _scheduler_implemented() and not _schedule_enabled(),
            "target_files": ["project_memory/runtime/schedule.json"],
            "proposed_repo_delta": ["project_memory/runtime/schedule.json"],
            "execution_steps": [
                {
                    "type": "write_file",
                    "path": "project_memory/runtime/schedule.json",
                    "content": _default_schedule_json(),
                }
            ],
            "verification_commands": [["python3", "scripts/loop_schedule.py", "--show"]],
            "done_when": ["file exists: project_memory/runtime/schedule.json"],
        },
    ]


def _spec_to_item(spec: dict[str, Any], *, research: dict[str, Any]) -> dict[str, Any]:
    detect = spec.get("detect_open")
    is_open = callable(detect) and detect()
    item = {
        "work_id": spec["work_id"],
        "title": spec["title"],
        "capability": spec.get("capability") or "",
        "local_only": bool(spec.get("local_only")),
        "goal_gap_addressed": spec["goal_gap_addressed"],
        "task_type": spec["task_type"],
        "priority": spec["priority"],
        "status": "open" if is_open else "verified",
        "objective": spec.get("objective") or spec["title"],
        "why_now": spec.get("why_now") or "",
        "description": spec.get("objective") or spec["title"],
        "target_files": list(spec.get("target_files") or []),
        "proposed_repo_delta": list(spec.get("proposed_repo_delta") or spec.get("expected_repo_delta") or []),
        "execution_steps": list(spec.get("execution_steps") or spec.get("actions") or []),
        "verification_commands": [list(c) for c in spec.get("verification_commands") or []],
        "done_when": list(spec.get("done_when") or []),
        "dispatch_target": spec.get("dispatch_target") or (
            spec.get("capability")
            if spec.get("capability") in {"goal_ingestion", "research_synthesis", "verification_dispatch"}
            else ""
        ),
        "expected_outputs": list(spec.get("expected_outputs") or spec.get("proposed_repo_delta") or []),
        "handler_inputs": dict(spec.get("handler_inputs") or {}),
        "blocked_by": spec.get("blocked_by") or "",
        "actions": list(spec.get("execution_steps") or spec.get("actions") or []),
        "expected_repo_delta": list(spec.get("proposed_repo_delta") or spec.get("expected_repo_delta") or []),
    }
    if not is_open:
        item["verified_at"] = _now_iso()
    if research.get("summary"):
        item["research_fact"] = str(research["summary"])[:240]
    if spec.get("generated_from"):
        item["generated_from"] = spec["generated_from"]
    if spec.get("hold_work_class"):
        item["hold_work_class"] = spec["hold_work_class"]
    if spec.get("routing_class"):
        item["routing_class"] = spec["routing_class"]
    return item


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def _has_verification_path(item: dict[str, Any]) -> bool:
    if item.get("verification_commands"):
        return True
    if item.get("done_when"):
        return True
    if item.get("expected_outputs") and item.get("dispatch_target"):
        return True
    return False


def _is_note_only(item: dict[str, Any]) -> bool:
    if item.get("task_type") not in NOTE_ONLY_TYPES:
        return False
    paths = list(item.get("target_files") or item.get("proposed_repo_delta") or [])
    if not paths:
        return True
    return all(
        p.startswith(("project_learning/", "project_status.md", "repo_map.md"))
        for p in paths
    )


def is_bookkeeping_item(item: dict[str, Any]) -> bool:
    if str(item.get("generated_from") or "") in {"mission_backlog_refresh", "target_backlog_refresh"}:
        return False
    if _is_note_only(item):
        return True
    wid = str(item.get("work_id") or "")
    if wid.startswith("blocked_"):
        return True
    paths = [str(p) for p in (item.get("target_files") or item.get("proposed_repo_delta") or item.get("expected_repo_delta") or [])]
    code_paths = [p for p in paths if p.endswith((".py", ".sh")) or p.startswith("contracts/")]
    if code_paths:
        return False
    task = str(item.get("task_type") or "")
    if task == "docs_update":
        return True
    if paths and all(p.endswith(".md") or p.startswith("project_learning/") for p in paths):
        return True
    return False


def is_proof_revalidation_item(item: dict[str, Any]) -> bool:
    wid = str(item.get("work_id") or "")
    if wid in {"product_cycle_closure"}:
        return True
    # already-verified gap closures are proof revalidation, not end-goal building
    if wid.startswith("product_gap_") and item.get("status") == "verified":
        return True
    return False


def is_end_goal_capability_item(item: dict[str, Any]) -> bool:
    if is_proof_revalidation_item(item) or is_proof_work_item(item) or is_bookkeeping_item(item):
        return False
    capability = str(item.get("capability") or "")
    return capability in MEANINGFUL_CAPABILITIES or str(item.get("work_id") or "").startswith("product_")


def is_meaningful_product_item(item: dict[str, Any]) -> bool:
    if is_proof_work_item(item) or is_bookkeeping_item(item) or is_proof_revalidation_item(item):
        return False
    if ltw.is_project_mode():
        if ltw.classify_work_item(item) != ltw.ROUTING_TARGET:
            return False
        paths = [str(p) for p in (item.get("target_files") or item.get("proposed_repo_delta") or [])]
        if paths and any(ltw.is_control_plane_path(p) for p in paths):
            return False
        return str(item.get("task_type") or "") in EXECUTABLE_TYPES | {"scheduler_integration"}
    wid = str(item.get("work_id") or "")
    if wid.startswith("product_gap_") or wid == "product_cycle_closure":
        return False
    if str(item.get("task_type") or "") not in EXECUTABLE_TYPES | {"scheduler_integration"}:
        return False
    capability = str(item.get("capability") or "")
    if capability in MEANINGFUL_CAPABILITIES:
        return True
    if wid.startswith("product_") or wid.startswith("operational_"):
        return True
    return False


def _apply_quality_rules(
    items: list[dict[str, Any]],
    *,
    goal_underspecified: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Reject low-quality backlog items. Returns (kept, rejection_reasons)."""
    kept: list[dict[str, Any]] = []
    rejections: list[str] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()

    for item in items:
        wid = str(item.get("work_id") or "")
        title = str(item.get("title") or item.get("objective") or "")
        norm_title = _normalize_title(title)

        if not wid:
            rejections.append("missing work_id")
            continue
        if wid in seen_ids:
            rejections.append(f"duplicate work_id: {wid}")
            continue
        if norm_title in seen_titles:
            rejections.append(f"duplicate title: {wid}")
            continue
        if len(title) < VAGUE_MIN_LEN and len(str(item.get("objective") or "")) < VAGUE_MIN_LEN:
            rejections.append(f"vague item: {wid}")
            continue
        if not (item.get("target_files") or item.get("proposed_repo_delta") or item.get("expected_repo_delta")):
            rejections.append(f"no target files: {wid}")
            continue
        if not _has_verification_path(item):
            rejections.append(f"no verification path: {wid}")
            continue
        if _is_note_only(item) and not goal_underspecified:
            rejections.append(f"note-only item rejected: {wid}")
            continue

        seen_ids.add(wid)
        seen_titles.add(norm_title)
        kept.append(item)

    return kept, rejections


def _goal_underspecified(goal_text: str) -> bool:
    text = goal_text.strip()
    if ltw.is_project_mode():
        return len(text) < 80 or "Success criteria" not in text
    return not text or len(text) < 50 or "Product Goal" not in text


def _derive_product_items(
    *,
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
    state: dict[str, Any],
    research: dict[str, Any],
) -> list[dict[str, Any]]:
    if ltw.is_project_mode():
        raw = [
            _spec_to_item(spec, research=research)
            for spec in _project_mode_product_specs(
                goal_text=goal_text, status_text=status_text, repo_snapshot=repo_snapshot
            )
        ]
        filtered, _ = _apply_quality_rules(raw, goal_underspecified=False)
        return filtered
    if is_external_target_active():
        raw = [
            _spec_to_item(spec, research=research)
            for spec in _external_target_product_specs(
                goal_text=goal_text, status_text=status_text, repo_snapshot=repo_snapshot
            )
        ]
        filtered, _ = _apply_quality_rules(raw, goal_underspecified=False)
        return filtered
    if _goal_underspecified(goal_text):
        return []
    raw = [_spec_to_item(spec, research=research) for spec in _product_capability_catalog()]
    filtered, rejections = _apply_quality_rules(raw, goal_underspecified=True)
    if rejections and not filtered:
        return []
    open_items = [i for i in filtered if i.get("status") == "open"]
    if open_items:
        executable = [i for i in open_items if i.get("task_type") in EXECUTABLE_TYPES]
        if not executable:
            # ponytail: hard-block classification when catalog has no code/verify open items
            return []
    filtered.sort(key=lambda i: int(i.get("priority", 99)))
    from loop_artifact_inputs import apply_artifact_scoring

    return apply_artifact_scoring(filtered, goal_text=goal_text, research=research)


def default_backlog() -> dict[str, Any]:
    return {
        "version": 2,
        "updated_at": _now_iso(),
        "capability_gaps": [],
        "product_work_items": [],
        "research_summary": "",
        "empty_reason": "",
        "quality_rejections": [],
        "backlog_health": {},
    }


def load_backlog() -> dict[str, Any]:
    path = backlog_file_path()
    if not path.is_file():
        return default_backlog()
    return json.loads(path.read_text(encoding="utf-8"))


def save_backlog(backlog: dict[str, Any]) -> Path:
    path = backlog_file_path()
    backlog["updated_at"] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(backlog, indent=2) + "\n", encoding="utf-8")
    return path


def _merge_items(existing: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(i["work_id"]): i for i in existing}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in fresh:
        wid = str(item["work_id"])
        seen.add(wid)
        prior = by_id.get(wid)
        if prior and prior.get("status") in {"verified", "rejected"}:
            # Re-open operational validation work while still operationally unproven.
            if (
                prior.get("status") == "verified"
                and str(item.get("generated_from") or "") in {
                    "operational_validation", "production_self_improvement",
                    "goal_delivery", "goal_delivery_linked_improve", "production_hold_repair",
                }
                and item.get("status") == "open"
            ):
                merged.append(item)
            else:
                merged.append(prior)
        elif prior and prior.get("status") == "in_progress":
            merged.append({**item, **{k: prior[k] for k in ("status", "cycle_id", "failure_reason") if k in prior}})
        elif prior and prior.get("status") == "open":
            keep = {k: prior[k] for k in ("failure_reason", "blocked_by") if k in prior}
            if prior.get("force_worker_bridge"):
                keep["force_worker_bridge"] = True
                keep["dispatch_target"] = prior.get("dispatch_target", "")
            merged.append({**item, **keep})
        else:
            merged.append(item)
    for wid, prior in by_id.items():
        if wid not in seen and prior.get("status") in {"verified", "rejected", "open", "in_progress"}:
            merged.append(prior)
    merged.sort(key=lambda i: int(i.get("priority", 99)))
    return merged


def backlog_health(backlog: dict[str, Any] | None = None) -> dict[str, Any]:
    backlog = backlog or load_backlog()
    items = backlog.get("product_work_items") or []
    open_list = open_items(backlog)
    executable_open = [i for i in open_list if i.get("task_type") in EXECUTABLE_TYPES]
    code_verify_total = [i for i in items if i.get("task_type") in EXECUTABLE_TYPES]
    return {
        "total_items": len(items),
        "open_count": len(open_list),
        "executable_open_count": len(executable_open),
        "code_verify_total": len(code_verify_total),
        "quality_rejections": backlog.get("quality_rejections") or [],
        "empty_reason": backlog.get("empty_reason") or "",
        "healthy": len(items) >= 5 and len(code_verify_total) >= 2,
        "has_executable_open": bool(executable_open),
    }




def _repo_map_needs_refresh() -> bool:
    text = _file_text("repo_map.md").lower()
    return "unknown" in text or "populate when known" in text or "examples only" in text


_BOOTSTRAP_GOAL_MARKERS = (
    "Define the product goal for this repository.",
    "under autonomous construction.",
    "Advance verifiable product work without proof-marker cycles.",
)


def _goals_need_enrichment() -> bool:
    text = _file_text("project_goals.md")
    if len(text.strip()) < 200:
        return True
    if any(marker in text for marker in _BOOTSTRAP_GOAL_MARKERS):
        return True
    required = ("Success criteria", "Architecture entrypoints", "Non-goals")
    return any(section not in text for section in required)


def _gather_target_repo_evidence(
    *,
    status_text: str,
    repo_snapshot: dict[str, Any],
) -> dict[str, Any]:
    root = ltw.product_root()
    readme = _file_text("README.md")
    product_name = "Aether Home"
    product_summary = "Local-first home assistant with room-node satellites and a central hub."
    for line in readme.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            product_name = stripped.lstrip("# ").strip() or product_name
        elif stripped and not stripped.startswith("#"):
            product_summary = stripped
            break
    services: list[str] = []
    svc_root = root / "services"
    if svc_root.is_dir():
        services = sorted(p.name for p in svc_root.iterdir() if p.is_dir() and not p.name.startswith("."))
    scripts_dir = root / "scripts"
    hub_scripts = sorted(p.name for p in scripts_dir.glob("run_aether*.sh")) if scripts_dir.is_dir() else []
    systemd_dir = root / "systemd"
    systemd_units = sorted(p.name for p in systemd_dir.glob("*.service")) if systemd_dir.is_dir() else []
    focus = ""
    status_label = ""
    for line in status_text.splitlines():
        if "Current focus:" in line:
            focus = line.split(":", 1)[-1].strip()
        if "Current status label:" in line:
            status_label = line.split(":", 1)[-1].strip()
    slug = str(repo_snapshot.get("target_repo_slug") or "")
    if not slug and ltw.active_contract():
        slug = str(ltw.active_contract().get("target_repo_slug") or "")
    return {
        "slug": slug,
        "product_name": product_name,
        "product_summary": product_summary,
        "services": services,
        "hub_scripts": hub_scripts,
        "systemd_units": systemd_units,
        "focus": focus,
        "status_label": status_label,
    }


def _build_repo_map_content(*, repo_snapshot: dict[str, Any]) -> str:
    root = ltw.product_root()
    top_dirs = sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in {"__pycache__", "node_modules"}
    )
    lines = [
        "# Repo Map",
        "",
        "Token-light human navigation map.",
        "",
        f"Last updated: {_now_iso()[:10]} (purple_halo target refresh)",
        "",
        "## Related Docs",
        "",
        "- `project_status.md` — current project state",
        "- `project_goals.md` — product goal",
        "- `project_memory/index.json` — compact navigation index",
        "",
        "## Entry Points",
        "",
        "- `AGENTS.md` — canonical agent contract",
    ]
    for name in ("services", "scripts", "docs", "systemd"):
        if name in top_dirs:
            lines.append(f"- `{name}/` — primary {name} tree")
    for name in top_dirs:
        if name not in {"services", "scripts", "docs", "systemd"}:
            lines.append(f"- `{name}/`")
    slug = str(repo_snapshot.get("target_repo_slug") or ltw.active_contract().get("target_repo_slug") if ltw.active_contract() else "")
    if slug:
        lines.extend(["", f"## Active target", "", f"- target slug: `{slug}`"])
    lines.append("")
    return "\n".join(lines)


def _build_goals_content(*, status_text: str, repo_snapshot: dict[str, Any]) -> str:
    ev = _gather_target_repo_evidence(status_text=status_text, repo_snapshot=repo_snapshot)
    lines = [
        "# Product Goal",
        "",
        f"**{ev['product_name']}** — {ev['product_summary']}",
        "",
        "## Mission",
        "",
        "Deliver a local-first home assistant where a central hub coordinates room-node satellites",
        "for audio, camera, MQTT, and dashboard operations without cloud dependency.",
        "",
        "## Success criteria",
        "",
        "- Hub websocket runtime and D030 field routes run via documented entrypoints (\`scripts/run_aether_hub_ws.sh\`, \`services/hub/\`).",
        "- Room-node diagnostics and ingest paths pass contract tests under \`services/room_node/tests/\`.",
        "- D031 real-hardware burn-in produces timestamped evidence under \`reports/field/\` with honest PASS/FAIL.",
        "- Systemd units (\`systemd/aether-hub.service\`, \`systemd/aether-room-node.service\`) deploy with field-safe restart policy.",
        "",
        "## Current maturity",
        "",
    ]
    if ev.get("status_label"):
        lines.append(f"- Status: {ev['status_label']}")
    if ev.get("focus"):
        lines.append(f"- Current focus: {ev['focus']}")
    lines.extend(
        [
            "- D030 runtime integration adapters and validation scripts are present (README, \`docs/D031_*\`).",
            "- D031 field validation remains blocked until real-hardware probes pass (\`project_status.md\`).",
            "",
            "## Non-goals (near term)",
            "",
            "- Control-plane or proof-marker work inside this product repo.",
            "- Claiming field PASS without measured hardware burn-in evidence.",
            "- Forcing repository layout renames; record existing paths in repo truth files.",
            "",
            "## Architecture entrypoints",
            "",
        ]
    )
    for svc in ev.get("services") or []:
        lines.append(f"- \`services/{svc}/\`")
    for script in ev.get("hub_scripts") or []:
        lines.append(f"- \`scripts/{script}\`")
    for unit in ev.get("systemd_units") or []:
        lines.append(f"- \`systemd/{unit}\`")
    lines.extend(["", f"Derived from live repo evidence ({_now_iso()[:10]}).", ""])
    return "\n".join(lines)


def _build_status_goal_crosslink_content() -> str:
    status_content = _file_text("project_status.md")
    if "project_goals.md" in status_content:
        return status_content
    marker = "# Project Status\n\n"
    insert = "Product goals: see project_goals.md (enriched from live repo evidence).\n\n"
    if status_content.startswith(marker):
        return status_content.replace(marker, marker + insert, 1)
    return insert + status_content


def _goal_driven_target_specs(
    *,
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    if _goals_need_enrichment():
        return []
    specs: list[dict[str, Any]] = []
    if "project_goals.md" not in _file_text("project_status.md"):
        specs.append(
            {
                "work_id": "target_status_goal_crosslink",
                "title": "Cross-link project_status with enriched project_goals",
                "capability": "repo_status_analysis",
                "goal_gap_addressed": "target_status_goal_alignment",
                "task_type": "code_implementation",
                "priority": 1,
                "routing_class": ltw.ROUTING_TARGET,
                "generated_from": "target_backlog_refresh",
                "objective": "Add explicit reference to project_goals.md from project_status.md after goal enrichment.",
                "why_now": "Enriched goals must anchor status updates and future backlog selection.",
                "detect_open": lambda: "project_goals.md" not in _file_text("project_status.md"),
                "target_files": ["project_status.md"],
                "proposed_repo_delta": ["project_status.md"],
                "expected_outputs": ["project_status.md"],
                "execution_steps": [{"type": "write_file", "path": "project_status.md", "content": _build_status_goal_crosslink_content()}],
                "verification_commands": [["grep", "-q", "project_goals.md", "project_status.md"]],
                "done_when": ["command passes: grep -q project_goals.md project_status.md"],
            }
        )
    return specs


def _mission_product_files(repo_snapshot: dict[str, Any]) -> list[str]:
    skip_prefixes = (
        "scripts/", "operator_ui/", "project_memory/", "contracts/",
        "config/", "systemd/purple", "docs/PURPLE_HALO",
    )
    files = [str(f) for f in (repo_snapshot.get("tracked_files") or [])]
    if ltw.is_project_mode():
        root = ltw.product_root()
        for pref in ("services", "apps", "src", "lib", "crates", "anima", "SCRIPTS"):
            base = root / pref
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                if any(rel.startswith(s) for s in skip_prefixes):
                    continue
                if rel.endswith((".py", ".rs", ".js", ".ts", ".tsx", ".sh", ".toml")):
                    files.append(rel)
    out: list[str] = []
    for rel in files:
        if any(rel.startswith(p) for p in skip_prefixes):
            continue
        if rel.endswith((".py", ".rs", ".js", ".ts", ".tsx", ".sh", ".toml", ".yaml", ".yml")):
            out.append(rel)
        elif rel.endswith(".md") and rel not in {"project_goals.md", "project_status.md", "repo_map.md"}:
            out.append(rel)

    def _rank(p: str) -> tuple[int, str]:
        for i, pref in enumerate(("services/", "apps/", "src/", "lib/", "crates/")):
            if p.startswith(pref):
                return (i, p)
        return (9, p)

    out.sort(key=_rank)
    return out[:24]


def _project_mode_product_specs(
    *,
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if _repo_map_needs_refresh():
        content = _build_repo_map_content(repo_snapshot=repo_snapshot)
        specs.append(
            {
                "work_id": "mission_repo_map_entry_points",
                "title": "Populate repo_map entry points from live layout",
                "capability": "repo_status_analysis",
                "goal_gap_addressed": "mission_repo_navigation",
                "task_type": "code_implementation",
                "priority": 2,
                "routing_class": ltw.ROUTING_TARGET,
                "generated_from": "mission_backlog_refresh",
                "objective": "Refresh repo_map.md from the live repository layout.",
                "why_now": "Mission cycles need accurate product navigation.",
                "detect_open": _repo_map_needs_refresh,
                "target_files": ["repo_map.md"],
                "proposed_repo_delta": ["repo_map.md"],
                "expected_outputs": ["repo_map.md"],
                "execution_steps": [{"type": "write_file", "path": "repo_map.md", "content": content}],
                "verification_commands": [["test", "-f", "repo_map.md"]],
                "done_when": ["file exists: repo_map.md"],
            }
        )
    if len(status_text.strip()) < 120:
        specs.append(
            {
                "work_id": "mission_project_status_bootstrap",
                "title": "Bootstrap project_status from repo evidence",
                "capability": "repo_status_analysis",
                "goal_gap_addressed": "mission_status_truth",
                "task_type": "code_implementation",
                "priority": 3,
                "routing_class": ltw.ROUTING_TARGET,
                "generated_from": "mission_backlog_refresh",
                "objective": "Seed project_status.md with live repo summary for mission planning.",
                "why_now": "Status file is thin; cycles need repo truth.",
                "detect_open": lambda: len(_file_text("project_status.md").strip()) < 120,
                "target_files": ["project_status.md"],
                "proposed_repo_delta": ["project_status.md"],
                "expected_outputs": ["project_status.md"],
                "execution_steps": [
                    {
                        "type": "write_file",
                        "path": "project_status.md",
                        "content": "# Project Status\n\n## Current state\n\n- Bootstrapped for purple_halo mission cycles.\n- See repo_map.md and operator mission in project_goals.md.\n",
                    }
                ],
                "verification_commands": [["test", "-f", "project_status.md"]],
                "done_when": ["file exists: project_status.md"],
            }
        )
    return specs


def _project_mode_gap_specs(
    capability_gaps: list[dict[str, Any]],
    *,
    research: dict[str, Any],
    repo_snapshot: dict[str, Any],
    existing_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not capability_gaps:
        return []
    product_files = _mission_product_files(repo_snapshot)
    if not product_files:
        product_files = ["project_status.md"]
    verified_gap_ids = {
        str(i.get("goal_gap_addressed") or "")
        for i in existing_items
        if i.get("status") == "verified"
    }
    existing_ids = {str(i.get("work_id") or "") for i in existing_items}
    specs: list[dict[str, Any]] = []
    for idx, gap in enumerate(sorted(capability_gaps, key=lambda g: int(g.get("priority") or 99))):
        gap_id = str(gap.get("id") or "")
        if not gap_id or gap_id in verified_gap_ids:
            continue
        work_id = f"mission_gap_{gap_id.removeprefix('gap_')}"
        if work_id in existing_ids:
            continue
        target = product_files[idx % len(product_files)]
        specs.append(
            {
                "work_id": work_id,
                "title": f"Mission step: {gap.get('description') or gap_id}",
                "capability": "implementation_dispatch",
                "goal_gap_addressed": gap_id,
                "task_type": "code_implementation",
                "priority": max(1, int(gap.get("priority") or 50)),
                "routing_class": ltw.ROUTING_TARGET,
                "generated_from": "mission_backlog_refresh",
                "objective": str(gap.get("description") or gap_id),
                "why_now": f"Next increment toward operator mission ({gap_id}).",
                "detect_open": lambda _gid=gap_id: True,
                "target_files": [target],
                "proposed_repo_delta": [target],
                "expected_outputs": [target],
                "execution_steps": [],
                "verification_commands": [["test", "-f", target]],
                "done_when": [f"file exists: {target}"],
            }
        )
        if len(specs) >= 3:
            break
    return specs


def _external_target_product_specs(
    *,
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if _repo_map_needs_refresh():
        content = _build_repo_map_content(repo_snapshot=repo_snapshot)
        specs.append(
            {
                "work_id": "target_repo_map_entry_points",
                "title": "Populate repo_map entry points from live layout",
                "capability": "repo_status_analysis",
                "goal_gap_addressed": "target_repo_navigation",
                "task_type": "code_implementation",
                "priority": 2,
                "routing_class": ltw.ROUTING_TARGET,
                "generated_from": "target_backlog_refresh",
                "objective": "Refresh repo_map.md entry points from the live repository layout.",
                "why_now": "Repo map still contains placeholder navigation; target cycles need real product files.",
                "detect_open": _repo_map_needs_refresh,
                "target_files": ["repo_map.md"],
                "proposed_repo_delta": ["repo_map.md"],
                "expected_outputs": ["repo_map.md"],
                "execution_steps": [{"type": "write_file", "path": "repo_map.md", "content": content}],
                "verification_commands": [["grep", "-q", "Entry Points", "repo_map.md"], ["grep", "-q", "services/", "repo_map.md"]],
                "done_when": ["file exists: repo_map.md", "command passes: grep -q services/ repo_map.md"],
            }
        )
    if _goals_need_enrichment():
        content = _build_goals_content(status_text=status_text, repo_snapshot=repo_snapshot)
        specs.append(
            {
                "work_id": "target_project_goals_enrichment",
                "title": "Enrich project_goals.md from live repo evidence",
                "capability": "goal_ingestion",
                "goal_gap_addressed": "target_goal_truth",
                "task_type": "code_implementation",
                "priority": 1,
                "routing_class": ltw.ROUTING_TARGET,
                "generated_from": "target_backlog_refresh",
                "objective": "Replace bootstrap goal stub with repo-specific mission, success criteria, and entrypoints.",
                "why_now": "Target goals file is still a bootstrap placeholder.",
                "detect_open": _goals_need_enrichment,
                "target_files": ["project_goals.md"],
                "proposed_repo_delta": ["project_goals.md"],
                "expected_outputs": ["project_goals.md"],
                "execution_steps": [{"type": "write_file", "path": "project_goals.md", "content": content}],
                "verification_commands": [
                    ["grep", "-qv", "Define the product goal for this repository.", "project_goals.md"],
                    ["grep", "-q", "Success criteria", "project_goals.md"],
                    ["grep", "-q", "services/hub", "project_goals.md"],
                ],
                "done_when": [
                    "file exists: project_goals.md",
                    "command passes: grep -q Success criteria project_goals.md",
                ],
            }
        )
    specs.extend(
        _goal_driven_target_specs(goal_text=goal_text, status_text=status_text, repo_snapshot=repo_snapshot)
    )
    return specs


def _target_bootstrap_spec() -> dict[str, Any] | None:
    if not ltw.is_target_active():
        return None
    if (ltw.runtime_root() / "target_bootstrap.json").is_file():
        return None
    inspection = ltw.inspect_target_repo()
    if inspection.get("truth_complete") and inspection.get("runtime_root_exists"):
        return None
    return {
        "work_id": "target_repo_bootstrap",
        "title": "Bootstrap target repo truth files and runtime",
        "capability": "target_onboarding",
        "goal_gap_addressed": "target_workspace_ready",
        "task_type": "code_implementation",
        "priority": 1,
        "routing_class": ltw.ROUTING_TARGET,
        "objective": "Create project_goals.md, project_status.md, repo_map.md, and target runtime root.",
        "why_now": "Target workspace is active but truth files or runtime layout are incomplete.",
        "detect_open": lambda: True,
        "target_files": ["project_goals.md", "project_status.md", "repo_map.md"],
        "proposed_repo_delta": ["project_goals.md", "project_status.md", "repo_map.md"],
        "expected_outputs": ["project_goals.md", "project_status.md", "repo_map.md"],
        "execution_steps": [
            {"type": "run_command", "command": ["python3", "scripts/loop_target_workspace.py", "--bootstrap"]},
        ],
        "verification_commands": [["python3", "scripts/loop_target_workspace.py", "--show"]],
        "done_when": [
            "file exists: project_goals.md",
            "file exists: project_status.md",
            "file exists: repo_map.md",
        ],
    }


def _apply_target_routing(fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routed: list[dict[str, Any]] = []
    for item in fresh:
        tagged = ltw.tag_item_routing(item)
        if ltw.is_target_active() and tagged.get("routing_class") == ltw.ROUTING_CONTROL:
            if tagged.get("status") != "open":
                continue
            tagged = dict(tagged)
            tagged["priority"] = int(tagged.get("priority") or 99) + 1000
        routed.append(tagged)
    return routed


def refresh_backlog(
    *,
    capability_gaps: list[dict[str, Any]],
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
    state: dict[str, Any],
    research: dict[str, Any],
    continuity_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backlog = load_backlog()
    goal_model_meta: dict[str, Any] = {}
    if not is_external_target_active():
        from loop_artifact_inputs import ensure_goal_model

        _goal_model, goal_model_meta = ensure_goal_model(goal_text)
        backlog["goal_model_input"] = {
            "source": goal_model_meta.get("source") or "goal_model.json",
            "freshness_status": (goal_model_meta.get("freshness") or {}).get("status"),
            "fresh": (goal_model_meta.get("freshness") or {}).get("fresh"),
            "used_stale": goal_model_meta.get("used_stale", False),
            "regenerated": goal_model_meta.get("regenerated", False),
            "last_refreshed_at": _goal_model.get("last_refreshed_at"),
            "source_hash": _goal_model.get("source_hash"),
        }
    if ltw.is_project_mode():
        raw_specs = [
            _spec_to_item(spec, research=research)
            for spec in _project_mode_product_specs(
                goal_text=goal_text, status_text=status_text, repo_snapshot=repo_snapshot
            )
        ]
        gap_specs = _project_mode_gap_specs(
            capability_gaps,
            research=research,
            repo_snapshot=repo_snapshot,
            existing_items=backlog.get("product_work_items") or [],
        )
        raw_specs.extend(_spec_to_item(spec, research=research) for spec in gap_specs)
        rejections: list[str] = []
        fresh, rejections = _apply_quality_rules(raw_specs, goal_underspecified=False)
    elif is_external_target_active():
        raw_specs = [
            _spec_to_item(spec, research=research)
            for spec in _external_target_product_specs(
                goal_text=goal_text, status_text=status_text, repo_snapshot=repo_snapshot
            )
        ]
        rejections: list[str] = []
        fresh = _derive_product_items(
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot=repo_snapshot,
            state=state,
            research=research,
        )
    else:
        raw_specs = [_spec_to_item(spec, research=research) for spec in _product_capability_catalog()]
        _, rejections = _apply_quality_rules(raw_specs, goal_underspecified=_goal_underspecified(goal_text))
        fresh = _derive_product_items(
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot=repo_snapshot,
            state=state,
            research=research,
        )
        if not fresh and not _goal_underspecified(goal_text):
            fresh = raw_specs
            fresh, _ = _apply_quality_rules(fresh, goal_underspecified=False)
    bootstrap_spec = _target_bootstrap_spec()
    if bootstrap_spec:
        fresh.insert(0, _spec_to_item(bootstrap_spec, research=research))
    from loop_artifact_inputs import apply_artifact_scoring

    fresh = apply_artifact_scoring(fresh, goal_text=goal_text, research=research)
    fresh = _apply_target_routing(fresh)
    if not capability_gaps and not is_external_target_active():
        from loop_open_gaps_state import gaps_for_planning

        hydrated, ogs_meta = gaps_for_planning(
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot=repo_snapshot,
            state=state,
            research=research,
            allow_stale=True,
        )
        if hydrated:
            capability_gaps = [g for g in hydrated if str(g.get("id", "")).startswith("gap_")]
            backlog["open_gaps_input_meta"] = ogs_meta
    backlog["capability_gaps"] = capability_gaps
    existing = backlog.get("product_work_items") or []
    if is_external_target_active() and not ltw.force_proof_mode() and ltw.target_proof_satisfied():
        existing = [
            i
            for i in existing
            if not is_proof_work_item(i)
            and (
                i.get("generated_from") == "target_backlog_refresh"
                or i.get("status") == "verified"
                or str(i.get("work_id") or "").startswith("target_")
            )
        ]
    backlog["product_work_items"] = _merge_items(existing, fresh)
    backlog["research_summary"] = str(research.get("summary") or "")
    backlog["quality_rejections"] = rejections
    backlog["backlog_health"] = backlog_health(backlog)
    backlog["version"] = 2
    open_list = open_items(backlog)
    if open_list:
        executable = [i for i in open_list if i.get("task_type") in EXECUTABLE_TYPES]
        if not executable and not capability_gaps:
            backlog["empty_reason"] = "no_executable_product_work"
    elif not capability_gaps:
        backlog["empty_reason"] = classify_empty(
            goal_text=goal_text,
            research=research,
            capability_gaps=capability_gaps,
            backlog=backlog,
        )
    if not is_external_target_active() and not ltw.is_project_mode():
        backlog = _seed_self_loop_gap_work(backlog, capability_gaps=capability_gaps, research=research)
        open_list = open_items(backlog)
        if capability_gaps and not open_list:
            backlog["empty_reason"] = "implementation_blocked"
        elif capability_gaps and open_list:
            backlog["empty_reason"] = ""
    backlog = ensure_open_worker_capability(backlog, research=research)
    if is_external_target_active() and not _goals_need_enrichment():
        for item in backlog.get("product_work_items") or []:
            if str(item.get("work_id")) == "target_project_goals_enrichment" and item.get("status") == "open":
                item["status"] = "verified"
                item["verified_at"] = _now_iso()
                item.pop("failure_reason", None)
    save_backlog(backlog)
    return backlog


def open_items(backlog: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        i
        for i in backlog.get("product_work_items") or []
        if i.get("status") == "open" and not str(i.get("blocked_by") or "").strip()
    ]


def pick_next_item(backlog: dict[str, Any], continuity_meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    items = open_items(backlog)
    if not items:
        return None
    executable = [i for i in items if i.get("task_type") in EXECUTABLE_TYPES]
    pool = executable or items
    if is_external_target_active() or ltw.is_project_mode():
        pool = [
            i
            for i in pool
            if ltw.classify_work_item(i) == ltw.ROUTING_TARGET
            and not is_proof_work_item(i)
            and not all(ltw.is_control_plane_path(str(p)) for p in (i.get("target_files") or i.get("proposed_repo_delta") or [""]))
        ]
    else:
        pool = [
            i
            for i in pool
            if not str(i.get("work_id") or "").startswith("target_")
            and not is_proof_work_item(i)
            and i.get("generated_from") != "target_backlog_refresh"
        ]
    if not proof_selection_allowed():
        pool = [i for i in pool if not is_proof_work_item(i)]
    if not pool:
        return None
    if worker_bridge_validated():
        real = [i for i in pool if is_worker_backed_code_item(i)]
        if real:
            pool = real
    meaningful = [i for i in pool if is_meaningful_product_item(i)]
    if ltw.is_project_mode() and meaningful:
        if continuity_meta and continuity_meta.get("resumed_prior_intent"):
            from loop_continuity_state import prefer_continuity_work_item
            preferred = prefer_continuity_work_item(meaningful, continuity_meta)
            if preferred:
                return preferred
        return sorted(meaningful, key=ltw.routing_sort_key)[0]
    if meaningful:
        try:
            from loop_autonomous import evaluate_product_complete, live_soak_active
            from loop_production_ops import production_ops_active
            assessment = evaluate_product_complete()
            progress = assessment.get("progress") or {}
            # Production hold: repair-class only. Goal-delivery: criterion-linked only.
            try:
                from loop_production_hold import HOLD_WORK_CLASSES, production_hold_active
                hold_on = production_hold_active()
            except Exception:
                hold_on = False
                HOLD_WORK_CLASSES = frozenset()
            if hold_on:
                pool = [
                    i for i in meaningful
                    if str(i.get("generated_from") or "") == "production_hold_repair"
                    or str(i.get("hold_work_class") or "") in HOLD_WORK_CLASSES
                    or str(i.get("work_id") or "").startswith("repair_")
                ]
            elif production_ops_active():
                linked = [
                    i for i in meaningful
                    if i.get("success_criterion_id")
                    or str(i.get("generated_from") or "") in {"goal_delivery", "goal_delivery_linked_improve"}
                    or str(i.get("work_id") or "").startswith("deliver_")
                ]
                deliver = [i for i in linked if str(i.get("work_id") or "").startswith("deliver_")]
                pool = deliver or linked
            # Soak / operational validation: freeze to operational_* priorities only.
            elif live_soak_active() or (
                assessment.get("mechanics_complete") and not assessment.get("operationally_realized")
            ):
                ops = [i for i in meaningful if str(i.get("work_id") or "").startswith("operational_")]
                pool = ops or meaningful
            else:
                next_cap = str(progress.get("next_missing_capability") or "")
                partial = set(progress.get("partial") or [])
                end_goal = [i for i in meaningful if is_end_goal_capability_item(i)]
                if next_cap:
                    focused = [i for i in end_goal if str(i.get("capability") or "") == next_cap]
                    pool = focused or [i for i in end_goal if str(i.get("capability") or "") in partial] or end_goal or meaningful
                else:
                    pool = end_goal or meaningful
        except Exception:
            pool = meaningful
    else:
        pool = [i for i in pool if not is_bookkeeping_item(i) and not is_proof_revalidation_item(i)]
        if not pool:
            return None
    if continuity_meta and continuity_meta.get("resumed_prior_intent"):
        from loop_continuity_state import prefer_continuity_work_item

        preferred = prefer_continuity_work_item(pool, continuity_meta)
        if preferred and is_meaningful_product_item(preferred):
            return preferred
    return sorted(pool, key=ltw.routing_sort_key)[0]


def ensure_open_worker_capability(backlog: dict[str, Any], *, research: dict[str, Any]) -> dict[str, Any]:
    """Keep at least one open worker-backed code_implementation item when not blocked/complete."""
    if ltw.is_project_mode():
        if any(is_worker_backed_code_item(i) for i in open_items(backlog)):
            return backlog
        from purple_halo_loop import repo_snapshot as _repo_snapshot
        gap_specs = _project_mode_gap_specs(
            backlog.get("capability_gaps") or [],
            research=research,
            repo_snapshot=_repo_snapshot(),
            existing_items=backlog.get("product_work_items") or [],
        )
        for spec in gap_specs:
            item = _spec_to_item(spec, research=research)
            item["status"] = "open"
            wid = str(item.get("work_id") or "")
            existing_ids = {str(i.get("work_id")) for i in backlog.get("product_work_items") or []}
            if wid not in existing_ids:
                backlog.setdefault("product_work_items", []).append(item)
            backlog["updated_at"] = _now_iso()
            break
        return backlog
    if is_external_target_active():
        return backlog
    if backlog.get("empty_reason") in {"product_complete", "goal_underspecified", "repo_blocked"}:
        return backlog
    if any(is_worker_backed_code_item(i) for i in open_items(backlog)):
        return backlog
    catalog = _real_product_runtime_specs() + _self_loop_integration_specs()
    for spec in catalog:
        detect = spec.get("detect_open")
        if callable(detect) and detect():
            item = _spec_to_item(spec, research=research)
            item["status"] = "open"
            existing = {str(i.get("work_id")) for i in backlog.get("product_work_items") or []}
            if item["work_id"] not in existing:
                backlog.setdefault("product_work_items", []).append(item)
            else:
                for i in backlog.get("product_work_items") or []:
                    if str(i.get("work_id")) == item["work_id"] and i.get("status") != "verified":
                        i["status"] = "open"
            backlog["updated_at"] = _now_iso()
            break
    if not any(is_worker_backed_code_item(i) for i in open_items(backlog)):
        gap_specs = _gap_driven_product_specs(
            backlog.get("capability_gaps") or [],
            research=research,
            existing_items=backlog.get("product_work_items") or [],
        )
        for spec in gap_specs:
            item = _spec_to_item(spec, research=research)
            item["status"] = "open"
            wid = str(item.get("work_id") or "")
            existing_ids = {str(i.get("work_id")) for i in backlog.get("product_work_items") or []}
            if wid not in existing_ids:
                backlog.setdefault("product_work_items", []).append(item)
            backlog["updated_at"] = _now_iso()
            break
    return backlog


def classify_empty(
    *,
    goal_text: str,
    research: dict[str, Any],
    capability_gaps: list[dict[str, Any]],
    backlog: dict[str, Any],
) -> str:
    if _goal_underspecified(goal_text):
        return "goal_underspecified"
    if not research.get("summary") and not research.get("goal_gap_addressed"):
        return "research_missing"
    blocked = [i for i in backlog.get("product_work_items") or [] if i.get("status") == "open" and i.get("blocked_by")]
    if blocked and not open_items(backlog):
        return "implementation_blocked"
    if capability_gaps:
        return "implementation_blocked"
    open_list = open_items(backlog)
    if open_list:
        executable = [i for i in open_list if i.get("task_type") in EXECUTABLE_TYPES]
        if not executable:
            return "no_executable_product_work"
        meaningful = [i for i in open_list if is_meaningful_product_item(i)]
        if not meaningful:
            return "no_meaningful_product_step"
    if not open_list:
        return "product_complete"
    return "product_complete"


def mark_item_status(
    backlog: dict[str, Any],
    work_id: str,
    status: str,
    *,
    failure_reason: str = "",
    cycle_id: int | None = None,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    for item in backlog.get("product_work_items") or []:
        if str(item.get("work_id")) == work_id:
            item["status"] = status
            if failure_reason:
                item["failure_reason"] = failure_reason
            if status == "verified":
                item["verified_at"] = _now_iso()
                item["failure_reason"] = ""
            if status == "open" and failure_reason:
                item["blocked_by"] = failure_reason[:200]
            if cycle_id is not None:
                item["cycle_id"] = cycle_id
            break
    backlog["empty_reason"] = ""
    backlog["backlog_health"] = backlog_health(backlog)
    save_backlog(backlog)
    return backlog


def work_item_to_plan(item: dict[str, Any], *, cycle_id: int, research: dict[str, Any]) -> dict[str, Any]:
    wid = str(item["work_id"])
    research_fact = item.get("research_fact") or research.get("summary") or item.get("objective") or ""
    steps = list(item.get("execution_steps") or item.get("actions") or [])
    delta = list(item.get("proposed_repo_delta") or item.get("expected_repo_delta") or [])
    vcmds = [list(c) for c in item.get("verification_commands") or []]
    done_when = list(item.get("done_when") or [])
    dispatch_target = item.get("dispatch_target") or ""
    plan: dict[str, Any] = {
        "id": wid,
        "plan_id": wid,
        "work_id": wid,
        "backlog_work_id": wid,
        "task_type": item["task_type"],
        "focus": item["title"],
        "description": item.get("objective") or item.get("description") or item["title"],
        "why_this_step_now": item.get("why_now") or item.get("selection_rationale") or f"Top open backlog item: {item['title']}",
        "goal_gap_addressed": item.get("goal_gap_addressed") or item.get("success_criterion_id") or "",
        "success_criterion_id": item.get("success_criterion_id") or item.get("goal_gap_addressed") or "",
        "success_criterion_text": item.get("success_criterion_text") or "",
        "evidence_will_move": item.get("evidence_will_move") or "",
        "capability": item.get("capability") or "",
        "local_only": bool(item.get("local_only") or wid.startswith("product_gap_") or wid.startswith("operational_") or wid.startswith("improve_") or wid.startswith("deliver_") or wid.startswith("repair_") or wid == "product_cycle_closure"),
        "dispatch_target": dispatch_target,
        "expected_outputs": list(item.get("expected_outputs") or []),
        "handler_inputs": dict(item.get("handler_inputs") or {}),
        "expected_repo_delta": delta,
        "proposed_repo_delta": delta,
        "target_files": list(item.get("target_files") or []),
        "verification_commands": vcmds,
        "done_when": done_when,
        "resume_reason": f"Consuming backlog item {wid}",
        "actions": steps,
        "execution_steps": steps,
        "success_criteria": [f"{p} exists" for p in delta],
        "next_focus_after": f"Continue backlog after {wid}",
        "research_summary": research_fact,
        "goal_inputs": dict(item.get("goal_inputs") or {}),
        "research_inputs": dict(item.get("research_inputs") or {}),
        "verification_basis": dict(item.get("verification_basis") or {}),
        "selection_rationale": item.get("selection_rationale") or "",
        "evidence_backed": bool(item.get("evidence_backed")),
    }
    if item.get("generated_from"):
        plan["generated_from"] = item["generated_from"]
    if item.get("hold_work_class"):
        plan["hold_work_class"] = item["hold_work_class"]
    if item.get("force_worker_bridge"):
        plan["force_worker_bridge"] = True
    return plan


def attach_work_package(
    plan: dict[str, Any],
    *,
    cycle_id: int,
    research: dict[str, Any],
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
) -> dict[str, Any]:
    from loop_work_package import build_work_package, package_to_plan_fields

    wid = plan.get("backlog_work_id") or plan.get("work_id")
    if not wid:
        return plan
    bounded = plan.get("bounded_step") or {}
    steps = list(
        plan.get("execution_steps")
        or bounded.get("execution_steps")
        or plan.get("actions")
        or bounded.get("actions")
        or []
    )
    item = {
        "work_id": wid,
        "title": plan.get("focus") or plan.get("description") or wid,
        "task_type": plan.get("task_type"),
        "goal_gap_addressed": plan.get("goal_gap_addressed"),
        "capability": plan.get("capability") or "",
        "objective": plan.get("description") or plan.get("focus"),
        "why_now": plan.get("why_this_step_now"),
        "dispatch_target": plan.get("dispatch_target") or bounded.get("dispatch_target") or "",
        "expected_outputs": list(plan.get("expected_outputs") or bounded.get("expected_outputs") or []),
        "handler_inputs": dict(plan.get("handler_inputs") or bounded.get("handler_inputs") or {}),
        "target_files": plan.get("target_files") or plan.get("expected_repo_delta") or [],
        "proposed_repo_delta": plan.get("proposed_repo_delta") or plan.get("expected_repo_delta") or [],
        "execution_steps": steps,
        "verification_commands": plan.get("verification_commands") or bounded.get("verification_commands") or [],
        "done_when": plan.get("done_when") or bounded.get("done_when") or [],
        "goal_inputs": plan.get("goal_inputs") or {},
        "research_inputs": plan.get("research_inputs") or {},
        "verification_basis": plan.get("verification_basis") or {},
        "selection_rationale": plan.get("selection_rationale") or plan.get("why_this_step_now") or "",
        "evidence_backed": plan.get("evidence_backed", False),
        "goal_inputs": dict(plan.get("goal_inputs") or {}),
        "research_inputs": dict(plan.get("research_inputs") or {}),
        "verification_basis": dict(plan.get("verification_basis") or {}),
        "selection_rationale": plan.get("selection_rationale") or plan.get("why_this_step_now") or "",
        "local_only": bool(plan.get("local_only")),
        "generated_from": plan.get("generated_from") or "",
        "hold_work_class": plan.get("hold_work_class") or "",
    }
    if plan.get("force_worker_bridge") or bounded.get("force_worker_bridge"):
        item["force_worker_bridge"] = True
    package = build_work_package(
        item,
        cycle_id=cycle_id,
        research=research,
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=repo_snapshot,
    )
    fields = package_to_plan_fields(package)
    merged = {**plan, **fields}
    merged["work_package"] = package
    merged["cycle_id"] = cycle_id
    merged["local_only"] = bool(package.get("local_only") or plan.get("local_only"))
    merged["dispatch_target"] = package.get("dispatch_target") or plan.get("dispatch_target") or ""
    return merged


def backlog_summary(backlog: dict[str, Any] | None = None) -> dict[str, Any]:
    backlog = backlog or load_backlog()
    items = backlog.get("product_work_items") or []
    open_list = open_items(backlog)
    in_progress = next((i for i in items if i.get("status") == "in_progress"), None)
    verified = [i for i in items if i.get("status") == "verified"]
    last_verified = max(verified, key=lambda i: i.get("verified_at") or "", default=None)
    blocked = [i for i in items if i.get("status") == "open" and i.get("blocked_by")]
    health = backlog_health(backlog)
    return {
        "open_count": len(open_list),
        "total_items": len(items),
        "current_in_progress": in_progress,
        "last_verified": last_verified,
        "blocked_items": blocked,
        "capability_gap_count": len(backlog.get("capability_gaps") or []),
        "empty_reason": backlog.get("empty_reason") or "",
        "backlog_health": health,
        "updated_at": backlog.get("updated_at"),
    }


def update_from_verification(
    *,
    plan: dict[str, Any],
    verification: dict[str, Any],
    cycle_id: int,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wid = plan.get("backlog_work_id") or plan.get("work_id")
    if not wid:
        return load_backlog()
    backlog = load_backlog()
    worker_outcome = verification.get("worker_outcome_class") or (execution or {}).get("worker_outcome_class")
    package_verified = verification.get("work_package_verified", verification.get("passed"))
    local_only = bool(plan.get("local_only") or (execution or {}).get("local_only"))
    # Keep operational validation / production self-improvement items open while active.
    keep_operational_open = False
    if str(wid).startswith("operational_"):
        try:
            from loop_autonomous import evaluate_product_complete, live_soak_active
            keep_operational_open = (
                not bool(evaluate_product_complete().get("operationally_realized"))
                or live_soak_active()
            )
        except Exception:
            keep_operational_open = True
    if str(wid).startswith("improve_") or str(wid).startswith("deliver_") or str(wid).startswith("repair_"):
        try:
            from loop_production_ops import production_ops_active
            from loop_goal_delivery import goal_delivery_active
            keep_operational_open = production_ops_active() or goal_delivery_active() or keep_operational_open
        except Exception:
            keep_operational_open = True
    if verification.get("passed") and (
        worker_outcome == "verified_complete"
        or local_only
        or package_verified
    ) and not keep_operational_open:
        mark_item_status(backlog, str(wid), "verified", cycle_id=cycle_id)
    elif verification.get("passed") and keep_operational_open:
        # record attempt but leave open for sustained validation window
        for item in backlog.get("product_work_items") or []:
            if str(item.get("work_id")) == str(wid):
                item["last_validation_cycle"] = cycle_id
                item["status"] = "open"
                item.pop("blocked_by", None)
                item.pop("failure_reason", None)
        save_backlog(backlog)
        return backlog
        for item in backlog.get("product_work_items") or []:
            if str(item.get("work_id")) == str(wid):
                item["worker_outcome"] = worker_outcome or ("local_verified" if local_only else "")
    elif worker_outcome == "verified_partial":
        mark_item_status(backlog, str(wid), "open", failure_reason="verified_partial", cycle_id=cycle_id)
        for item in backlog.get("product_work_items") or []:
            if str(item.get("work_id")) == str(wid):
                item["worker_outcome"] = worker_outcome
                item["missing_outputs"] = (execution or {}).get("worker_result", {}).get("missing_outputs") or []
        save_backlog(backlog)
    elif worker_outcome == "verification_failed":
        mark_item_status(backlog, str(wid), "open", failure_reason="verification_failed", cycle_id=cycle_id)
        for item in backlog.get("product_work_items") or []:
            if str(item.get("work_id")) == str(wid):
                item["worker_outcome"] = worker_outcome
                item["failure_evidence"] = (execution or {}).get("worker_result", {}).get("failure_evidence") or []
        save_backlog(backlog)
    elif package_verified and verification.get("passed"):
        mark_item_status(backlog, str(wid), "verified", cycle_id=cycle_id)
    else:
        reason = worker_outcome or "; ".join(
            c.get("detail") or c.get("criterion", "")
            for c in verification.get("checks") or []
            if not c.get("passed")
        )[:300] or "verification failed"
        mark_item_status(backlog, str(wid), "open", failure_reason=reason, cycle_id=cycle_id)
    return load_backlog()


def mark_in_progress(work_id: str, cycle_id: int) -> dict[str, Any]:
    backlog = load_backlog()
    mark_item_status(backlog, work_id, "in_progress", cycle_id=cycle_id)
    return load_backlog()


def self_check() -> None:
    specs = _product_capability_catalog()
    assert len(specs) >= 10, len(specs)
    runtime = _real_product_runtime_specs()
    assert len(runtime) == 5, len(runtime)
    assert {s["work_id"] for s in runtime} == REAL_PRODUCT_CAPABILITIES
    code_verify = [s for s in specs if s["task_type"] in EXECUTABLE_TYPES]
    assert len(code_verify) >= 2, len(code_verify)

    vague = {"work_id": "vague", "title": "x", "task_type": "docs_update", "target_files": ["project_learning/a.md"]}
    kept, rej = _apply_quality_rules([vague], goal_underspecified=False)
    assert not kept and rej

    backlog = refresh_backlog(
        capability_gaps=[],
        goal_text=_file_text("project_goals.md"),
        status_text=_file_text("project_status.md"),
        repo_snapshot={},
        state={},
        research={"summary": "test", "goal_gap_addressed": "capability_plan_generation", "capability_area": "plan_generation"},
    )
    items = backlog.get("product_work_items") or []
    assert len(items) >= 5, len(items)
    cv = [i for i in items if i.get("task_type") in EXECUTABLE_TYPES]
    assert len(cv) >= 2, len(cv)
    with_dispatch = [i for i in items if i.get("dispatch_target")]
    assert len(with_dispatch) >= 3, len(with_dispatch)
    nxt = pick_next_item(backlog)
    if nxt:
        plan = work_item_to_plan(nxt, cycle_id=1, research={"summary": "test", "capability_area": "plan"})
        assert plan["backlog_work_id"]
        assert plan.get("execution_steps") or plan.get("actions") or plan.get("dispatch_target")
        assert plan.get("done_when")
    summary = backlog_summary(backlog)
    assert summary["backlog_health"]["total_items"] >= 5
    print("loop-backlog: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo goal backlog")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.show:
        print(json.dumps({"backlog": load_backlog(), "summary": backlog_summary()}, indent=2))
        return 0
    if args.refresh:
        from loop_open_gaps_state import gaps_for_planning
        from loop_state import load_state

        goal_text = _file_text("project_goals.md")
        status_text = _file_text("project_status.md")
        state_payload = load_state()
        control_state = state_payload.get("control_state") or state_payload
        gaps, _gaps_meta = gaps_for_planning(
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot={},
            state=control_state,
            research={},
            regenerate=True,
        )
        backlog = refresh_backlog(
            capability_gaps=gaps,
            goal_text=goal_text,
            status_text=status_text,
            repo_snapshot={},
            state=control_state,
            research={},
        )
        print(json.dumps(backlog_summary(backlog), indent=2))
        return 0
    parser.error("specify --self-check, --show, or --refresh")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
