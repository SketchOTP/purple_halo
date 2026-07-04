#!/usr/bin/env python3
"""Build and upsert governance traces from runtime hook events."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from governance_terms import route_for_details
from governance_tier import build_governance_observability


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "project_memory" / "runtime"
TRACE_LOG = RUNTIME_DIR / "trace_records.jsonl"
TRACE_STATE_DIR = RUNTIME_DIR / "traces"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _post_json(url: str, payload: dict, api_key: str | None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _safe_trace_filename(trace_id: str) -> str:
    return trace_id.replace(":", "__")


def _state_path(trace_id: str) -> Path:
    return TRACE_STATE_DIR / f"{_safe_trace_filename(trace_id)}.json"


def _load_trace(trace_id: str) -> dict | None:
    path = _state_path(trace_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_trace(trace_id: str) -> dict | None:
    return _load_trace(trace_id)


def _coerce_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _extract_token_usage(payload: dict) -> dict[str, int]:
    details = payload.get("details") or {}
    nested = payload.get("token_usage") or details.get("token_usage") or {}
    prompt_tokens = _coerce_int(
        nested.get("prompt_tokens")
        or nested.get("input_tokens")
        or details.get("prompt_tokens")
        or details.get("input_tokens")
    )
    completion_tokens = _coerce_int(
        nested.get("completion_tokens")
        or nested.get("output_tokens")
        or details.get("completion_tokens")
        or details.get("output_tokens")
    )
    total_tokens = _coerce_int(
        nested.get("total_tokens")
        or nested.get("token_count")
        or details.get("total_tokens")
        or details.get("token_count")
    )
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    usage: dict[str, int] = {}
    if prompt_tokens is not None:
        usage["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        usage["completion_tokens"] = completion_tokens
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    return usage


def _merge_token_usage(existing: dict | None, observed: dict[str, int]) -> dict[str, int] | None:
    merged = dict(existing or {})
    for key, value in observed.items():
        merged[key] = max(int(merged.get(key, 0) or 0), value)
    if "total_tokens" not in merged:
        prompt = merged.get("prompt_tokens")
        completion = merged.get("completion_tokens")
        if prompt is not None and completion is not None:
            merged["total_tokens"] = prompt + completion
    return merged or None


def _extract_provenance(payload: dict) -> dict:
    details = payload.get("details") or {}
    nested = payload.get("provenance") or details.get("provenance") or {}
    observed: dict[str, int | str] = {}
    for key in ("model", "provider"):
        value = nested.get(key) or details.get(key)
        if value:
            observed[key] = str(value)
    for key, aliases in {
        "tool_call_count": ("tool_call_count",),
        "approval_gate_count": ("approval_gate_count", "architect_call_count"),
        "memory_recall_count": ("memory_recall_count", "memory_hits"),
    }.items():
        value = None
        for alias in aliases:
            value = nested.get(alias)
            if value is None:
                value = details.get(alias)
            if value is not None:
                break
        number = _coerce_int(value)
        if number is not None:
            observed[key] = number
    return observed


def _merge_provenance(existing: dict | None, observed: dict, *, hook: str, tool_name: str | None, route: str | None) -> dict:
    merged = dict(existing or {})
    for key in ("model", "provider"):
        if observed.get(key):
            merged[key] = observed[key]
    for key in ("tool_call_count", "approval_gate_count", "memory_recall_count"):
        merged[key] = int(merged.get(key, 0) or 0) + int(observed.get(key, 0) or 0)
    if hook == "pre_tool_use" and tool_name:
        merged["tool_call_count"] = int(merged.get("tool_call_count", 0) or 0) + 1
        if tool_name.startswith(("approval", "governance")) or (route or "").startswith(("approval", "architect")):
            merged["approval_gate_count"] = int(merged.get("approval_gate_count", 0) or 0) + 1
        if tool_name in {"memory_recall", "memory.recall", "memory_search", "memory.search"}:
            merged["memory_recall_count"] = int(merged.get("memory_recall_count", 0) or 0) + 1
    return merged


def _extract_code_navigation_pack_ids(payload: dict) -> list[str]:
    details = payload.get("details") or {}
    packs = details.get("code_navigation_pack_ids")
    if packs is None:
        packs = ((details.get("context_plan") or {}).get("code_navigation_pack_ids"))
    if packs is None:
        packs = (((details.get("context_plan") or {}).get("code_navigation")) or {}).get("pack_ids")
    out: list[str] = []
    for value in packs or []:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _extract_navigation_required(payload: dict) -> bool | None:
    details = payload.get("details") or {}
    value = details.get("navigation_required")
    if value is None:
        value = (details.get("context_plan") or {}).get("navigation_required")
    if value is None:
        return None
    return bool(value)


def _extract_loaded_mcps(payload: dict) -> list[str]:
    details = payload.get("details") or {}
    values = details.get("loaded_mcps")
    if values is None:
        values = (details.get("context_plan") or {}).get("mcp_preflight")
    seen: set[str] = set()
    out: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _extract_route_evidence(payload: dict) -> list[str]:
    details = payload.get("details") or {}
    context_plan = details.get("context_plan") or {}
    values = (
        details.get("route_evidence")
        or context_plan.get("mcp_actions")
        or context_plan.get("navigation_preference")
        or []
    )
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _extract_tool_evidence(payload: dict) -> list[str]:
    details = payload.get("details") or {}
    values = details.get("tool_evidence") or []
    tool_name = details.get("tool_name")
    command = details.get("command")
    if tool_name:
        values = [*values, f"tool:{tool_name}"]
    if command:
        values = [*values, f"command:{command}"]
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _extract_repair_source(payload: dict) -> str | None:
    details = payload.get("details") or {}
    source = details.get("repair_source")
    if not source:
        return None
    normalized = str(source).strip().lower()
    aliases = {
        "direct_retry": "direct_retry",
        "retry": "direct_retry",
        "governance_replan": "governance_replan",
        "approval_replan": "governance_replan",
        "architect_replan": "governance_replan",
        "replan": "governance_replan",
        "verification_feedback": "verification_feedback",
        "verification": "verification_feedback",
        "memory_recall": "memory_recall",
        "memory": "memory_recall",
    }
    return aliases.get(normalized)


def _merge_repair_attribution(existing: dict | None, repair_source: str | None) -> dict | None:
    merged = dict(existing or {})
    if repair_source:
        merged[repair_source] = int(merged.get(repair_source, 0) or 0) + 1
    return merged or None


def _extract_lineage(payload: dict, current_trace: dict) -> dict:
    details = payload.get("details") or {}
    nested = details.get("lineage") or {}
    lineage_id = str(
        nested.get("lineage_id")
        or details.get("lineage_id")
        or (current_trace.get("lineage") or {}).get("lineage_id")
        or current_trace["trace_id"]
    )
    parent_trace_id = (
        nested.get("parent_trace_id")
        or details.get("parent_trace_id")
        or (current_trace.get("lineage") or {}).get("parent_trace_id")
    )
    resumed_from_trace_id = (
        nested.get("resumed_from_trace_id")
        or details.get("resumed_from_trace_id")
        or (current_trace.get("lineage") or {}).get("resumed_from_trace_id")
    )
    resume_depth = _coerce_int(
        nested.get("resume_depth")
        or details.get("resume_depth")
        or (current_trace.get("lineage") or {}).get("resume_depth")
        or 0
    ) or 0
    lineage = {
        "lineage_id": lineage_id,
        "resume_depth": resume_depth,
    }
    if parent_trace_id:
        lineage["parent_trace_id"] = str(parent_trace_id)
    if resumed_from_trace_id:
        lineage["resumed_from_trace_id"] = str(resumed_from_trace_id)
    return lineage


def _default_trace(payload: dict) -> dict:
    details = payload.get("details") or {}
    tool_name = details.get("tool_name")
    route = route_for_details(details)
    task = details.get("task") or details.get("prompt") or f"{payload.get('hook')}:{tool_name or 'unknown'}"
    return {
        "trace_id": payload["trace_id"],
        "task": task,
        "route": route,
        "status": "partial",
        "started_at": payload.get("timestamp") or utc_now(),
        "ended_at": None,
        "tool_loadout": [tool_name] if tool_name else [],
        "steps": [
            {
                "name": "hook_runtime_started",
                "status": "done",
                "notes": f"created from {payload.get('hook')}",
                "artifacts": [],
            }
        ],
        "verification": [],
        "token_usage": _extract_token_usage(payload) or None,
        "provenance": _extract_provenance(payload) or None,
        "repair_attribution": None,
        "lineage": {
            "lineage_id": payload["trace_id"],
            "resume_depth": 0,
        },
        "eval_id": payload.get("eval_id"),
        "resume_id": details.get("resume_id"),
        "project": payload.get("project"),
        "session_id": payload.get("session_id"),
        "routing_context": {
            "loaded_mcps": _extract_loaded_mcps(payload),
            "navigation_required": _extract_navigation_required(payload),
            "code_navigation_pack_ids": _extract_code_navigation_pack_ids(payload),
            "route_evidence": _extract_route_evidence(payload),
            "tool_evidence": _extract_tool_evidence(payload),
        },
    }


def _append_step(
    trace: dict,
    *,
    name: str,
    status: str,
    notes: str = "",
    artifacts: list[str] | None = None,
    token_usage: dict[str, int] | None = None,
) -> None:
    step = {
        "name": name,
        "status": status,
        "notes": notes,
        "artifacts": list(artifacts or []),
    }
    if token_usage:
        step["token_usage"] = token_usage
    trace.setdefault("steps", []).append(step)


def build_trace(existing: dict | None, payload: dict) -> dict:
    trace = dict(existing or _default_trace(payload))
    details = payload.get("details") or {}
    hook = payload.get("hook")
    trace["trace_id"] = payload["trace_id"]
    trace["project"] = payload.get("project") or trace.get("project")
    trace["session_id"] = payload.get("session_id") or trace.get("session_id")
    trace["eval_id"] = payload.get("eval_id") or trace.get("eval_id")
    trace["resume_id"] = details.get("resume_id") or trace.get("resume_id") or f"resume:{trace['trace_id']}"
    trace["route"] = route_for_details(details, default=str(trace.get("route") or "direct"))
    trace["task"] = details.get("task") or trace.get("task") or "unknown task"
    trace["started_at"] = trace.get("started_at") or payload.get("timestamp") or utc_now()
    trace.setdefault("tool_loadout", [])
    trace.setdefault("steps", [])
    trace.setdefault("verification", [])
    trace["token_usage"] = _merge_token_usage(trace.get("token_usage"), _extract_token_usage(payload))
    trace["provenance"] = _merge_provenance(
        trace.get("provenance"),
        _extract_provenance(payload),
        hook=hook,
        tool_name=details.get("tool_name"),
        route=trace.get("route"),
    )
    trace["repair_attribution"] = _merge_repair_attribution(
        trace.get("repair_attribution"),
        _extract_repair_source(payload),
    )
    trace["lineage"] = _extract_lineage(payload, trace)
    trace["routing_context"] = dict(trace.get("routing_context") or {})
    loaded_mcps = _extract_loaded_mcps(payload)
    if loaded_mcps:
        existing_loaded = list(trace["routing_context"].get("loaded_mcps") or [])
        for item in loaded_mcps:
            if item not in existing_loaded:
                existing_loaded.append(item)
        trace["routing_context"]["loaded_mcps"] = existing_loaded
    navigation_required = _extract_navigation_required(payload)
    if navigation_required is not None:
        trace["routing_context"]["navigation_required"] = navigation_required
    observed_pack_ids = _extract_code_navigation_pack_ids(payload)
    if observed_pack_ids:
        existing_pack_ids = list(trace["routing_context"].get("code_navigation_pack_ids") or [])
        for item in observed_pack_ids:
            if item not in existing_pack_ids:
                existing_pack_ids.append(item)
        trace["routing_context"]["code_navigation_pack_ids"] = existing_pack_ids
    route_evidence = _extract_route_evidence(payload)
    if route_evidence:
        existing_route_evidence = list(trace["routing_context"].get("route_evidence") or [])
        for item in route_evidence:
            if item not in existing_route_evidence:
                existing_route_evidence.append(item)
        trace["routing_context"]["route_evidence"] = existing_route_evidence
    tool_evidence = _extract_tool_evidence(payload)
    if tool_evidence:
        existing_tool_evidence = list(trace["routing_context"].get("tool_evidence") or [])
        for item in tool_evidence:
            if item not in existing_tool_evidence:
                existing_tool_evidence.append(item)
        trace["routing_context"]["tool_evidence"] = existing_tool_evidence
    governance = build_governance_observability(details)
    for key in (
        "governance_tier",
        "governance_entrypoint",
        "degraded_reason",
        "denied_reason",
        "ungoverned_success_risk",
        "is_fully_governed",
    ):
        if governance.get(key) is not None:
            trace["routing_context"][key] = governance[key]
    pack_quality = dict(details.get("pack_quality") or {})
    for key in ("pack_quality_state", "pack_relevance_score", "pack_token_estimate", "pack_file_count"):
        value = details.get(key) or pack_quality.get(key)
        if value is not None:
            trace["routing_context"][key] = value
    if pack_quality:
        trace["routing_context"]["pack_quality"] = pack_quality

    tool_name = details.get("tool_name")
    observed_tokens = _extract_token_usage(payload)
    if tool_name and tool_name not in trace["tool_loadout"]:
        trace["tool_loadout"].append(tool_name)

    if hook == "session_start":
        trace["status"] = "partial"
        _append_step(
            trace,
            name="session_start",
            status="done",
            notes=trace.get("task") or "",
            artifacts=list(details.get("artifacts") or []),
            token_usage=observed_tokens or None,
        )
    elif hook == "pre_tool_use":
        _append_step(
            trace,
            name=f"pre:{tool_name or 'tool'}",
            status="done" if payload.get("status") != "deny" else "blocked",
            notes=details.get("command") or "",
            artifacts=list(details.get("artifacts") or []),
            token_usage=observed_tokens or None,
        )
        if payload.get("status") == "deny":
            trace["status"] = "blocked"
    elif hook == "post_tool_use":
        exit_code = details.get("exit_code")
        step_status = "done" if exit_code in {None, 0} else "failed"
        _append_step(
            trace,
            name=f"post:{tool_name or 'tool'}",
            status=step_status,
            notes=f"exit_code={exit_code}" if exit_code is not None else "",
            artifacts=list(details.get("artifacts") or []),
            token_usage=observed_tokens or None,
        )
        if step_status == "failed":
            trace["status"] = "failed"
    elif hook == "verification_complete":
        verification = details.get("verification") or details.get("checks") or []
        trace["verification"] = [dict(item) for item in verification]
        _append_step(
            trace,
            name="verification_complete",
            status="done",
            notes=f"checks={len(verification)}",
            artifacts=list(details.get("artifacts") or []),
            token_usage=observed_tokens or None,
        )
        if verification:
            results = {item.get("result") for item in verification}
            if "fail" in results:
                trace["status"] = "failed"
            elif "blocked" in results:
                trace["status"] = "blocked"
    elif hook == "session_end":
        final_status = str(details.get("final_status") or "").lower()
        _append_step(
            trace,
            name="session_end",
            status="done",
            notes=final_status,
            artifacts=list(details.get("artifacts") or []),
            token_usage=observed_tokens or None,
        )
        trace["ended_at"] = payload.get("timestamp") or utc_now()
        if final_status in {"complete", "completed", "success"}:
            trace["status"] = "complete"
        elif final_status in {"blocked", "failed", "partial"}:
            trace["status"] = final_status

    return trace


def write_trace(payload: dict) -> dict:
    trace_id = str(payload.get("trace_id") or "")
    if not trace_id:
        raise ValueError("trace_id is required")
    TRACE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    existing = _load_trace(trace_id)
    trace = build_trace(existing, payload)
    state_path = _state_path(trace_id)
    state_path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with TRACE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trace, sort_keys=True) + "\n")
    endpoint = os.environ.get("MIMIR_ENDPOINT", "").rstrip("/")
    if endpoint:
        api_result = _post_json(
            f"{endpoint}/api/governance/traces",
            trace,
            os.environ.get("MIMIR_API_KEY"),
        )
        trace["mimir"] = api_result
    return trace


def _self_check() -> int:
    trace_id = "trace-self"
    samples = [
        {
            "trace_id": trace_id,
            "hook": "session_start",
            "status": "observe",
            "timestamp": utc_now(),
            "details": {"task": "run tests", "route": "direct"},
        },
        {
            "trace_id": trace_id,
            "hook": "pre_tool_use",
            "status": "allow",
            "timestamp": utc_now(),
            "details": {"tool_name": "shell", "command": "pytest -q", "task": "run tests", "prompt_tokens": 120, "completion_tokens": 30},
        },
        {
            "trace_id": trace_id,
            "hook": "post_tool_use",
            "status": "observe",
            "timestamp": utc_now(),
            "details": {"tool_name": "shell", "exit_code": 0},
        },
        {
            "trace_id": trace_id,
            "hook": "verification_complete",
            "status": "allow",
            "eval_id": "eval-self",
            "timestamp": utc_now(),
            "details": {
                "verification": [
                    {"label": "pytest", "command": "pytest -q", "result": "pass", "evidence": "ok"}
                ]
            },
        },
        {
            "trace_id": trace_id,
            "hook": "session_end",
            "status": "observe",
            "timestamp": utc_now(),
            "details": {"final_status": "complete"},
        },
    ]
    trace = None
    for payload in samples:
        trace = write_trace(payload)
    assert trace is not None
    assert trace["status"] == "complete"
    assert "shell" in trace["tool_loadout"]
    assert len(trace["verification"]) == 1
    assert trace["token_usage"]["total_tokens"] == 150
    assert trace["provenance"]["tool_call_count"] >= 1
    assert "routing_context" in trace
    print("trace-writer: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build or enrich a trace from a hook payload.")
    parser.add_argument("--payload-json", help="Inline JSON hook payload")
    parser.add_argument("--payload-file", help="Path to JSON hook payload")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()
    if not args.payload_json and not args.payload_file:
        raise SystemExit("provide --payload-json or --payload-file")
    if args.payload_json:
        payload = json.loads(args.payload_json)
    else:
        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    trace = write_trace(payload)
    print(json.dumps(trace, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
