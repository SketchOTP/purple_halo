#!/usr/bin/env python3
"""Cursor-facing governed session entrypoint for shell-backed execution.

CONTRACT BOUNDARY (canonical):
  The only trusted coding path is this governed session (`cursor_session.py start|tool|shell|complete`).
  Cursor-native direct MCP/tool use is DEGRADED / ADVISORY until routed through the same hooks,
  navigation packs, trace/eval/resume contract, and policy surface documented in docs/cold-handoff.md.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from governance_tier import CURSOR_SESSION_ENTRYPOINT
from mimir_code_nav import memory_record_outcome, mimir_available
from session_orchestrator import SessionOrchestrator, start_orchestrated_session
from session_runtime import refresh_session_navigation
from structured_adapters import resolve_install, resolve_run, execute_command as adapter_execute_command
from trace_writer import load_trace


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "project_memory" / "runtime"
ACTIVE_SESSION_PATH = RUNTIME_DIR / "cursor_active_session.json"


def _load_select_verification_helpers():
    module_path = ROOT / "scripts" / "select-verification.py"
    spec = importlib.util.spec_from_file_location("agent_select_verification", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load select-verification from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize_path, module.select_verification


normalize_path, select_verification = _load_select_verification_helpers()


def _default_metadata() -> dict[str, Any]:
    return {
        "command_history": [],
        "changed_files": [],
        "verification_runs": [],
        "last_verification_plan": None,
    }


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


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _load_active_record() -> dict[str, Any] | None:
    payload = _read_json(ACTIVE_SESSION_PATH)
    if not payload:
        return None
    if payload.get("workspace_root") != str(ROOT):
        return None
    payload["session"] = dict(payload.get("session") or {})
    payload["metadata"] = dict(payload.get("metadata") or _default_metadata())
    return payload


def _load_active_session() -> dict[str, Any] | None:
    payload = _load_active_record()
    if not payload:
        return None
    return dict(payload.get("session") or {})


def _save_active_record(session: dict[str, Any], metadata: dict[str, Any] | None = None) -> None:
    _write_json(
        ACTIVE_SESSION_PATH,
        {
            "workspace_root": str(ROOT),
            "session": session,
            "metadata": metadata or _default_metadata(),
        },
    )


def _save_active_session(session: dict[str, Any]) -> None:
    existing = _load_active_record() or {}
    _save_active_record(session, dict(existing.get("metadata") or _default_metadata()))


def _clear_active_session() -> None:
    try:
        ACTIVE_SESSION_PATH.unlink()
    except FileNotFoundError:
        return


def _git_path_lines(args: list[str]) -> list[str]:
    completed = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        return []
    return [normalize_path(line.strip()) for line in completed.stdout.splitlines() if line.strip()]


def _repo_changed_files() -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for group in (
        _git_path_lines(["git", "diff", "--name-only"]),
        _git_path_lines(["git", "diff", "--cached", "--name-only"]),
        _git_path_lines(["git", "ls-files", "--others", "--exclude-standard"]),
    ):
        for item in group:
            if item not in seen:
                seen.add(item)
                values.append(item)
    return values


def _changed_files_delta(before: list[str], after: list[str]) -> list[str]:
    delta: list[str] = []
    for item in after:
        if item not in before and item not in delta:
            delta.append(item)
    for item in before:
        if item not in after and item not in delta:
            delta.append(item)
    return delta


def _resolve_fail_closed_navigation(args: argparse.Namespace) -> bool | None:
    if getattr(args, "allow_without_navigation", False):
        return False
    if getattr(args, "fail_closed_navigation", False):
        return True
    return None


def _orchestrator_with_navigation(
    orchestrator: SessionOrchestrator,
    *,
    task: str | None,
    fail_closed_navigation: bool | None,
) -> SessionOrchestrator:
    session = refresh_session_navigation(orchestrator.session, task=task or orchestrator.task)
    session["entrypoint"] = CURSOR_SESSION_ENTRYPOINT
    return SessionOrchestrator(session, fail_closed_navigation=fail_closed_navigation)


def _load_or_start_session(
    *,
    task: str | None,
    route: str,
    project: str | None,
    fail_closed_navigation: bool | None,
    new_session: bool = False,
) -> SessionOrchestrator:
    if not new_session:
        existing_record = _load_active_record()
        if existing_record:
            orchestrator = SessionOrchestrator(
                existing_record["session"],
                fail_closed_navigation=fail_closed_navigation,
            )
            orchestrator = _orchestrator_with_navigation(
                orchestrator,
                task=task or orchestrator.task,
                fail_closed_navigation=fail_closed_navigation,
            )
            _save_active_record(orchestrator.session, dict(existing_record.get("metadata") or _default_metadata()))
            return orchestrator
    if not task:
        raise ValueError("task is required when no active Cursor session exists")
    orchestrator = start_orchestrated_session(
        task=task,
        route=route,
        project=project,
        fail_closed_navigation=fail_closed_navigation,
    )
    orchestrator = _orchestrator_with_navigation(
        orchestrator,
        task=task,
        fail_closed_navigation=fail_closed_navigation,
    )
    _save_active_record(orchestrator.session, _default_metadata())
    return orchestrator


def _execute_shell(command: str, *, cwd: str | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        shell=True,
        cwd=cwd or str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {
        "command": command,
        "cwd": cwd or str(ROOT),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "exit_code": int(completed.returncode),
    }


def _execute_python_action(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    return action()


def _tail_text(text: str, limit: int = 240) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _command_entry(
    *,
    tool_name: str,
    command: str,
    action_class: str,
    target_paths: list[str],
    result: dict[str, Any],
    changed_files: list[str],
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "command": command,
        "action_class": action_class,
        "target_paths": target_paths,
        "exit_code": int(result.get("exit_code") if result.get("exit_code") is not None else (1 if result.get("blocked") else 0)),
        "changed_files": list(changed_files),
    }


def _verification_result_from_command(command: str, result: dict[str, Any], blocked_reason: str | None = None) -> dict[str, Any]:
    if blocked_reason:
        return {
            "label": command,
            "command": command,
            "result": "blocked",
            "evidence": blocked_reason,
        }
    exit_code = int(result.get("exit_code") or 0)
    payload = dict(result.get("result") or {})
    evidence = _tail_text("\n".join(filter(None, [str(payload.get("stdout") or ""), str(payload.get("stderr") or "")])))
    return {
        "label": command,
        "command": command,
        "result": "pass" if exit_code == 0 else "fail",
        "evidence": evidence or f"exit_code={exit_code}",
    }


def _map_final_result_to_task_outcome(final_status: str) -> str:
    status = final_status.strip().lower()
    if status in {"complete", "completed", "success"}:
        return "success"
    if status in {"blocked", "failed", "failure"}:
        return "failure"
    return "partial"


def _build_memory_outcome(
    *,
    orchestrator: SessionOrchestrator,
    metadata: dict[str, Any],
    final_status: str,
    eval_id: str,
    verification: list[dict[str, Any]],
) -> tuple[str, str]:
    changed_files = list(metadata.get("changed_files") or [])
    verification_summary = ", ".join(
        f"{item.get('label')}: {item.get('result')}" for item in verification
    ) or "none"
    content = (
        f"task={orchestrator.task}; result={final_status.upper()}; route={orchestrator.route}; "
        f"changed_files={changed_files or ['none']}; verification={verification_summary}; "
        f"trace_id={orchestrator.trace_id}; eval_id={eval_id}"
    )
    return content, _map_final_result_to_task_outcome(final_status)


def _record_memory_outcome(
    *,
    orchestrator: SessionOrchestrator,
    metadata: dict[str, Any],
    final_status: str,
    eval_id: str,
    verification: list[dict[str, Any]],
) -> dict[str, Any]:
    if not mimir_available():
        return {"ok": False, "blocked_reason": "MIMIR_ENDPOINT unavailable"}
    content, task_outcome = _build_memory_outcome(
        orchestrator=orchestrator,
        metadata=metadata,
        final_status=final_status,
        eval_id=eval_id,
        verification=verification,
    )
    try:
        payload = memory_record_outcome(
            content=content,
            result=final_status.upper(),
            project=orchestrator.project,
            session_id=orchestrator.session_id,
            task_outcome=task_outcome,
            has_harmful_outcome=task_outcome == "failure",
        )
        return {"ok": True, "payload": payload}
    except Exception as exc:
        return {"ok": False, "blocked_reason": str(exc)}


def _execute_with_tracking(
    *,
    orchestrator: SessionOrchestrator,
    metadata: dict[str, Any],
    tool_name: str,
    command: str,
    action_class: str,
    target_paths: list[str],
    cwd: str | None = None,
    raw_shell_allowed: bool = False,
    executor: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    before = _repo_changed_files()
    captured: dict[str, Any] = {}

    def _executor() -> dict[str, Any]:
        payload = executor() if executor is not None else _execute_shell(command, cwd=cwd)
        after_now = _repo_changed_files()
        changed_now = _changed_files_delta(before, after_now)
        payload["artifacts"] = list(changed_now)
        captured["changed_files"] = list(changed_now)
        return payload

    result = orchestrator.run_tool(
        tool_name=tool_name,
        command=command,
        details={
            "cwd": cwd or str(ROOT),
            "action_class": action_class,
            "target_paths": target_paths,
            "raw_shell_allowed": raw_shell_allowed,
        },
        post_details={},
        executor=_executor,
    )
    changed_delta = list(captured.get("changed_files") or [])
    if result.get("post_tool"):
        post_trace = (((result.get("post_tool") or {}).get("trace")) or {})
        if post_trace:
            pass
    entry = _command_entry(
        tool_name=tool_name,
        command=command,
        action_class=action_class,
        target_paths=target_paths,
        result=result,
        changed_files=changed_delta,
    )
    history = list(metadata.get("command_history") or [])
    history.append(entry)
    metadata["command_history"] = history
    cumulative = list(metadata.get("changed_files") or [])
    for item in changed_delta:
        if item not in cumulative:
            cumulative.append(item)
    metadata["changed_files"] = cumulative
    if result.get("post_tool") and isinstance(result["post_tool"], dict):
        result["post_tool"]["observed_changed_files"] = changed_delta
    return result


def _list_files(path: str | None, pattern: str | None) -> dict[str, Any]:
    root = _resolve_path(path or ".")
    if not root.exists():
        return {"exit_code": 1, "stderr": f"path not found: {root}", "stdout": ""}
    if root.is_file():
        files = [str(root.relative_to(ROOT)).replace("\\", "/")]
    else:
        matcher = pattern or "**/*"
        files = [
            str(item.relative_to(ROOT)).replace("\\", "/")
            for item in sorted(root.glob(matcher))
            if item.is_file()
        ]
    return {"exit_code": 0, "stdout": "\n".join(files), "stderr": ""}


def _read_file(path: str, lines: int) -> dict[str, Any]:
    target = _resolve_path(path)
    if not target.is_file():
        return {"exit_code": 1, "stdout": "", "stderr": f"file not found: {target}"}
    content = target.read_text(encoding="utf-8")
    return {"exit_code": 0, "stdout": "\n".join(content.splitlines()[:lines]), "stderr": ""}


def _write_file(path: str, content: str) -> dict[str, Any]:
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"exit_code": 0, "stdout": f"wrote {target}", "stderr": ""}


def _append_file(path: str, content: str) -> dict[str, Any]:
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(content)
    return {"exit_code": 0, "stdout": f"appended {target}", "stderr": ""}


def _replace_text(path: str, old: str, new: str, count: int | None = None) -> dict[str, Any]:
    target = _resolve_path(path)
    if not target.is_file():
        return {"exit_code": 1, "stdout": "", "stderr": f"file not found: {target}"}
    content = target.read_text(encoding="utf-8")
    if old not in content:
        return {"exit_code": 1, "stdout": "", "stderr": "old text not found"}
    replaced = content.replace(old, new, count if count is not None and count >= 0 else -1)
    target.write_text(replaced, encoding="utf-8")
    return {"exit_code": 0, "stdout": f"updated {target}", "stderr": ""}


def _runtime_shell_command(command: str) -> str:
    adapted = command.strip()
    if os.name != "nt":
        return adapted
    if adapted.startswith("./scripts/") and adapted.endswith(".sh"):
        return f"bash {adapted[2:]}"
    if adapted.startswith("python3 "):
        return "py -3 " + adapted[len("python3 ") :]
    return adapted


def _plan_verification(metadata: dict[str, Any]) -> dict[str, Any]:
    changed_files = list(metadata.get("changed_files") or [])
    plan = select_verification(changed_files)
    metadata["last_verification_plan"] = plan
    return plan


def _auto_run_verification(
    *,
    orchestrator: SessionOrchestrator,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    plan = _plan_verification(metadata)
    checks: list[dict[str, Any]] = []
    for command in plan.get("commands") or []:
        runtime_command = _runtime_shell_command(command)
        result = _execute_with_tracking(
            orchestrator=orchestrator,
            metadata=metadata,
            tool_name="shell.verify",
            command=runtime_command,
            action_class="verify",
            target_paths=list(metadata.get("changed_files") or []),
        )
        checks.append(
            _verification_result_from_command(
                command,
                result,
                blocked_reason=str(result.get("error") or "") if result.get("blocked") else None,
            )
        )
    metadata["verification_runs"] = [*(metadata.get("verification_runs") or []), *checks]
    return checks


def start_command(args: argparse.Namespace) -> int:
    fail_closed = _resolve_fail_closed_navigation(args)
    orchestrator = start_orchestrated_session(
        task=args.task,
        route=args.route,
        project=args.project,
        run_kind=getattr(args, "run_kind", None),
        fail_closed_navigation=fail_closed,
    )
    orchestrator = _orchestrator_with_navigation(
        orchestrator,
        task=args.task,
        fail_closed_navigation=fail_closed,
    )
    _save_active_record(orchestrator.session, _default_metadata())
    result = {
        "ok": orchestrator.session.get("ok", True),
        "trace_id": orchestrator.trace_id,
        "session_id": orchestrator.session_id,
        "route": orchestrator.route,
        "context_plan": orchestrator.context_plan,
        "navigation": orchestrator.navigation,
        "active_session_path": str(ACTIVE_SESSION_PATH),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def shell_command(args: argparse.Namespace) -> int:
    record = _load_active_record() or {"metadata": _default_metadata()}
    fail_closed = _resolve_fail_closed_navigation(args)
    orchestrator = _load_or_start_session(
        task=args.task,
        route=args.route,
        project=args.project,
        fail_closed_navigation=fail_closed,
        new_session=args.new_session,
    )
    metadata = dict(record.get("metadata") or _default_metadata())
    result = _execute_with_tracking(
        orchestrator=orchestrator,
        metadata=metadata,
        tool_name=args.tool_name,
        command=args.shell_text,
        action_class="raw_shell",
        target_paths=list(args.target_path or []),
        cwd=args.cwd,
        raw_shell_allowed=args.allow_raw_shell,
    )
    _save_active_record(orchestrator.session, metadata)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


def tool_command(args: argparse.Namespace) -> int:
    record = _load_active_record() or {"metadata": _default_metadata()}
    fail_closed = _resolve_fail_closed_navigation(args)
    orchestrator = _load_or_start_session(
        task=args.task,
        route=args.route,
        project=args.project,
        fail_closed_navigation=fail_closed,
        new_session=args.new_session,
    )
    metadata = dict(record.get("metadata") or _default_metadata())
    if args.adapter == "search_code":
        if not args.pattern:
            raise SystemExit("--pattern is required for search_code")
        target_root = args.path or "."
        command = f'rg -n --hidden --glob "!project_memory/runtime/**" {json.dumps(args.pattern)} {json.dumps(target_root)}'
        target_paths = [normalize_path(target_root)]
        action_class = "search_code"
        executor = None
    elif args.adapter == "read_file":
        if not args.path:
            raise SystemExit("--path is required for read_file")
        target_root = normalize_path(args.path)
        max_lines = int(args.lines or 200)
        command = f"read_file {target_root}"
        target_paths = [target_root]
        action_class = "read_file"
        executor = lambda: _execute_python_action(lambda: _read_file(target_root, max_lines))
    elif args.adapter == "list_files":
        target_root = normalize_path(args.path or ".")
        command = f"list_files {target_root}"
        target_paths = [target_root]
        action_class = "list_files"
        executor = lambda: _execute_python_action(lambda: _list_files(target_root, args.pattern))
    elif args.adapter == "git_diff":
        target_paths = [normalize_path(path) for path in (args.path_list or [])]
        scope = " -- " + " ".join(target_paths) if target_paths else ""
        command = f"git diff{scope}"
        action_class = "git_diff"
        executor = None
    elif args.adapter == "git_status":
        target_paths = [normalize_path(path) for path in (args.path_list or [])]
        command = "git status --short"
        action_class = "git_status"
        executor = None
    elif args.adapter == "write_file":
        if not args.path:
            raise SystemExit("--path is required for write_file")
        if args.text is None:
            raise SystemExit("--text is required for write_file")
        target_root = normalize_path(args.path)
        command = f"write_file {target_root}"
        target_paths = [target_root]
        action_class = "write"
        executor = lambda: _execute_python_action(lambda: _write_file(target_root, args.text))
    elif args.adapter == "append_file":
        if not args.path:
            raise SystemExit("--path is required for append_file")
        if args.text is None:
            raise SystemExit("--text is required for append_file")
        target_root = normalize_path(args.path)
        command = f"append_file {target_root}"
        target_paths = [target_root]
        action_class = "append"
        executor = lambda: _execute_python_action(lambda: _append_file(target_root, args.text))
    elif args.adapter == "replace_text":
        if not args.path:
            raise SystemExit("--path is required for replace_text")
        if args.old_text is None or args.new_text is None:
            raise SystemExit("--old-text and --new-text are required for replace_text")
        target_root = normalize_path(args.path)
        command = f"replace_text {target_root}"
        target_paths = [target_root]
        action_class = "write"
        executor = lambda: _execute_python_action(lambda: _replace_text(target_root, args.old_text, args.new_text, args.count))
    elif args.adapter == "install_dependencies":
        plan = resolve_install(manifest_type=args.manifest or "auto", path=args.path or ".")
        target_paths = [normalize_path(args.path or ".")]
        action_class = "install_dependencies"
        if not plan.get("ok"):
            command = f"install_dependencies blocked:{plan.get('blocked_reason')}"
            executor = lambda: {
                "exit_code": 1,
                "stdout": "",
                "stderr": str(plan.get("message") or plan.get("blocked_reason") or "blocked"),
                "blocked_reason": plan.get("blocked_reason"),
            }
        else:
            command = str(plan["command_text"])
            executor = lambda: _execute_python_action(
                lambda: adapter_execute_command(plan["command"], cwd=_resolve_path(args.path or "."))
            )
    elif args.adapter == "run_application":
        plan = resolve_run(
            profile=args.run_profile,
            script_path=args.script_path,
            extra_args=list(args.extra_arg or []),
        )
        action_class = "run_application"
        if not plan.get("ok"):
            command = f"run_application blocked:{plan.get('blocked_reason')}"
            target_paths = []
            executor = lambda: {
                "exit_code": 1,
                "stdout": "",
                "stderr": str(plan.get("message") or plan.get("blocked_reason") or "blocked"),
                "blocked_reason": plan.get("blocked_reason"),
            }
        else:
            command = str(plan["command_text"])
            target_paths = list(plan.get("target_paths") or [])
            executor = None
    else:
        if not args.tool_command:
            raise SystemExit("--command is required for this adapter")
        command = args.tool_command
        target_paths = [normalize_path(path) for path in (args.path_list or [])]
        action_class = args.adapter
        executor = None
    result = _execute_with_tracking(
        orchestrator=orchestrator,
        metadata=metadata,
        tool_name=f"shell.{args.adapter}",
        command=command,
        action_class=action_class,
        target_paths=target_paths,
        cwd=args.cwd,
        executor=executor,
    )
    _save_active_record(orchestrator.session, metadata)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


def complete_command(args: argparse.Namespace) -> int:
    record = _load_active_record() or {"metadata": _default_metadata()}
    fail_closed = _resolve_fail_closed_navigation(args)
    orchestrator = _load_or_start_session(
        task=args.task,
        route=args.route,
        project=args.project,
        fail_closed_navigation=fail_closed,
        new_session=args.new_session,
    )
    metadata = dict(record.get("metadata") or _default_metadata())
    verification = json.loads(args.verification_json or "[]")
    auto_verification_plan = None
    if not verification:
        auto_verification_plan = _plan_verification(metadata)
        if auto_verification_plan.get("commands"):
            verification = _auto_run_verification(orchestrator=orchestrator, metadata=metadata)
        elif args.verification_blocked_reason:
            verification = [
                {
                    "label": "planned verification",
                    "command": "select_verification",
                    "result": "blocked",
                    "evidence": args.verification_blocked_reason,
                }
            ]
        elif metadata.get("changed_files"):
            result = {
                "ok": False,
                "error": "verification_required",
                "verification_plan": auto_verification_plan,
                "active_session_path": str(ACTIVE_SESSION_PATH),
            }
            _save_active_record(orchestrator.session, metadata)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 1
    eval_id = args.eval_id or f"eval:{uuid.uuid4().hex[:12]}"
    verified = orchestrator.verification_complete(
        eval_id=eval_id,
        verification=verification,
        details={
            **json.loads(args.details_json or "{}"),
            "artifacts": list(metadata.get("changed_files") or []),
        },
    )
    computed_final_status = args.final_status
    if any(item.get("result") == "fail" for item in verification) and computed_final_status == "complete":
        computed_final_status = "failed"
    if any(item.get("result") == "blocked" for item in verification) and computed_final_status == "complete":
        computed_final_status = "blocked"
    ended = orchestrator.session_end(
        final_status=computed_final_status,
        eval_id=eval_id,
        details={
            **json.loads(args.end_details_json or "{}"),
            "artifacts": list(metadata.get("changed_files") or []),
        },
    )
    memory_outcome = _record_memory_outcome(
        orchestrator=orchestrator,
        metadata=metadata,
        final_status=computed_final_status,
        eval_id=eval_id,
        verification=verification,
    )
    if not args.keep_open:
        _clear_active_session()
    else:
        _save_active_record(orchestrator.session, metadata)
    result = {
        "ok": bool(verified.get("ok")) and bool(ended.get("ok")),
        "eval_id": eval_id,
        "final_status": computed_final_status,
        "verification_complete": verified,
        "session_end": ended,
        "verification": verification,
        "verification_plan": auto_verification_plan,
        "memory_record_outcome": memory_outcome,
        "active_session_path": str(ACTIVE_SESSION_PATH),
        "session_cleared": not args.keep_open,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def status_command(args: argparse.Namespace) -> int:
    record = _load_active_record()
    session = dict((record or {}).get("session") or {})
    result = {
        "ok": bool(session),
        "session": session,
        "metadata": dict((record or {}).get("metadata") or _default_metadata()),
        "trace": load_trace(str(session.get("trace_id") or "")) if session else None,
        "active_session_path": str(ACTIVE_SESSION_PATH),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if session else 1


def reset_command(args: argparse.Namespace) -> int:
    _clear_active_session()
    print(json.dumps({"ok": True, "cleared": str(ACTIVE_SESSION_PATH)}, indent=2, sort_keys=True))
    return 0


def _self_check() -> int:
    _clear_active_session()
    orchestrator = _load_or_start_session(
        task="cursor session self-check",
        route="direct",
        project="agent",
        fail_closed_navigation=False,
        new_session=True,
    )
    assert orchestrator.trace_id
    metadata = _default_metadata()
    old_endpoint = os.environ.pop("MIMIR_ENDPOINT", None)
    # ponytail: test-harness only — avoids remote Mimir policy denying local hook self-check; not a runtime pattern
    try:
        shell_result = _execute_with_tracking(
            orchestrator=orchestrator,
            metadata=metadata,
            tool_name="shell.git_status",
            command="git status --short",
            action_class="git_status",
            target_paths=[],
        )
        assert shell_result["ok"] is True
        tool_result = _execute_with_tracking(
            orchestrator=orchestrator,
            metadata=metadata,
            tool_name="shell.list_files",
            command="list_files scripts",
            action_class="list_files",
            target_paths=["scripts"],
            executor=lambda: _execute_python_action(lambda: _list_files("scripts", "*.py")),
        )
        assert tool_result["ok"] is True
        denied_raw_shell = orchestrator.run_tool(
            tool_name="shell",
            command="echo should-block",
            details={
                "cwd": str(ROOT),
                "action_class": "raw_shell",
                "target_paths": [],
                "raw_shell_allowed": False,
                "navigation_required": True,
            },
            post_details={},
            executor=lambda: _execute_shell("echo should-block"),
        )
        assert denied_raw_shell["ok"] is False
        assert denied_raw_shell.get("blocked") is True
        denied_pre = dict(denied_raw_shell.get("pre_tool") or {})
        assert denied_pre.get("status") == "deny"
        checks = _auto_run_verification(orchestrator=orchestrator, metadata=metadata)
        assert isinstance(checks, list)
        verified = orchestrator.verification_complete(
            eval_id="eval-cursor-self",
            verification=[{"label": "self-check", "command": "python -c", "result": "pass", "evidence": "cursor-ok"}],
            details={"token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        )
        assert verified["ok"] is True
        ended = orchestrator.session_end(final_status="complete", eval_id="eval-cursor-self")
        assert ended["ok"] is True
    finally:
        if old_endpoint is not None:
            os.environ["MIMIR_ENDPOINT"] = old_endpoint
    _clear_active_session()
    print("cursor-session: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Cursor-governed session entrypoint.")
    subparsers = parser.add_subparsers(dest="subcommand")

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--task", required=True)
    start_parser.add_argument("--route", default="direct")
    start_parser.add_argument("--project", default="agent")
    start_parser.add_argument("--run-kind", choices=["governed_oriented", "validation_pressure", "unknown"], default="unknown")
    start_parser.add_argument("--fail-closed-navigation", action="store_true", default=None)
    start_parser.add_argument("--allow-without-navigation", action="store_true", default=None)

    shell_parser = subparsers.add_parser("shell")
    shell_parser.add_argument("--task")
    shell_parser.add_argument("--route", default="direct")
    shell_parser.add_argument("--project", default="agent")
    shell_parser.add_argument("--tool-name", default="shell")
    shell_parser.add_argument("--cwd")
    shell_parser.add_argument("--target-path", action="append")
    shell_parser.add_argument("--new-session", action="store_true")
    shell_parser.add_argument("--fail-closed-navigation", action="store_true", default=None)
    shell_parser.add_argument("--allow-without-navigation", action="store_true", default=None)
    shell_parser.add_argument("--allow-raw-shell", action="store_true")
    shell_parser.add_argument("--command", dest="shell_text", required=True)

    tool_parser = subparsers.add_parser("tool")
    tool_parser.add_argument("--task")
    tool_parser.add_argument("--route", default="direct")
    tool_parser.add_argument("--project", default="agent")
    tool_parser.add_argument("--cwd")
    tool_parser.add_argument("--new-session", action="store_true")
    tool_parser.add_argument("--fail-closed-navigation", action="store_true", default=None)
    tool_parser.add_argument("--allow-without-navigation", action="store_true", default=None)
    tool_parser.add_argument("--adapter", required=True, choices=["run_test", "search_code", "read_file", "list_files", "git_diff", "git_status", "lint", "format", "write_file", "append_file", "replace_text", "install_dependencies", "run_application"])
    tool_parser.add_argument("--manifest", default="auto", help="install_dependencies: auto|pip-editable|pip-requirements|npm-ci|npm-install")
    tool_parser.add_argument("--run-profile", choices=["verify", "governance_report", "select_context", "collect_evidence"])
    tool_parser.add_argument("--script-path", help="run_application: scripts/*.py under repo")
    tool_parser.add_argument("--extra-arg", action="append", default=[])
    tool_parser.add_argument("--command", dest="tool_command")
    tool_parser.add_argument("--pattern")
    tool_parser.add_argument("--path")
    tool_parser.add_argument("--path-list", action="append")
    tool_parser.add_argument("--lines", type=int)
    tool_parser.add_argument("--text")
    tool_parser.add_argument("--old-text")
    tool_parser.add_argument("--new-text")
    tool_parser.add_argument("--count", type=int)

    complete_parser = subparsers.add_parser("complete")
    complete_parser.add_argument("--task")
    complete_parser.add_argument("--route", default="direct")
    complete_parser.add_argument("--project", default="agent")
    complete_parser.add_argument("--new-session", action="store_true")
    complete_parser.add_argument("--fail-closed-navigation", action="store_true", default=None)
    complete_parser.add_argument("--allow-without-navigation", action="store_true", default=None)
    complete_parser.add_argument("--eval-id")
    complete_parser.add_argument("--verification-json", default="[]")
    complete_parser.add_argument("--details-json", default="{}")
    complete_parser.add_argument("--end-details-json", default="{}")
    complete_parser.add_argument("--verification-blocked-reason")
    complete_parser.add_argument("--final-status", default="complete")
    complete_parser.add_argument("--keep-open", action="store_true")

    subparsers.add_parser("status")
    subparsers.add_parser("reset")
    parser.add_argument("--self-check", action="store_true")

    args = parser.parse_args(argv)
    if args.self_check:
        return _self_check()
    if args.subcommand == "start":
        return start_command(args)
    if args.subcommand == "shell":
        return shell_command(args)
    if args.subcommand == "tool":
        return tool_command(args)
    if args.subcommand == "complete":
        return complete_command(args)
    if args.subcommand == "status":
        return status_command(args)
    if args.subcommand == "reset":
        return reset_command(args)
    raise SystemExit("use start, shell, tool, complete, status, or reset")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
