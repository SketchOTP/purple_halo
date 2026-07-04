#!/usr/bin/env python3
"""Executable governance hook runner for the agent repo."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from eval_writer import build_eval, write_eval
from governance_tier import merge_governance_observability
from governance_terms import approval_request_id, requires_approval_gate
from policy_worker import resolve_policy
from resume_writer import write_resume
from mimir_code_nav import load_mimir_env
from trace_writer import load_trace, write_trace

load_mimir_env()

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "project_memory" / "runtime"
HOOK_LOG = ARTIFACT_DIR / "hook_events.jsonl"

ALLOWED_HOOKS = {
    "session_start",
    "pre_tool_use",
    "post_tool_use",
    "verification_complete",
    "session_end",
}
ALLOWED_STATUS = {"allow", "deny", "observe", "error"}


def _looks_like_broad_mutation(details: dict) -> bool:
    action_class = str(details.get("action_class") or "").strip().lower()
    target_paths = [str(item).strip() for item in (details.get("target_paths") or []) if str(item).strip()]
    command = str(details.get("command") or "").lower()
    wildcard_targets = any(path in {".", "./", "*"} or "*" in path for path in target_paths)
    distinct_roots = {Path(path).parts[0] if Path(path).parts else path for path in target_paths}
    if action_class in {"format", "bulk_edit", "write"} and (wildcard_targets or len(target_paths) > 1 or len(distinct_roots) > 1):
        return True
    broad_mutators = (
        "git apply",
        "sed -i",
        "perl -pi",
        "find .",
        "xargs",
        "prettier --write .",
        "ruff format .",
        "black .",
    )
    return any(term in command for term in broad_mutators)


def _structured_adapter_suggestion(action_class: str) -> str:
    mapping = {
        "raw_shell": "use cursor_session.py tool --adapter search_code/read_file/git_diff/run_test/lint/format/replace_text/write_file/append_file/install_dependencies/run_application",
        "shell": "use cursor_session.py tool --adapter search_code/read_file/git_diff/run_test/lint/format/replace_text/write_file/append_file/install_dependencies/run_application",
    }
    return mapping.get(action_class, "use a structured cursor_session.py tool adapter")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require(payload: dict, key: str) -> None:
    value = payload.get(key)
    if value is None or value == "" or value == []:
        raise ValueError(f"missing required field: {key}")


def _validate_event(payload: dict) -> None:
    for key in ("event_id", "hook", "trace_id", "status", "timestamp"):
        _require(payload, key)
    if payload["hook"] not in ALLOWED_HOOKS:
        raise ValueError(f"unsupported hook: {payload['hook']}")
    if payload["status"] not in ALLOWED_STATUS:
        raise ValueError(f"unsupported status: {payload['status']}")
    if payload["hook"] == "session_start":
        details = payload.get("details") or {}
        _require(details, "task")
    if payload["hook"] == "pre_tool_use":
        details = payload.get("details") or {}
        _require(details, "tool_name")
        if requires_approval_gate(details) and not approval_request_id(details):
            raise ValueError("pre_tool_use requires approval_request_id for approval-gated work")
        blocked_terms = ("rm -rf", "git reset --hard", "DROP TABLE")
        command = str(details.get("command") or "")
        if any(term in command for term in blocked_terms):
            payload["status"] = "deny"
            details["denial_reason"] = "blocked_destructive_command"
            payload["details"] = details
        if (
            str(details.get("action_class") or "").strip().lower() == "raw_shell"
            and bool(details.get("navigation_required"))
            and not bool(details.get("raw_shell_allowed"))
        ):
            payload["status"] = "deny"
            details["denial_reason"] = "raw_shell_requires_structured_adapter"
            details["suggested_adapter_flow"] = _structured_adapter_suggestion("raw_shell")
            payload["details"] = details
        if (
            bool(details.get("navigation_required"))
            and not list(details.get("code_navigation_pack_ids") or [])
            and _looks_like_broad_mutation(details)
        ):
            payload["status"] = "deny"
            details["denial_reason"] = "navigation_required_for_broad_edit"
            details["suggested_adapter_flow"] = _structured_adapter_suggestion(str(details.get("action_class") or ""))
            payload["details"] = details
        if payload["status"] == "deny":
            details = merge_governance_observability({**details, "hook_status": "deny"})
            payload["details"] = details
    if payload["hook"] == "verification_complete":
        if not payload.get("eval_id"):
            raise ValueError("verification_complete requires eval_id")
    if payload["hook"] == "session_end":
        details = payload.get("details") or {}
        _require(details, "final_status")


def _write_local_event(payload: dict) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with HOOK_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _require_token_usage(trace: dict, payload: dict) -> None:
    if os.environ.get("AGENT_STRICT_TOKENS", "0") != "1":
        return
    total_tokens = ((trace.get("token_usage") or {}).get("total_tokens"))
    if total_tokens is None:
        raise ValueError(
            f"strict token mode requires total_tokens before {payload.get('hook')} for trace {payload.get('trace_id')}"
        )


def _post_json(url: str, payload: dict, api_key: str | None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _maybe_emit_mimir(payload: dict) -> dict | None:
    endpoint = os.environ.get("MIMIR_ENDPOINT", "").rstrip("/")
    if not endpoint:
        return None
    return _post_json(f"{endpoint}/api/governance/hooks", payload, os.environ.get("MIMIR_API_KEY"))


def _maybe_fetch_policy(payload: dict) -> dict | None:
    endpoint = os.environ.get("MIMIR_ENDPOINT", "").rstrip("/")
    if not endpoint:
        return None
    details = payload.get("details") or {}
    tool_name = details.get("tool_name")
    if not tool_name:
        return None
    trace = load_trace(str(payload.get("trace_id") or "")) or {}
    routing_context = dict(trace.get("routing_context") or {})
    request = {
        "project": payload.get("project"),
        "hook": payload.get("hook"),
        "tool_name": tool_name,
        "command": details.get("command"),
        "trace_id": payload.get("trace_id"),
        "route": details.get("route") or trace.get("route"),
        "task": details.get("task") or trace.get("task"),
        "navigation_required": details.get("navigation_required")
        if details.get("navigation_required") is not None
        else routing_context.get("navigation_required"),
        "code_navigation_pack_ids": details.get("code_navigation_pack_ids")
        or routing_context.get("code_navigation_pack_ids")
        or [],
        "action_class": details.get("action_class"),
        "governance_entrypoint": details.get("entrypoint")
        or details.get("governance_entrypoint")
        or routing_context.get("governance_entrypoint"),
        "session_id": payload.get("session_id"),
    }
    return _post_json(
        f"{endpoint}/api/governance/policy/tool-guard",
        request,
        os.environ.get("MIMIR_API_KEY"),
    )


def run_event(payload: dict) -> dict:
    _validate_event(payload)
    policy = None
    if payload["hook"] == "pre_tool_use":
        try:
            policy = _maybe_fetch_policy(payload)
        except Exception as exc:
            policy = {"ok": False, "error": str(exc)}
        if policy and policy.get("action") == "deny":
            payload["status"] = "deny"
            details = payload.get("details") or {}
            details["policy_reasons"] = policy.get("reasons") or []
            details["denial_reason"] = details.get("denial_reason") or "policy_denied"
            details["requires_governance_review"] = bool(
                policy.get("requires_governance_review") or policy.get("requires_architect_review")
            )
            details = merge_governance_observability({**details, "hook_status": "deny"})
            payload["details"] = details
        elif policy and policy.get("action") == "observe":
            details = payload.get("details") or {}
            details["policy_reasons"] = policy.get("reasons") or []
            details["policy_observation"] = True
            payload["details"] = details
    _write_local_event(payload)
    result = {
        "ok": True,
        "event_id": payload["event_id"],
        "hook": payload["hook"],
        "status": payload["status"],
    }
    result["trace"] = write_trace(payload)
    if payload["hook"] in {"verification_complete", "session_end"}:
        _require_token_usage(result["trace"], payload)
    result["resume"] = write_resume(result["trace"], payload)
    if policy is not None:
        result["policy"] = policy
    if payload["hook"] == "verification_complete":
        details = payload.get("details") or {}
        routing_context = dict((result["trace"] or {}).get("routing_context") or {})
        eval_input = {
            "trace_id": payload.get("trace_id"),
            "eval_id": payload.get("eval_id"),
            "project": payload.get("project"),
            "session_id": payload.get("session_id"),
            "verification": result["trace"].get("verification") or details.get("verification") or details.get("checks") or [],
            "used_memory": details.get("used_memory", False),
            "token_efficiency": details.get("token_efficiency", 0.75),
            "code_navigation_pack_ids": details.get("code_navigation_pack_ids")
            or routing_context.get("code_navigation_pack_ids")
            or [],
            "token_usage": result["trace"].get("token_usage") or details.get("token_usage") or {},
        }
        if eval_input["verification"]:
            result["eval"] = write_eval(eval_input)
        else:
            result["eval"] = build_eval(eval_input)
        result["runtime_policy"] = resolve_policy(
            trace_id=payload.get("trace_id"),
            eval_payload=result["eval"],
        )
    try:
        mimir_result = _maybe_emit_mimir(payload)
        if mimir_result is not None:
            result["mimir"] = mimir_result
    except Exception as exc:
        result["mimir_error"] = str(exc)
    return result


def _self_check() -> int:
    samples = [
        {
            "event_id": "evt-start",
            "hook": "session_start",
            "trace_id": "trace-1",
            "status": "observe",
            "timestamp": utc_now(),
            "details": {"task": "search TODO", "route": "direct"},
        },
        {
            "event_id": "evt-pre",
            "hook": "pre_tool_use",
            "trace_id": "trace-1",
            "status": "allow",
            "timestamp": utc_now(),
            "details": {"tool_name": "shell", "command": "rg TODO", "requires_approval_gate": False, "task": "search TODO"},
        },
        {
            "event_id": "evt-post",
            "hook": "post_tool_use",
            "trace_id": "trace-1",
            "status": "observe",
            "timestamp": utc_now(),
            "details": {"tool_name": "shell", "exit_code": 0},
        },
        {
            "event_id": "evt-verify",
            "hook": "verification_complete",
            "trace_id": "trace-1",
            "eval_id": "eval-1",
            "status": "allow",
            "timestamp": utc_now(),
            "details": {
                "checks_passed": 3,
                "token_usage": {"prompt_tokens": 120, "completion_tokens": 30},
                "verification": [
                    {"label": "pytest", "command": "pytest -q", "result": "pass", "evidence": "ok"},
                    {"label": "architect review", "command": "architect_review_diff", "result": "fail", "evidence": "scope drift"},
                ],
            },
        },
        {
            "event_id": "evt-end",
            "hook": "session_end",
            "trace_id": "trace-1",
            "eval_id": "eval-1",
            "status": "observe",
            "timestamp": utc_now(),
            "details": {"final_status": "complete"},
        },
    ]
    for payload in samples:
        outcome = run_event(payload)
        if not outcome.get("ok"):
            raise SystemExit("hook runner self-check failed")
    old = os.environ.get("AGENT_STRICT_TOKENS")
    os.environ["AGENT_STRICT_TOKENS"] = "1"
    try:
        strict_payload = {
            "event_id": "evt-strict",
            "hook": "verification_complete",
            "trace_id": "trace-strict",
            "eval_id": "eval-strict",
            "status": "allow",
            "timestamp": utc_now(),
            "details": {
                "task": "strict tokens",
                "token_usage": {"total_tokens": 10},
                "verification": [{"label": "pytest", "command": "pytest -q", "result": "pass", "evidence": "ok"}],
            },
        }
        run_event(strict_payload)
    finally:
        if old is None:
            os.environ.pop("AGENT_STRICT_TOKENS", None)
        else:
            os.environ["AGENT_STRICT_TOKENS"] = old
    raw_shell_payload = {
        "event_id": "evt-raw-shell",
        "hook": "pre_tool_use",
        "trace_id": "trace-raw-shell",
        "status": "allow",
        "timestamp": utc_now(),
        "details": {
            "tool_name": "shell",
            "command": "python3 scripts/do_work.py",
            "task": "implement governed feature",
            "navigation_required": True,
            "action_class": "raw_shell",
            "raw_shell_allowed": False,
        },
    }
    denied = run_event(raw_shell_payload)
    assert denied["status"] == "deny"
    print("hook-runner: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run an agent governance hook.")
    parser.add_argument("--hook")
    parser.add_argument("--trace-id")
    parser.add_argument("--event-id")
    parser.add_argument("--status")
    parser.add_argument("--eval-id")
    parser.add_argument("--details-json", default="{}")
    parser.add_argument("--timestamp")
    parser.add_argument("--project")
    parser.add_argument("--session-id")
    parser.add_argument("--prompt-tokens", type=int)
    parser.add_argument("--completion-tokens", type=int)
    parser.add_argument("--total-tokens", type=int)
    parser.add_argument("--model")
    parser.add_argument("--provider")
    parser.add_argument("--memory-recall-count", type=int)
    parser.add_argument("--repair-source")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()

    payload = {
        "event_id": args.event_id,
        "hook": args.hook,
        "trace_id": args.trace_id,
        "status": args.status,
        "eval_id": args.eval_id,
        "project": args.project,
        "session_id": args.session_id,
        "timestamp": args.timestamp or utc_now(),
        "details": json.loads(args.details_json or "{}"),
    }
    if any(value is not None for value in (args.prompt_tokens, args.completion_tokens, args.total_tokens)):
        payload["details"]["token_usage"] = {
            key: value
            for key, value in {
                "prompt_tokens": args.prompt_tokens,
                "completion_tokens": args.completion_tokens,
                "total_tokens": args.total_tokens,
            }.items()
            if value is not None
        }
    if args.model or args.provider or args.memory_recall_count is not None:
        payload["details"]["provenance"] = {
            key: value
            for key, value in {
                "model": args.model,
                "provider": args.provider,
                "memory_recall_count": args.memory_recall_count,
            }.items()
            if value is not None
        }
    if args.repair_source:
        payload["details"]["repair_source"] = args.repair_source
    result = run_event(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
