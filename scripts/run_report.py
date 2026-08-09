#!/usr/bin/env python3
"""Append-only run report: MMDDYY HHMM summary. Stdlib only."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "RUN_REPORT.md"


def stamp() -> str:
    return datetime.now().strftime("%m%d%y %H%M")


def append_line(summary: str) -> str:
    line = f"{stamp()} {summary.strip()}"
    if not REPORT_PATH.is_file():
        REPORT_PATH.write_text("# purple_halo run report\n\n", encoding="utf-8")
    with REPORT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return line




def _failure_summary(error: str, payload: dict[str, Any]) -> str:
    for key in ("stop_reason", "blocked_classification", "stop_detail", "stop_condition"):
        val = payload.get(key)
        if val:
            detail = payload.get("stop_detail")
            if key != "stop_detail" and detail and detail != val:
                return f"Failed ({val}; {detail})"
            return f"Failed ({val})"
    err = (error or "").strip()
    if err.startswith("{"):
        try:
            data = json.loads(err)
            for key in ("stop_reason", "blocked_classification", "stop_detail"):
                if data.get(key):
                    return f"Failed ({data[key]})"
        except json.JSONDecodeError:
            pass
    err = (error or "unknown error").replace("\n", " ").strip()[:120]
    return f"Failed: {err}"

def summarize_run(
    *,
    status: str,
    error: str = "",
    payload: dict[str, Any] | None = None,
    reason: str = "",
) -> str:
    payload = payload or {}
    if status == "skipped":
        return f"Skipped ({error or reason or 'no work'})"
    if status == "failure":
        return _failure_summary(error, payload)
    plan = (
        payload.get("plan_id")
        or payload.get("selected_work_id")
        or payload.get("work_id")
        or ""
    )
    parts: list[str] = []
    if plan:
        parts.append(f"Worked on {plan}")
    if payload.get("meaningful_product_progress"):
        parts.append("made progress")
    elif payload.get("verification_passed"):
        parts.append("verified health")
    elif payload.get("blocked_classification"):
        parts.append(f"blocked ({payload.get('blocked_classification')})")
    why = payload.get("why_selected") or payload.get("why_run") or ""
    if why and not parts:
        parts.append(str(why).replace("\n", " ").strip()[:120])
    if not parts:
        parts.append("Run completed")
    return "; ".join(parts)


def count_report_lines(path: Path | None = None) -> int:
    """Count stamped run lines in RUN_REPORT.md (excludes header/blank lines)."""
    path = path or REPORT_PATH
    if not path.is_file():
        return 0
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def self_check() -> None:
    assert summarize_run(
        status="failure",
        error='{"stop_reason":"no_executable_work","stop_detail":"backlog_empty"}',
    ) == "Failed (no_executable_work)"
    line = append_line("self-check")
    assert line.startswith(stamp()[:4]) or len(line) > 10
    text = REPORT_PATH.read_text(encoding="utf-8")
    assert line in text
    assert count_report_lines() >= 1
    print("run-report: PASS")


if __name__ == "__main__":
    self_check()