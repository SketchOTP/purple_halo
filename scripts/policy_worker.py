#!/usr/bin/env python3
"""Runtime policy adapter driven by measured governance failures."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "project_memory" / "runtime"
EVAL_LOG = RUNTIME_DIR / "eval_results.jsonl"


def _post_json(url: str, payload: dict, api_key: str | None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _latest_local_eval(trace_id: str | None) -> dict | None:
    if not EVAL_LOG.is_file():
        return None
    matches: list[dict] = []
    for line in EVAL_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if trace_id and payload.get("trace_id") != trace_id:
            continue
        matches.append(payload)
    return matches[-1] if matches else None


def _local_adjustment(eval_payload: dict) -> dict:
    classes = set(eval_payload.get("failure_classes") or [])
    pack_ids = list(eval_payload.get("code_navigation_pack_ids") or [])
    navigation_required = bool(eval_payload.get("navigation_required"))
    pack_quality = dict(eval_payload.get("pack_quality") or {})
    pack_state = str(pack_quality.get("pack_quality_state") or "")
    route = "direct"
    verification_mode = "targeted"
    repair_strategy = "continue"
    parallelism = 2
    if navigation_required and not pack_ids:
        verification_mode = "full"
        repair_strategy = "collect_navigation_before_work"
        parallelism = 1
    if pack_state == "misleading":
        repair_strategy = "refresh_navigation_before_retry"
        verification_mode = "full"
        parallelism = 1
    elif pack_state == "wasteful":
        repair_strategy = "narrow_retrieval_scope"
        parallelism = 1
    if "wrong_tool_selected" in classes:
        route = "approval_gated"
        verification_mode = "full"
        repair_strategy = "use_structured_adapter"
        parallelism = 1
    elif "architectural_drift" in classes:
        route = "approval_gated"
        verification_mode = "full"
        repair_strategy = "governance_review_before_retry"
        parallelism = 1
    elif "tool_flaky" in classes:
        verification_mode = "retry_with_backoff"
        repair_strategy = "retry_same_path"
    elif "test_regression" in classes:
        verification_mode = "targeted"
        repair_strategy = "fix_then_reverify"
    elif "verification_gap" in classes:
        verification_mode = "full"
        repair_strategy = "add_missing_proof"
    elif pack_ids and pack_state == "relevant":
        repair_strategy = "retune_retrieval_before_retry"
        parallelism = 1
    return {
        "ok": True,
        "route_preference": route,
        "verification_mode": verification_mode,
        "repair_strategy": repair_strategy,
        "parallelism": parallelism,
        "failure_classes": sorted(classes),
        "code_navigation_pack_ids": pack_ids,
        "navigation_required": navigation_required,
        "pack_quality_state": pack_state or None,
        "source": "local_fallback",
    }


def resolve_policy(*, trace_id: str | None = None, eval_payload: dict | None = None) -> dict:
    payload = dict(eval_payload or {})
    if not payload and trace_id:
        payload = _latest_local_eval(trace_id) or {}
    endpoint = os.environ.get("MIMIR_ENDPOINT", "").rstrip("/")
    if endpoint:
        try:
            result = _post_json(
                f"{endpoint}/api/governance/policy/runtime-adjustment",
                {
                    "trace_id": trace_id or payload.get("trace_id"),
                    "eval_id": payload.get("eval_id"),
                    "project": payload.get("project"),
                    "failure_classes": payload.get("failure_classes") or [],
                    "outcome": payload.get("outcome"),
                    "code_navigation_pack_ids": payload.get("code_navigation_pack_ids") or [],
                    "navigation_required": bool(payload.get("navigation_required")),
                },
                os.environ.get("MIMIR_API_KEY"),
            )
            return {"source": "mimir", **result}
        except Exception:
            pass
    return _local_adjustment(payload)


def _self_check() -> int:
    old_endpoint = os.environ.pop("MIMIR_ENDPOINT", None)
    try:
        result = resolve_policy(
            eval_payload={
                "trace_id": "trace-self",
                "failure_classes": ["wrong_tool_selected", "test_regression"],
                "outcome": "fail",
                "code_navigation_pack_ids": ["cnp-self"],
                "navigation_required": True,
            }
        )
        assert result["route_preference"] == "approval_gated"
        assert result["parallelism"] == 1
    finally:
        if old_endpoint is not None:
            os.environ["MIMIR_ENDPOINT"] = old_endpoint
    print("policy-worker: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Resolve runtime policy from measured failures.")
    parser.add_argument("--trace-id")
    parser.add_argument("--eval-file")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()

    eval_payload = None
    if args.eval_file:
        eval_payload = json.loads(Path(args.eval_file).read_text(encoding="utf-8"))
    result = resolve_policy(trace_id=args.trace_id, eval_payload=eval_payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
