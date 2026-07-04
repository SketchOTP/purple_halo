#!/usr/bin/env python3
"""Cursor-native enforcement — classify and gate direct MCP/shell/tool use."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from governance_tier import (
    CURSOR_SESSION_ENTRYPOINT,
    FULLY_GOVERNED,
    PARTIALLY_GOVERNED,
    UNGCOVERNED,
    merge_governance_observability,
)
from hook_runner import run_event, utc_now
from session_orchestrator import SessionOrchestrator, start_orchestrated_session
from session_runtime import refresh_session_navigation


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "project_memory" / "runtime"
ACTIVE_SESSION_PATH = RUNTIME / "cursor_active_session.json"
ENFORCEMENT_STATE_PATH = RUNTIME / "cursor_enforcement_state.json"

CURSOR_DIRECT_ENTRYPOINT = "cursor_direct_mcp"
ENFORCEMENT_ENV = "AGENT_CURSOR_NATIVE_ENFORCEMENT"
ROLLOUT_STAGE_ENV = "AGENT_CURSOR_NATIVE_ROLLOUT_STAGE"

WRITE_TOOL_MARKERS = ("write", "edit", "apply_patch", "strreplace", "delete", "create")
MUTATING_SHELL_MARKERS = (
    "rm -rf",
    "git reset --hard",
    "git push",
    "npm publish",
    "pip install",
    "> ",
    " >> ",
)


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def enforcement_enabled() -> bool:
    return _env_flag(ENFORCEMENT_ENV, default=True)


def enforcement_mode() -> str:
    return "enforce" if enforcement_enabled() else "rollback"


def rollout_stage() -> int:
    raw = os.environ.get(ROLLOUT_STAGE_ENV, "1")
    try:
        return max(0, min(3, int(str(raw).strip())))
    except ValueError:
        return 1


def governance_attribution() -> str:
    """Attribution label for observability — rollback must not mask enforce success."""
    if not enforcement_enabled():
        return "rollback_observe_only"
    return f"enforce_stage_{rollout_stage()}"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_enforcement_state() -> dict[str, Any]:
    payload = {
        "enabled": enforcement_enabled(),
        "mode": enforcement_mode(),
        "rollout_stage": rollout_stage(),
        "governance_attribution": governance_attribution(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "entrypoint_direct": CURSOR_DIRECT_ENTRYPOINT,
        "trusted_entrypoint": CURSOR_SESSION_ENTRYPOINT,
    }
    _write_json(ENFORCEMENT_STATE_PATH, payload)
    return payload


def load_active_session() -> dict[str, Any] | None:
    record = _read_json(ACTIVE_SESSION_PATH)
    if not record:
        return None
    if record.get("workspace_root") != str(ROOT):
        return None
    session = dict(record.get("session") or {})
    if not session.get("trace_id"):
        return None
    return session


def _is_cursor_session_command(command: str) -> bool:
    text = str(command or "").lower()
    return "cursor_session.py" in text


def _is_mutating_shell(command: str) -> bool:
    lowered = str(command or "").lower()
    return any(marker in lowered for marker in MUTATING_SHELL_MARKERS)


def _tool_name_from_payload(payload: dict[str, Any]) -> str:
    for key in ("tool_name", "tool", "name"):
        value = payload.get(key)
        if value:
            return str(value)
    return "unknown"


def _looks_like_write_tool(tool_name: str) -> bool:
    lowered = str(tool_name or "").lower()
    return any(marker in lowered for marker in WRITE_TOOL_MARKERS)


def _session_observability(session: dict[str, Any] | None, *, entrypoint: str) -> dict[str, Any]:
    if not session:
        base = {
            "entrypoint": entrypoint,
            "governance_entrypoint": entrypoint,
            "navigation_required": True,
            "code_navigation_pack_ids": [],
            "mimir_available": True,
        }
        return merge_governance_observability(base)

    plan = dict(session.get("context_plan") or {})
    packs = list(plan.get("code_navigation_pack_ids") or [])
    nav = dict(plan.get("code_navigation") or {})
    for pid in nav.get("pack_ids") or []:
        if pid and pid not in packs:
            packs.append(pid)
    base = {
        "entrypoint": entrypoint,
        "governance_entrypoint": entrypoint if entrypoint != CURSOR_DIRECT_ENTRYPOINT else CURSOR_DIRECT_ENTRYPOINT,
        "navigation_required": bool(plan.get("navigation_required", True)),
        "code_navigation_pack_ids": packs,
        "context_plan": plan,
        "mimir_available": True,
        "pack_quality": plan.get("pack_quality"),
        "pack_quality_state": (plan.get("pack_quality") or {}).get("pack_quality_state"),
    }
    if entrypoint == CURSOR_SESSION_ENTRYPOINT:
        base["governance_entrypoint"] = CURSOR_SESSION_ENTRYPOINT
    return merge_governance_observability(base)


def _structured_adapter_hint(tool_name: str, command: str | None) -> tuple[str | None, str]:
    """Map native bridge tools to governed adapter paths. None => bridge may still apply."""
    lowered = str(tool_name or "").lower()
    cmd = str(command or "").lower()
    if any(m in lowered for m in ("write", "strreplace", "edit", "apply_patch", "delete")):
        return (
            "raw_shell_requires_structured_adapter",
            "cursor_session.py tool --adapter write_file|append_file|replace_text",
        )
    if lowered == "read" or (lowered.endswith("read") and "grep" not in lowered):
        return (
            "raw_shell_requires_structured_adapter",
            "cursor_session.py tool --adapter read_file --path <path>",
        )
    if "grep" in lowered or lowered == "grep":
        return (
            "raw_shell_requires_structured_adapter",
            "cursor_session.py tool --adapter search_code --pattern <term> --path <dir>",
        )
    if lowered == "shell" or lowered.endswith("shell"):
        if "git status" in cmd:
            return ("raw_shell_requires_structured_adapter", "cursor_session.py tool --adapter git_status")
        if "git diff" in cmd:
            return ("raw_shell_requires_structured_adapter", "cursor_session.py tool --adapter git_diff")
        if "rg " in cmd or "grep " in cmd or " search " in cmd:
            return (
                "raw_shell_requires_structured_adapter",
                "cursor_session.py tool --adapter search_code --pattern <term> --path scripts",
            )
        if "list" in cmd or "ls " in cmd:
            return (
                "raw_shell_requires_structured_adapter",
                "cursor_session.py tool --adapter list_files --path <dir>",
            )
        return (
            "raw_shell_requires_structured_adapter",
            "cursor_session.py tool --adapter git_status|search_code|list_files|read_file",
        )
    return None, ""


def _deny_bridge_for_structured_adapter(
    *,
    session: dict[str, Any],
    tool_name: str,
    command: str | None,
    denial_reason: str,
    adapter_hint: str,
) -> dict[str, Any]:
    obs = merge_governance_observability(
        {
            **_session_observability(session, entrypoint=CURSOR_DIRECT_ENTRYPOINT),
            "entrypoint": CURSOR_DIRECT_ENTRYPOINT,
            "governance_entrypoint": CURSOR_DIRECT_ENTRYPOINT,
            "degraded_reason": denial_reason,
            "denial_reason": denial_reason,
            "suggested_adapter_flow": adapter_hint,
            "hook_status": "deny",
        }
    )
    return {
        "permission": "deny",
        "tier": PARTIALLY_GOVERNED,
        "degraded_reason": denial_reason,
        "denied_reason": denial_reason,
        "reason": "bridge_requires_structured_adapter",
        "adapter_hint": adapter_hint,
        "observability": obs,
    }


def classify_native_use(
    *,
    event: str,
    tool_name: str,
    command: str | None,
    session: dict[str, Any] | None,
    enforcement_on: bool,
) -> dict[str, Any]:
    """Return tier, permission, and reason for a Cursor-native tool/shell event."""
    if _is_cursor_session_command(command or ""):
        obs = _session_observability(session, entrypoint=CURSOR_SESSION_ENTRYPOINT)
        return {
            "permission": "allow",
            "tier": FULLY_GOVERNED if obs.get("is_fully_governed") else PARTIALLY_GOVERNED,
            "degraded_reason": obs.get("degraded_reason"),
            "denied_reason": obs.get("denied_reason"),
            "reason": "cursor_session_command",
            "observability": obs,
        }

    if session and session.get("entrypoint") == CURSOR_SESSION_ENTRYPOINT:
        denial_reason, adapter_hint = _structured_adapter_hint(tool_name, command)
        if enforcement_on and denial_reason:
            return _deny_bridge_for_structured_adapter(
                session=session,
                tool_name=tool_name,
                command=command,
                denial_reason=denial_reason,
                adapter_hint=adapter_hint,
            )
        obs = _session_observability(session, entrypoint=CURSOR_DIRECT_ENTRYPOINT)
        obs = merge_governance_observability(
            {
                **obs,
                "entrypoint": CURSOR_DIRECT_ENTRYPOINT,
                "governance_entrypoint": CURSOR_DIRECT_ENTRYPOINT,
                "degraded_reason": "not_cursor_session_entrypoint",
            }
        )
        return {
            "permission": "allow",
            "tier": PARTIALLY_GOVERNED,
            "degraded_reason": "not_cursor_session_entrypoint",
            "denied_reason": None,
            "reason": "direct_native_with_active_session",
            "observability": obs,
        }

    obs = _session_observability(None, entrypoint=CURSOR_DIRECT_ENTRYPOINT)
    obs = merge_governance_observability(
        {
            **obs,
            "degraded_reason": "not_cursor_session_entrypoint",
            "ungoverned_success_risk": True,
        }
    )
    mutating = _looks_like_write_tool(tool_name) or _is_mutating_shell(command or "")
    if enforcement_on and mutating:
        return {
            "permission": "deny",
            "tier": UNGCOVERNED,
            "degraded_reason": "not_cursor_session_entrypoint",
            "denied_reason": "not_cursor_session_entrypoint",
            "reason": "mutating_native_without_session",
            "observability": obs,
        }
    if enforcement_on:
        return {
            "permission": "deny",
            "tier": UNGCOVERNED,
            "degraded_reason": "not_cursor_session_entrypoint",
            "denied_reason": "not_cursor_session_entrypoint",
            "reason": "native_without_session",
            "observability": obs,
        }
    return {
        "permission": "allow",
        "tier": UNGCOVERNED,
        "degraded_reason": "not_cursor_session_entrypoint",
        "denied_reason": None,
        "reason": "rollback_observe_only",
        "observability": obs,
    }


def ensure_governed_session(*, task: str, project: str = "agent") -> dict[str, Any]:
    """Establish governed session before first meaningful native tool use."""
    existing = load_active_session()
    if existing:
        refreshed = refresh_session_navigation(existing, task=task)
        refreshed["entrypoint"] = CURSOR_SESSION_ENTRYPOINT
        orchestrator = SessionOrchestrator(refreshed)
        record = _read_json(ACTIVE_SESSION_PATH) or {}
        _write_json(
            ACTIVE_SESSION_PATH,
            {
                "workspace_root": str(ROOT),
                "session": orchestrator.session,
                "metadata": record.get("metadata") or {"command_history": [], "changed_files": [], "verification_runs": []},
            },
        )
        return {
            "ok": True,
            "created": False,
            "trace_id": orchestrator.trace_id,
            "session_id": orchestrator.session_id,
            "observability": _session_observability(refreshed, entrypoint=CURSOR_SESSION_ENTRYPOINT),
        }

    orchestrator = start_orchestrated_session(task=task, route="direct-governance", project=project)
    session = refresh_session_navigation(orchestrator.session, task=task)
    session["entrypoint"] = CURSOR_SESSION_ENTRYPOINT
    orchestrator = SessionOrchestrator(session)
    _write_json(
        ACTIVE_SESSION_PATH,
        {
            "workspace_root": str(ROOT),
            "session": orchestrator.session,
            "metadata": {"command_history": [], "changed_files": [], "verification_runs": []},
        },
    )
    return {
        "ok": True,
        "created": True,
        "trace_id": orchestrator.trace_id,
        "session_id": orchestrator.session_id,
        "observability": _session_observability(orchestrator.session, entrypoint=CURSOR_SESSION_ENTRYPOINT),
    }


def record_native_event(
    *,
    event: str,
    classification: dict[str, Any],
    session: dict[str, Any] | None,
    tool_name: str,
    command: str | None,
) -> dict[str, Any]:
    trace_id = str((session or {}).get("trace_id") or f"trace:native:{uuid.uuid4().hex[:12]}")
    details = dict(classification.get("observability") or {})
    details.update(
        {
            "tool_name": tool_name,
            "command": command,
            "cursor_native_enforcement_enabled": enforcement_enabled(),
            "cursor_native_enforcement_mode": enforcement_mode(),
            "cursor_native_rollout_stage": rollout_stage(),
            "governance_attribution": governance_attribution(),
            "cursor_native_hook_event": event,
            "classification_reason": classification.get("reason"),
            "hook_status": "deny" if classification.get("permission") == "deny" else "allow",
        }
    )
    if classification.get("permission") == "deny":
        details["denial_event_id"] = f"denial:{uuid.uuid4().hex[:12]}"
        reason = str(classification.get("reason") or "")
        details["denial_class"] = _denial_class_for_reason(reason)
        details["denial_reason"] = classification.get("denied_reason") or "not_cursor_session_entrypoint"
        if classification.get("adapter_hint"):
            details["suggested_adapter_flow"] = classification.get("adapter_hint")
    payload = {
        "event_id": f"{trace_id}:native:{uuid.uuid4().hex[:8]}",
        "hook": "pre_tool_use",
        "trace_id": trace_id,
        "status": "deny" if classification.get("permission") == "deny" else "allow",
        "timestamp": utc_now(),
        "project": (session or {}).get("project") or "agent",
        "session_id": (session or {}).get("session_id"),
        "details": merge_governance_observability(details),
    }
    return run_event(payload)


def _denial_class_for_reason(reason: str) -> str:
    if reason in {"native_without_session", "mutating_native_without_session"}:
        return "recoverable"
    if reason == "bridge_requires_structured_adapter":
        return "terminal"
    return "other"


def _hook_response(classification: dict[str, Any]) -> dict[str, Any]:
    permission = classification.get("permission") or "allow"
    if permission != "deny":
        return {"permission": "allow"}
    reason = str(classification.get("reason") or "")
    hint = classification.get("adapter_hint") or ""
    if reason == "bridge_requires_structured_adapter":
        cmd = hint or "python3 scripts/cursor_session.py tool --adapter read_file --path <path>"
        return {
            "permission": "deny",
            "user_message": "Next: run governed adapter command below",
            "agent_message": (
                "RECOVERY (terminal denial — use adapter, not native tool):\n  "
                + cmd
            ),
            "recovery_command": cmd,
            "denial_class": "terminal",
        }
    if reason in {"native_without_session", "mutating_native_without_session"}:
        recovery = "python3 scripts/cursor_session.py start --task '<describe your task>'"
        return {
            "permission": "deny",
            "user_message": "Next: start governed session (command below)",
            "agent_message": (
                "RECOVERY (recoverable denial — start session first):\n  "
                + recovery
            ),
            "recovery_command": recovery,
            "denial_class": "recoverable",
        }
    return {
        "permission": "deny",
        "user_message": "Governed session required",
        "agent_message": (
            "Cursor-native enforcement blocked ungoverned tool use.\n"
            "  python3 scripts/cursor_session.py start --task '<describe your task>'"
        ),
    }

def handle_session_start(payload: dict[str, Any]) -> dict[str, Any]:
    write_enforcement_state()
    task = str(payload.get("task") or payload.get("prompt") or payload.get("user_message") or "Cursor governed session")
    if not enforcement_enabled():
        return {"ok": True, "enforcement_mode": "rollback", "session": None}
    ensured = ensure_governed_session(task=task)
    return {"ok": True, "enforcement_mode": "enforce", "session": ensured}


def handle_intercept(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    write_enforcement_state()
    session = load_active_session()
    tool_name = _tool_name_from_payload(payload)
    command = payload.get("command") or payload.get("shell_command")
    if not command and payload.get("tool_input"):
        command = json.dumps(payload.get("tool_input"), sort_keys=True)
    classification = classify_native_use(
        event=event,
        tool_name=tool_name,
        command=str(command) if command else None,
        session=session,
        enforcement_on=enforcement_enabled(),
    )
    record_native_event(
        event=event,
        classification=classification,
        session=session,
        tool_name=tool_name,
        command=str(command) if command else None,
    )
    response = _hook_response(classification)
    response["classification"] = {
        "tier": classification.get("tier"),
        "reason": classification.get("reason"),
        "degraded_reason": classification.get("degraded_reason"),
        "denied_reason": classification.get("denied_reason"),
        "enforcement_mode": enforcement_mode(),
        "rollout_stage": rollout_stage(),
        "governance_attribution": governance_attribution(),
    }
    return response


def process_hook_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event == "sessionStart":
        return handle_session_start(payload)
    return handle_intercept(event, payload)


def _self_check() -> int:
    write_enforcement_state()
    assert enforcement_mode() in {"enforce", "rollback"}

    denied = classify_native_use(
        event="beforeShellExecution",
        tool_name="Shell",
        command="git status",
        session=None,
        enforcement_on=True,
    )
    assert denied["permission"] == "deny"
    assert denied["tier"] == UNGCOVERNED

    old = os.environ.get(ENFORCEMENT_ENV)
    os.environ[ENFORCEMENT_ENV] = "false"
    try:
        rollback = classify_native_use(
            event="beforeShellExecution",
            tool_name="Shell",
            command="git status",
            session=None,
            enforcement_on=False,
        )
        assert rollback["permission"] == "allow"
        assert rollback["tier"] == UNGCOVERNED
    finally:
        if old is None:
            os.environ.pop(ENFORCEMENT_ENV, None)
        else:
            os.environ[ENFORCEMENT_ENV] = old

    allowed = classify_native_use(
        event="beforeShellExecution",
        tool_name="Shell",
        command="python3 scripts/cursor_session.py start --task test",
        session=None,
        enforcement_on=True,
    )
    assert allowed["permission"] == "allow"

    fake_session = {"trace_id": "trace-bridge-test", "entrypoint": "cursor_session", "session_id": "s1", "project": "agent"}
    read_denied = classify_native_use(
        event="preToolUse",
        tool_name="Read",
        command=None,
        session=fake_session,
        enforcement_on=True,
    )
    assert read_denied["permission"] == "deny"
    assert read_denied["reason"] == "bridge_requires_structured_adapter"

    write_denied = classify_native_use(
        event="preToolUse",
        tool_name="Write",
        command=None,
        session=fake_session,
        enforcement_on=True,
    )
    assert write_denied["permission"] == "deny"
    assert "write_file" in (write_denied.get("adapter_hint") or "")

    print("cursor-native-enforcement: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Cursor-native enforcement hook handler.")
    parser.add_argument("--event", required=False)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--ensure-session", metavar="TASK")
    parser.add_argument("--classify-json")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()
    if args.ensure_session:
        print(json.dumps(ensure_governed_session(task=args.ensure_session), indent=2, sort_keys=True))
        return 0
    if args.classify_json:
        payload = json.loads(args.classify_json)
        event = str(args.event or "preToolUse")
        print(json.dumps(process_hook_event(event, payload), indent=2, sort_keys=True))
        return 0

    event = str(args.event or "preToolUse")
    payload = json.load(sys.stdin)
    result = process_hook_event(event, payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("permission") == "deny":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
