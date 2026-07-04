#!/usr/bin/env python3
"""Build and optionally publish governance eval payloads from verification results."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from pack_quality import assess_pack_quality
from trace_writer import load_trace


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "project_memory" / "runtime"
EVAL_LOG = RUNTIME_DIR / "eval_results.jsonl"
HOOK_LOG = RUNTIME_DIR / "hook_events.jsonl"


def _post_json(url: str, payload: dict, api_key: str | None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_checks(payload: dict) -> list[dict]:
    checks = payload.get("verification") or payload.get("checks") or []
    return [dict(item) for item in checks]


def _hook_failure_classes(trace_id: str) -> list[str]:
    if not HOOK_LOG.is_file() or not trace_id:
        return []
    classes: list[str] = []
    for line in HOOK_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(payload.get("trace_id") or "") != trace_id or str(payload.get("status") or "") != "deny":
            continue
        details = dict(payload.get("details") or {})
        denial_reason = str(details.get("denial_reason") or "")
        blocked_reason = str(details.get("blocked_reason") or "")
        if any(term in f"{denial_reason} {blocked_reason}" for term in ("raw_shell_requires_structured_adapter", "navigation_required_for_broad_edit")):
            classes.append("wrong_tool_selected")
        elif "navigation_required_but" in f"{denial_reason} {blocked_reason}":
            classes.append("verification_gap")
    return sorted(set(classes))


def _text_blob(check: dict) -> str:
    return " ".join(
        str(check.get(key) or "")
        for key in ("label", "command", "evidence")
    ).lower()


def infer_failure_class(check: dict) -> str | None:
    result = str(check.get("result") or "").lower()
    if result not in {"fail", "blocked", "not_run"}:
        return None
    blob = _text_blob(check)
    if result in {"blocked", "not_run"}:
        return "verification_gap"
    if any(term in blob for term in ("scope drift", "drift", "approved plan", ".architect/", "architect_review", "architect_release_gate")):
        return "architectural_drift"
    if any(term in blob for term in ("wrong tool", "tool selection", "route mismatch", "expected tool", "selected tool")):
        return "wrong_tool_selected"
    if any(term in blob for term in ("timeout", "timed out", "broken pipe", "connection reset", "connection refused", "temporary failure", "flaky", "transport error")):
        return "tool_flaky"
    if any(term in blob for term in ("pytest", "npm test", "go test", "cargo test", "unit test", "integration test", "failing test", "assertionerror")):
        return "test_regression"
    return "other"


def _score_fraction(passed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(max(0.0, min(1.0, passed / total)), 3)


def build_eval(payload: dict) -> dict:
    trace_id = str(payload.get("trace_id") or "")
    eval_id = str(payload.get("eval_id") or "")
    if not trace_id or not eval_id:
        raise ValueError("trace_id and eval_id are required")

    trace = load_trace(trace_id) or {}
    routing_context = dict(trace.get("routing_context") or {})
    code_navigation_pack_ids = list(
        payload.get("code_navigation_pack_ids")
        or routing_context.get("code_navigation_pack_ids")
        or []
    )
    pack_quality = dict(
        payload.get("pack_quality")
        or routing_context.get("pack_quality")
        or (routing_context.get("context_plan") or {}).get("pack_quality")
        or {}
    )
    if not pack_quality and routing_context.get("pack_quality_state"):
        pack_quality = {
            "pack_quality_state": routing_context.get("pack_quality_state"),
            "pack_relevance_score": routing_context.get("pack_relevance_score"),
            "pack_token_estimate": routing_context.get("pack_token_estimate"),
        }
    nav_payload = (routing_context.get("code_navigation") or {}) if not pack_quality else {}
    if not pack_quality.get("pack_quality_state") and code_navigation_pack_ids:
        pack_quality = assess_pack_quality(
            task=str(trace.get("task") or payload.get("task") or ""),
            nav_payload=nav_payload or {"pack_ids": code_navigation_pack_ids},
            verification_passed=None,
            wrong_tool=False,
        )
    checks = _normalize_checks(payload)
    total = len(checks)
    passed = sum(1 for check in checks if check.get("result") == "pass")
    failed = [check for check in checks if check.get("result") in {"fail", "blocked", "not_run"}]

    findings: list[dict] = []
    failure_classes: list[str] = []
    for check in failed:
        failure_class = infer_failure_class(check) or "other"
        check["failure_class"] = failure_class
        failure_classes.append(failure_class)
        severity = "high" if failure_class in {"architectural_drift", "wrong_tool_selected"} else "medium"
        if check.get("result") in {"blocked", "not_run"}:
            severity = "medium"
        findings.append(
            {
                "severity": severity,
                "message": f"{check.get('label', 'verification check')} => {check.get('result')}",
                "failure_class": failure_class,
                "repair_hint": f"inspect {check.get('command') or check.get('label')}",
            }
        )

    for failure_class in _hook_failure_classes(trace_id):
        if failure_class in failure_classes:
            continue
        failure_classes.append(failure_class)
        findings.append(
            {
                "severity": "high" if failure_class == "wrong_tool_selected" else "medium",
                "message": f"hook policy => {failure_class}",
                "failure_class": failure_class,
                "repair_hint": "inspect pre_tool_use denial and route through structured adapters",
            }
        )

    verification_passed = total > 0 and passed == total
    pack_quality = assess_pack_quality(
        task=str(trace.get("task") or payload.get("task") or ""),
        nav_payload={"pack_ids": code_navigation_pack_ids, **pack_quality},
        verification_passed=verification_passed,
        wrong_tool="wrong_tool_selected" in failure_classes,
        repair_loops=int((trace.get("repair_attribution") or {}).get("direct_retry", 0) or 0),
        failure_classes=failure_classes,
    )
    pack_state = str(pack_quality.get("pack_quality_state") or "absent")
    if pack_state in {"misleading", "wasteful", "stale"}:
        findings.append(
            {
                "severity": "medium" if pack_state == "stale" else "high",
                "message": f"pack quality => {pack_state} (score={pack_quality.get('pack_relevance_score')})",
                "failure_class": "verification_gap" if pack_state == "stale" else "wrong_tool_selected",
                "repair_hint": "re-run navigation or tighten retrieval before broad edits",
            }
        )

    outcome = "pass"
    if failed:
        outcome = "fail" if any(item["severity"] == "high" for item in findings) else "warn"

    verification_quality = _score_fraction(passed, total)
    goal_completion = 1.0 if outcome == "pass" else (0.5 if outcome == "warn" else 0.0)
    token_efficiency = round(float(payload.get("token_efficiency", 0.75)), 3)
    tool_selection = 0.0 if "wrong_tool_selected" in failure_classes else (0.5 if failed else 1.0)
    memory_quality = 0.5 if payload.get("used_memory") else 0.0

    pack_relevance = float(pack_quality.get("pack_relevance_score") or 0.0)
    if pack_state == "relevant":
        pack_relevance = max(pack_relevance, 0.75)
    elif pack_state in {"misleading", "absent"}:
        pack_relevance = min(pack_relevance, 0.25)

    return {
        "eval_id": eval_id,
        "trace_id": trace_id,
        "outcome": outcome,
        "scores": {
            "goal_completion": goal_completion,
            "verification_quality": verification_quality,
            "token_efficiency": token_efficiency,
            "tool_selection": tool_selection,
            "memory_quality": memory_quality,
            "pack_relevance": round(pack_relevance, 3),
        },
        "findings": findings,
        "failure_classes": sorted(set(failure_classes)),
        "code_navigation_pack_ids": code_navigation_pack_ids,
        "pack_quality": pack_quality,
        "token_usage": dict(payload.get("token_usage") or trace.get("token_usage") or {}),
        "project": payload.get("project"),
        "session_id": payload.get("session_id"),
        "verification": checks,
    }


def write_eval(payload: dict) -> dict:
    result = build_eval(payload)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with EVAL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")
    endpoint = os.environ.get("MIMIR_ENDPOINT", "").rstrip("/")
    if endpoint:
        api_result = _post_json(
            f"{endpoint}/api/governance/evals",
            {
                "eval_id": result["eval_id"],
                "trace_id": result["trace_id"],
                "outcome": result["outcome"],
                "scores": result["scores"],
                "findings": result["findings"],
                "failure_classes": result["failure_classes"],
                "code_navigation_pack_ids": result.get("code_navigation_pack_ids") or [],
                "pack_quality": result.get("pack_quality") or {},
                "token_usage": result.get("token_usage") or {},
                "project": result.get("project"),
                "session_id": result.get("session_id"),
            },
            os.environ.get("MIMIR_API_KEY"),
        )
        result["mimir"] = api_result
    return result


def _self_check() -> int:
    payload = {
        "trace_id": "trace-self",
        "eval_id": "eval-self",
        "project": "agent",
        "verification": [
            {"label": "pytest", "command": "pytest -q", "result": "fail", "evidence": "AssertionError in tests"},
            {"label": "architect review", "command": "architect_review_diff", "result": "fail", "evidence": "scope drift detected"},
            {"label": "networked tool", "command": "shell", "result": "fail", "evidence": "connection refused temporary failure"},
            {"label": "optional step", "command": "custom", "result": "not_run", "evidence": "not executed"},
        ],
        "used_memory": True,
        "token_usage": {"total_tokens": 200},
    }
    result = build_eval(payload)
    classes = {item["failure_class"] for item in result["findings"]}
    assert "test_regression" in classes
    assert "architectural_drift" in classes
    assert "tool_flaky" in classes
    assert "verification_gap" in classes
    assert result["token_usage"]["total_tokens"] == 200
    print("eval-writer: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build an eval result from verification checks.")
    parser.add_argument("--payload-json", help="Inline JSON payload with trace_id/eval_id/verification")
    parser.add_argument("--payload-file", help="Path to JSON payload file")
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
    result = write_eval(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
