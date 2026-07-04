#!/usr/bin/env python3
"""Read resumable session checkpoints from Mimir with local fallback."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "project_memory" / "runtime"
RESUME_STATE_DIR = RUNTIME_DIR / "resumes"


def _safe_resume_filename(resume_id: str) -> str:
    return resume_id.replace(":", "__")


def _headers(api_key: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    return headers


def _get_json(url: str, api_key: str | None) -> dict | list:
    req = urllib.request.Request(url, headers=_headers(api_key), method="GET")
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _local_by_resume_id(resume_id: str) -> dict | None:
    path = RESUME_STATE_DIR / f"{_safe_resume_filename(resume_id)}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _local_by_trace_id(trace_id: str) -> dict | None:
    if not RESUME_STATE_DIR.is_dir():
        return None
    newest: tuple[float, dict] | None = None
    for path in RESUME_STATE_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("trace_id") != trace_id:
            continue
        mtime = path.stat().st_mtime
        if newest is None or mtime > newest[0]:
            newest = (mtime, payload)
    return newest[1] if newest else None


def fetch_resume(*, resume_id: str | None = None, trace_id: str | None = None) -> dict:
    if not resume_id and not trace_id:
        raise ValueError("resume_id or trace_id is required")

    endpoint = os.environ.get("MIMIR_ENDPOINT", "").rstrip("/")
    api_key = os.environ.get("MIMIR_API_KEY")

    if endpoint and resume_id:
        try:
            result = _get_json(f"{endpoint}/api/governance/resumes/{urllib.parse.quote(resume_id, safe='')}", api_key)
            if isinstance(result, dict) and result.get("resume"):
                return {"source": "mimir", **result["resume"]}
        except Exception:
            pass

    if endpoint and trace_id:
        try:
            query = urllib.parse.urlencode({"trace_id": trace_id})
            result = _get_json(f"{endpoint}/api/governance/resumes/latest?{query}", api_key)
            if isinstance(result, dict) and result.get("resume"):
                return {"source": "mimir", **result["resume"]}
        except Exception:
            pass

    if resume_id:
        local = _local_by_resume_id(resume_id)
        if local:
            return {"source": "local", **local}
    if trace_id:
        local = _local_by_trace_id(trace_id)
        if local:
            return {"source": "local", **local}

    raise FileNotFoundError("resume checkpoint not found")


def _self_check() -> int:
    RESUME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    sample = {
        "resume_id": "resume:self-check",
        "trace_id": "trace:self-check",
        "task": "self-check task",
        "status": "ready",
        "next_step": "continue",
        "tool_loadout": ["shell"],
        "parallel_tracks": [],
    }
    path = RESUME_STATE_DIR / f"{_safe_resume_filename(sample['resume_id'])}.json"
    path.write_text(json.dumps(sample, indent=2) + "\n", encoding="utf-8")
    assert fetch_resume(resume_id="resume:self-check")["resume_id"] == "resume:self-check"
    assert fetch_resume(trace_id="trace:self-check")["trace_id"] == "trace:self-check"
    print("resume-reader: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Read a resumable session checkpoint.")
    parser.add_argument("--resume-id")
    parser.add_argument("--trace-id")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()
    result = fetch_resume(resume_id=args.resume_id, trace_id=args.trace_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
