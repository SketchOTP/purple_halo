#!/usr/bin/env python3
"""First-class session bootstrap and cross-machine resume operations."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

from governance_tier import CURSOR_SESSION_ENTRYPOINT, merge_governance_observability
from hook_runner import run_event, utc_now
from pack_quality import assess_pack_quality, merge_pack_quality_into_context
from resume_reader import fetch_resume
from trace_writer import load_trace


ROOT = Path(__file__).resolve().parents[1]


def _load_build_plan():
    module_path = ROOT / "scripts" / "select-context.py"
    spec = importlib.util.spec_from_file_location("agent_select_context", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load select-context from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_plan


_BUILD_PLAN = _load_build_plan()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def navigation_fail_closed_default() -> bool:
    """Fail closed when navigation is required but pack_ids are missing (default: true)."""
    return _env_flag("AGENT_NAVIGATION_FAIL_CLOSED", default=True)


def refresh_session_navigation(
    session_state: dict,
    *,
    task: str | None = None,
) -> dict:
    """Index + hybrid search via Mimir when navigation is required but pack_ids are empty."""
    from mimir_code_nav import mimir_available, navigate_for_task

    session = dict(session_state)
    plan = dict(session.get("context_plan") or {})
    if not plan.get("navigation_required"):
        return session

    pack_ids = list(plan.get("code_navigation_pack_ids") or [])
    nav_payload = dict(plan.get("code_navigation") or {})
    for pid in nav_payload.get("pack_ids") or []:
        if pid and pid not in pack_ids:
            pack_ids.append(pid)

    if pack_ids:
        plan["code_navigation_pack_ids"] = pack_ids
        nav_payload = dict(plan.get("code_navigation") or {})
        if nav_payload and not plan.get("pack_quality"):
            quality = assess_pack_quality(task=str(task or plan.get("task") or ""), nav_payload=nav_payload)
            plan["pack_quality"] = quality
            plan["code_navigation"] = merge_pack_quality_into_context(nav_payload, quality)
        session["context_plan"] = plan
        return session

    if not mimir_available():
        return session

    resolved_task = str(task or plan.get("task") or session.get("task") or "governed session")
    nav = navigate_for_task(
        task=resolved_task,
        repo_path=str(ROOT),
        repo_name=ROOT.name,
        project=session.get("project"),
        trace_id=session.get("trace_id"),
        session_id=session.get("session_id"),
    )
    quality = assess_pack_quality(task=resolved_task, nav_payload=nav)
    nav = merge_pack_quality_into_context(nav, quality)
    new_pack_ids = list(nav.get("pack_ids") or [])
    for pid in new_pack_ids:
        if pid and pid not in pack_ids:
            pack_ids.append(pid)
    plan["code_navigation"] = nav
    plan["code_navigation_pack_ids"] = pack_ids
    plan["pack_quality"] = nav.get("pack_quality")
    session["context_plan"] = plan
    return session


def _context_plan_for_session(
    *,
    task: str,
    route: str,
    project: str | None,
    trace_id: str,
    session_id: str,
    resume_id: str,
) -> dict:
    plan = _BUILD_PLAN(
        task,
        None,
        repo_path=str(ROOT),
        repo_name=ROOT.name,
        project=project,
        trace_id=trace_id,
        session_id=session_id,
        resume_id=resume_id,
    )
    if route:
        plan["requested_route"] = route
    return plan


def _get_json(url: str, api_key: str | None) -> dict:
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _bundle_from_mimir(trace_id: str) -> dict | None:
    endpoint = os.environ.get("MIMIR_ENDPOINT", "").rstrip("/")
    if not endpoint:
        return None
    try:
        return _get_json(
            f"{endpoint}/api/governance/trace-bundle/{trace_id}",
            os.environ.get("MIMIR_API_KEY"),
        )
    except Exception:
        return None


def _bundle_local(trace_id: str) -> dict | None:
    trace = load_trace(trace_id)
    if trace is None:
        return None
    return {"ok": True, "trace": trace}


def navigation_guard(
    context_plan: dict,
    *,
    fail_closed_navigation: bool | None = None,
) -> dict:
    navigation_required = bool(context_plan.get("navigation_required"))
    pack_ids = [str(item) for item in (context_plan.get("code_navigation_pack_ids") or []) if str(item).strip()]
    code_navigation = dict(context_plan.get("code_navigation") or {})
    mimir_is_available = bool(code_navigation.get("enabled")) or bool(os.environ.get("MIMIR_ENDPOINT", "").rstrip("/"))
    should_fail_closed = (
        navigation_fail_closed_default()
        if fail_closed_navigation is None
        else bool(fail_closed_navigation)
    )
    blocked = bool(should_fail_closed and navigation_required and not pack_ids)
    reason = None
    if blocked:
        reason = "navigation_required_but_mimir_unavailable"
        if mimir_is_available:
            reason = "navigation_required_but_no_pack_ids"
    return {
        "navigation_required": navigation_required,
        "code_navigation_pack_ids": pack_ids,
        "mimir_available": mimir_is_available,
        "fail_closed_navigation": should_fail_closed,
        "blocked": blocked,
        "reason": reason,
    }


def start_session(
    *,
    task: str,
    route: str,
    project: str | None = None,
    run_kind: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    resume_id: str | None = None,
    lineage: dict | None = None,
    fail_closed_navigation: bool | None = None,
) -> dict:
    resolved_trace_id = trace_id or f"trace:{uuid.uuid4().hex[:12]}"
    resolved_session_id = session_id or f"session:{uuid.uuid4().hex[:12]}"
    resolved_resume_id = resume_id or f"resume:{resolved_trace_id}"
    context_plan = _context_plan_for_session(
        task=task,
        route=route,
        project=project,
        trace_id=resolved_trace_id,
        session_id=resolved_session_id,
        resume_id=resolved_resume_id,
    )
    session_state = refresh_session_navigation(
        {
            "trace_id": resolved_trace_id,
            "session_id": resolved_session_id,
            "project": project,
            "task": task,
            "entrypoint": CURSOR_SESSION_ENTRYPOINT,
            "context_plan": context_plan,
        },
        task=task,
    )
    context_plan = dict(session_state.get("context_plan") or context_plan)
    if run_kind:
        context_plan["run_kind"] = run_kind
    nav_guard = navigation_guard(context_plan, fail_closed_navigation=fail_closed_navigation)
    effective_route = str(context_plan.get("route") or route)
    session_details = merge_governance_observability(
        {
            "entrypoint": session_state.get("entrypoint") or CURSOR_SESSION_ENTRYPOINT,
            "task": task,
            "route": effective_route,
            "resume_id": resolved_resume_id,
            "lineage": lineage or {"lineage_id": resolved_trace_id, "resume_depth": 0},
            "context_plan": context_plan,
            "loaded_mcps": list(context_plan.get("mcp_preflight") or []),
            "navigation_required": nav_guard["navigation_required"],
            "code_navigation_pack_ids": nav_guard["code_navigation_pack_ids"],
            "route_evidence": list(context_plan.get("mcp_actions") or []),
            "mimir_available": nav_guard["mimir_available"],
            "fail_closed_navigation": nav_guard["fail_closed_navigation"],
            "blocked_reason": nav_guard["reason"],
            "navigation_blocked": nav_guard["blocked"],
            "pack_quality": context_plan.get("pack_quality"),
            "pack_quality_state": (context_plan.get("pack_quality") or {}).get("pack_quality_state"),
            **({"run_kind": run_kind} if run_kind else {}),
        }
    )
    payload = {
        "event_id": f"{resolved_trace_id}:session_start",
        "hook": "session_start",
        "trace_id": resolved_trace_id,
        "status": "deny" if nav_guard["blocked"] else "observe",
        "timestamp": utc_now(),
        "project": project,
        "session_id": resolved_session_id,
        "details": session_details,
    }
    result = run_event(payload)
    return {
        "ok": not nav_guard["blocked"],
        "trace_id": resolved_trace_id,
        "session_id": resolved_session_id,
        "resume_id": result.get("resume", {}).get("resume_id"),
        "trace_status": result.get("trace", {}).get("status"),
        "route": effective_route,
        "context_plan": context_plan,
        "navigation_guard": nav_guard,
        "error": nav_guard["reason"],
    }


def resume_session(
    *,
    resume_id: str | None = None,
    trace_id: str | None = None,
    continue_as_new_trace: bool = True,
) -> dict:
    checkpoint = fetch_resume(resume_id=resume_id, trace_id=trace_id)
    resolved_trace_id = str(trace_id or checkpoint.get("trace_id") or "")
    if not resolved_trace_id:
        raise ValueError("resume checkpoint missing trace_id")
    bundle = _bundle_from_mimir(resolved_trace_id) or _bundle_local(resolved_trace_id) or {}
    trace = bundle.get("trace") or {}
    eval_payload = bundle.get("eval") or {}
    verification = list((trace.get("verification") or eval_payload.get("verification") or []))
    failed_checks = [item for item in verification if item.get("result") in {"fail", "blocked", "not_run"}]
    result = {
        "ok": True,
        "resume": checkpoint,
        "trace": trace,
        "eval": eval_payload or None,
        "pending_verification": bool(failed_checks),
        "failed_checks": failed_checks,
        "suggested_next_actions": [
            checkpoint.get("next_step") or "continue from checkpoint",
            "repair failing verification before closing session" if failed_checks else "continue execution",
        ],
        "source_of_truth": "mimir" if checkpoint.get("source") == "mimir" else "local_fallback",
    }
    if continue_as_new_trace:
        current_lineage = dict(trace.get("lineage") or checkpoint.get("lineage") or {})
        lineage = {
            "lineage_id": str(current_lineage.get("lineage_id") or resolved_trace_id),
            "parent_trace_id": resolved_trace_id,
            "resumed_from_trace_id": resolved_trace_id,
            "resume_depth": int(current_lineage.get("resume_depth", 0) or 0) + 1,
        }
        child = start_session(
            task=str(trace.get("task") or checkpoint.get("task") or "resumed session"),
            route=str(trace.get("route") or "direct"),
            project=trace.get("project") or None,
            resume_id=checkpoint.get("resume_id"),
            lineage=lineage,
        )
        result["resumed_trace"] = child
    return result


def _self_check() -> int:
    started = start_session(
        task="implement trace writer governance change",
        route="direct",
        project="agent",
        fail_closed_navigation=False,
    )
    assert "context_plan" in started
    assert started["context_plan"]["navigation_required"] is True
    assert started["navigation_guard"]["fail_closed_navigation"] is False
    old_endpoint = os.environ.pop("MIMIR_ENDPOINT", None)
    old_fail_closed = os.environ.get("AGENT_NAVIGATION_FAIL_CLOSED")
    os.environ["AGENT_NAVIGATION_FAIL_CLOSED"] = "1"
    try:
        blocked = start_session(
            task="implement trace writer governance change",
            route="direct",
            project="agent",
        )
        assert blocked["ok"] is False
        assert blocked["navigation_guard"]["blocked"] is True
        assert blocked["error"] == "navigation_required_but_mimir_unavailable"
    finally:
        if old_endpoint is not None:
            os.environ["MIMIR_ENDPOINT"] = old_endpoint
        if old_fail_closed is None:
            os.environ.pop("AGENT_NAVIGATION_FAIL_CLOSED", None)
        else:
            os.environ["AGENT_NAVIGATION_FAIL_CLOSED"] = old_fail_closed
    resumed = resume_session(trace_id=started["trace_id"], continue_as_new_trace=True)
    assert resumed["resume"]["trace_id"] == started["trace_id"]
    assert resumed["resumed_trace"]["trace_id"] != started["trace_id"]
    assert resumed["source_of_truth"] in {"mimir", "local_fallback"}
    print("session-runtime: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Start or resume governed runtime sessions.")
    subparsers = parser.add_subparsers(dest="command")

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--task", required=True)
    start_parser.add_argument("--route", default="direct")
    start_parser.add_argument("--project")
    start_parser.add_argument("--session-id")
    start_parser.add_argument("--trace-id")
    start_parser.add_argument("--resume-id")
    start_parser.add_argument("--fail-closed-navigation", action="store_true")

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("--resume-id")
    resume_parser.add_argument("--trace-id")
    resume_parser.add_argument("--inspect-only", action="store_true")

    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()

    if args.command == "start":
        result = start_session(
            task=args.task,
            route=args.route,
            project=args.project,
            session_id=args.session_id,
            trace_id=args.trace_id,
            resume_id=args.resume_id,
            fail_closed_navigation=args.fail_closed_navigation,
        )
    elif args.command == "resume":
        result = resume_session(
            resume_id=args.resume_id,
            trace_id=args.trace_id,
            continue_as_new_trace=not args.inspect_only,
        )
    else:
        raise SystemExit("use start or resume")

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
