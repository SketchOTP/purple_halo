#!/usr/bin/env python3
"""Small runtime orchestrator that enforces the session context plan during tool execution."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import Any, Callable

from governance_terms import normalize_hook_details
from governance_tier import CURSOR_SESSION_ENTRYPOINT, merge_governance_observability
from hook_runner import run_event, utc_now
from session_runtime import navigation_guard, start_session


class SessionOrchestrator:
    def __init__(
        self,
        session_state: dict[str, Any],
        *,
        fail_closed_navigation: bool | None = None,
    ) -> None:
        self.session = dict(session_state)
        self.context_plan = dict(session_state.get("context_plan") or {})
        self.trace_id = str(session_state["trace_id"])
        self.session_id = str(session_state["session_id"])
        self.project = session_state.get("project")
        self.route = str(session_state.get("route") or self.context_plan.get("route") or "direct")
        self.task = str(self.context_plan.get("task") or session_state.get("task") or "governed session")
        self.navigation = navigation_guard(
            self.context_plan,
            fail_closed_navigation=fail_closed_navigation,
        )

    def _base_details(self) -> dict[str, Any]:
        base = {
            "task": self.task,
            "route": self.route,
            "context_plan": self.context_plan,
            "entrypoint": self.session.get("entrypoint") or CURSOR_SESSION_ENTRYPOINT,
            "navigation_required": self.navigation["navigation_required"],
            "code_navigation_pack_ids": list(self.navigation["code_navigation_pack_ids"]),
            "loaded_mcps": list(self.context_plan.get("mcp_preflight") or []),
            "route_evidence": list(self.context_plan.get("mcp_actions") or []),
            "mimir_available": self.navigation["mimir_available"],
            "fail_closed_navigation": self.navigation["fail_closed_navigation"],
            "navigation_blocked": self.navigation["blocked"],
            "blocked_reason": self.navigation["reason"],
        }
        return merge_governance_observability(base)

    def pre_tool_use(
        self,
        *,
        tool_name: str,
        command: str | None = None,
        details: dict[str, Any] | None = None,
        requires_approval_gate: bool = False,
        approval_request_id: str | None = None,
    ) -> dict[str, Any]:
        payload_details = self._base_details()
        payload_details.update(details or {})
        payload_details["tool_name"] = tool_name
        if command:
            payload_details["command"] = command
        if requires_approval_gate:
            payload_details["requires_approval_gate"] = True
        if approval_request_id:
            payload_details["approval_request_id"] = approval_request_id
        payload_details = normalize_hook_details(payload_details)
        status = "allow"
        if self.navigation["blocked"]:
            status = "deny"
            payload_details["blocked_reason"] = self.navigation["reason"]
        payload_details = merge_governance_observability({**payload_details, "hook_status": status})
        return run_event(
            {
                "event_id": f"{self.trace_id}:pre:{uuid.uuid4().hex[:8]}",
                "hook": "pre_tool_use",
                "trace_id": self.trace_id,
                "status": status,
                "timestamp": utc_now(),
                "project": self.project,
                "session_id": self.session_id,
                "details": payload_details,
            }
        )

    def post_tool_use(
        self,
        *,
        tool_name: str,
        exit_code: int,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_details = self._base_details()
        payload_details.update(details or {})
        payload_details["tool_name"] = tool_name
        payload_details["exit_code"] = exit_code
        return run_event(
            {
                "event_id": f"{self.trace_id}:post:{uuid.uuid4().hex[:8]}",
                "hook": "post_tool_use",
                "trace_id": self.trace_id,
                "status": "observe",
                "timestamp": utc_now(),
                "project": self.project,
                "session_id": self.session_id,
                "details": payload_details,
            }
        )

    def verification_complete(
        self,
        *,
        eval_id: str,
        verification: list[dict[str, Any]],
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_details = self._base_details()
        payload_details.update(details or {})
        payload_details["verification"] = verification
        return run_event(
            {
                "event_id": f"{self.trace_id}:verify:{uuid.uuid4().hex[:8]}",
                "hook": "verification_complete",
                "trace_id": self.trace_id,
                "eval_id": eval_id,
                "status": "allow",
                "timestamp": utc_now(),
                "project": self.project,
                "session_id": self.session_id,
                "details": payload_details,
            }
        )

    def session_end(
        self,
        *,
        final_status: str,
        eval_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_details = self._base_details()
        payload_details.update(details or {})
        payload_details["final_status"] = final_status
        return run_event(
            {
                "event_id": f"{self.trace_id}:end:{uuid.uuid4().hex[:8]}",
                "hook": "session_end",
                "trace_id": self.trace_id,
                "eval_id": eval_id,
                "status": "observe",
                "timestamp": utc_now(),
                "project": self.project,
                "session_id": self.session_id,
                "details": payload_details,
            }
        )

    def run_tool(
        self,
        *,
        tool_name: str,
        executor: Callable[[], Any],
        command: str | None = None,
        details: dict[str, Any] | None = None,
        post_details: dict[str, Any] | None = None,
        requires_approval_gate: bool = False,
        approval_request_id: str | None = None,
    ) -> dict[str, Any]:
        pre = self.pre_tool_use(
            tool_name=tool_name,
            command=command,
            details=details,
            requires_approval_gate=requires_approval_gate,
            approval_request_id=approval_request_id,
        )
        if pre.get("status") == "deny":
            return {
                "ok": False,
                "blocked": True,
                "pre_tool": pre,
                "error": ((pre.get("trace") or {}).get("routing_context") or {}).get("blocked_reason")
                or (details or {}).get("blocked_reason")
                or self.navigation["reason"],
            }
        exit_code = 0
        payload: Any = None
        try:
            payload = executor()
            if isinstance(payload, dict) and payload.get("exit_code") is not None:
                exit_code = int(payload.get("exit_code") or 0)
        except Exception as exc:
            exit_code = 1
            payload = {"error": str(exc)}
        resolved_post_details = dict(post_details or {})
        if isinstance(payload, dict):
            resolved_post_details.setdefault("result", payload)
            if isinstance(payload.get("artifacts"), list):
                resolved_post_details.setdefault("artifacts", list(payload.get("artifacts") or []))
        else:
            resolved_post_details.setdefault("result_summary", str(payload))
        post = self.post_tool_use(
            tool_name=tool_name,
            exit_code=exit_code,
            details=resolved_post_details,
        )
        return {
            "ok": exit_code == 0,
            "blocked": False,
            "pre_tool": pre,
            "post_tool": post,
            "result": payload,
            "exit_code": exit_code,
        }


def start_orchestrated_session(
    *,
    task: str,
    route: str,
    project: str | None = None,
    run_kind: str | None = None,
    fail_closed_navigation: bool | None = None,
) -> SessionOrchestrator:
    session_state = start_session(
        task=task,
        route=route,
        project=project,
        run_kind=run_kind,
        fail_closed_navigation=fail_closed_navigation,
    )
    session_state["task"] = task
    session_state["project"] = project
    return SessionOrchestrator(
        session_state,
        fail_closed_navigation=fail_closed_navigation,
    )


def _self_check() -> int:
    orchestrator = start_orchestrated_session(
        task="implement session orchestrator trace guard",
        route="direct",
        project="agent",
        fail_closed_navigation=False,
    )
    allowed = orchestrator.run_tool(
        tool_name="shell",
        command="pytest -q",
        executor=lambda: {"stdout": "ok"},
    )
    assert allowed["ok"] is True
    verified = orchestrator.verification_complete(
        eval_id="eval-orchestrator-self",
        verification=[{"label": "pytest", "command": "pytest -q", "result": "pass", "evidence": "ok"}],
        details={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
    )
    assert verified["ok"] is True
    ended = orchestrator.session_end(final_status="complete", eval_id="eval-orchestrator-self")
    assert ended["ok"] is True
    old_endpoint = None
    blocked = None
    try:
        old_endpoint = os.environ.pop("MIMIR_ENDPOINT", None)
        blocked = start_orchestrated_session(
            task="implement session orchestrator trace guard",
            route="direct",
            project="agent",
            fail_closed_navigation=True,
        ).run_tool(
            tool_name="shell",
            command="pytest -q",
            executor=lambda: {"stdout": "should not run"},
        )
    except Exception as exc:
        raise AssertionError(str(exc)) from exc
    finally:
        if old_endpoint is not None:
            os.environ["MIMIR_ENDPOINT"] = old_endpoint
    assert blocked is not None
    assert blocked["ok"] is False
    assert blocked["blocked"] is True
    print("session-orchestrator: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run tool execution through the governed session orchestrator.")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--task")
    parser.add_argument("--route", default="direct")
    parser.add_argument("--project")
    parser.add_argument("--tool-name")
    parser.add_argument("--command")
    parser.add_argument("--fail-closed-navigation", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()
    if not args.task or not args.tool_name:
        raise SystemExit("task and tool-name are required unless --self-check is used")

    orchestrator = start_orchestrated_session(
        task=args.task,
        route=args.route,
        project=args.project,
        fail_closed_navigation=args.fail_closed_navigation,
    )
    result = orchestrator.run_tool(
        tool_name=args.tool_name,
        command=args.command,
        executor=lambda: {"status": "no-op"},
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
