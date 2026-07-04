#!/usr/bin/env python3
"""Build and upsert resumable session checkpoints from trace state."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "project_memory" / "runtime"
RESUME_LOG = RUNTIME_DIR / "resume_records.jsonl"
RESUME_STATE_DIR = RUNTIME_DIR / "resumes"


def _post_json(url: str, payload: dict, api_key: str | None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _safe_resume_filename(resume_id: str) -> str:
    return resume_id.replace(":", "__")


def _resume_path(resume_id: str) -> Path:
    return RESUME_STATE_DIR / f"{_safe_resume_filename(resume_id)}.json"


def derive_resume_id(trace: dict, payload: dict) -> str:
    details = payload.get("details") or {}
    return str(
        details.get("resume_id")
        or trace.get("resume_id")
        or f"resume:{trace['trace_id']}"
    )


def _status_for_resume(trace: dict, payload: dict) -> str:
    hook = payload.get("hook")
    trace_status = str(trace.get("status") or "partial")
    if hook == "verification_complete":
        return "waiting_verification" if trace_status != "complete" else "ready"
    if trace_status in {"blocked", "failed"}:
        return "blocked"
    return "ready"


def _next_step_for_resume(trace: dict, payload: dict) -> str:
    details = payload.get("details") or {}
    hook = payload.get("hook")
    if details.get("next_step"):
        return str(details["next_step"])
    if hook == "session_start":
        return "begin tool routing"
    if hook == "pre_tool_use":
        return "run tool and record outcome"
    if hook == "post_tool_use":
        return "run verification or continue execution"
    if hook == "verification_complete":
        return "review verification and either repair or close session"
    if hook == "session_end":
        return "resume only if reopened by operator"
    return "continue from latest trace state"


def build_resume(trace: dict, payload: dict) -> dict:
    resume_id = derive_resume_id(trace, payload)
    details = payload.get("details") or {}
    checkpoint = {
        "resume_id": resume_id,
        "trace_id": trace["trace_id"],
        "task": trace.get("task") or "unknown task",
        "status": _status_for_resume(trace, payload),
        "next_step": _next_step_for_resume(trace, payload),
        "tool_loadout": list(trace.get("tool_loadout") or []),
        "parallel_tracks": list(details.get("parallel_tracks") or []),
        "lineage": dict(trace.get("lineage") or {}),
    }
    return checkpoint


def write_resume(trace: dict, payload: dict) -> dict:
    checkpoint = build_resume(trace, payload)
    RESUME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    state_path = _resume_path(checkpoint["resume_id"])
    state_path.write_text(json.dumps(checkpoint, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with RESUME_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(checkpoint, sort_keys=True) + "\n")
    endpoint = os.environ.get("MIMIR_ENDPOINT", "").rstrip("/")
    if endpoint:
        api_result = _post_json(
            f"{endpoint}/api/governance/resumes",
            {
                **checkpoint,
                "project": trace.get("project"),
                "session_id": trace.get("session_id"),
            },
            os.environ.get("MIMIR_API_KEY"),
        )
        checkpoint["mimir"] = api_result
    return checkpoint


def _self_check() -> int:
    trace = {
        "trace_id": "trace-self",
        "task": "run tests",
        "status": "partial",
        "tool_loadout": ["shell"],
        "resume_id": "resume:trace-self",
    }
    start = write_resume(trace, {"hook": "session_start", "details": {}})
    assert start["status"] == "ready"
    verify = write_resume(trace, {"hook": "verification_complete", "details": {}})
    assert verify["status"] == "waiting_verification"
    blocked = write_resume({**trace, "status": "blocked"}, {"hook": "post_tool_use", "details": {}})
    assert blocked["status"] == "blocked"
    print("resume-writer: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build or enrich a session resume checkpoint from trace state.")
    parser.add_argument("--trace-json")
    parser.add_argument("--payload-json")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()
    if not args.trace_json or not args.payload_json:
        raise SystemExit("provide --trace-json and --payload-json")
    trace = json.loads(args.trace_json)
    payload = json.loads(args.payload_json)
    checkpoint = write_resume(trace, payload)
    print(json.dumps(checkpoint, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
