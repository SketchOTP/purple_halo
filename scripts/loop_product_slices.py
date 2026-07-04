#!/usr/bin/env python3
"""Bounded product code slices the loop executor can apply. Stdlib only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

SCHEDULE_RUNNER = '''#!/usr/bin/env python3
"""Schedule-driven loop runner for purple_halo. Stdlib only."""

from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "project_memory" / "runtime" / "schedule.default.json"
ACTIVE = ROOT / "project_memory" / "runtime" / "schedule.json"
HISTORY_PATH = ROOT / "project_memory" / "runtime" / "schedule_run_history.json"
LOOP = ROOT / "scripts" / "purple_halo_loop.py"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_schedule() -> dict[str, Any]:
    path = ACTIVE if ACTIVE.is_file() else DEFAULT
    return json.loads(path.read_text(encoding="utf-8"))


def load_run_history() -> dict[str, Any]:
    if not HISTORY_PATH.is_file():
        return {"attempts": [], "last_failure": None, "retry_count": 0}
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))


def save_run_history(history: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\\n", encoding="utf-8")


def append_run_record(
    *,
    trigger: str,
    status: str,
    cycle_id: int | None = None,
    error: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    history = load_run_history()
    record = {
        "id": str(uuid.uuid4())[:8],
        "started_at": started_at or _now_iso(),
        "finished_at": finished_at or _now_iso(),
        "trigger": trigger,
        "status": status,
        "cycle_id": cycle_id,
        "error": error,
        "retry_count": int(history.get("retry_count") or 0),
    }
    history.setdefault("attempts", []).append(record)
    if status == "failure":
        history["retry_count"] = int(history.get("retry_count") or 0) + 1
        history["last_failure"] = record
    elif status == "success":
        history["retry_count"] = 0
        history["last_failure"] = None
    save_run_history(history)
    return record


def _current_hhmm() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def slots_due_now(schedule: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    schedule = schedule or load_schedule()
    if not schedule.get("enabled"):
        return []
    now = _current_hhmm()
    return [slot for slot in (schedule.get("runs") or []) if str(slot.get("at") or "") == now]


def run_loop(*, trigger: str) -> dict[str, Any]:
    started = _now_iso()
    proc = subprocess.run(["python3", str(LOOP), "run"], cwd=ROOT, capture_output=True, text=True)
    cycle_id = None
    status = "failure"
    error = ""
    try:
        payload = json.loads(proc.stdout)
        cycle_id = payload.get("cycle_id")
        if proc.returncode == 0 and payload.get("verification_passed"):
            status = "success"
        else:
            error = (proc.stderr or proc.stdout or "verification failed")[:500]
    except json.JSONDecodeError:
        error = (proc.stderr or proc.stdout or "invalid loop output")[:500]
    record = append_run_record(
        trigger=trigger,
        status=status,
        cycle_id=cycle_id,
        error=error,
        started_at=started,
        finished_at=_now_iso(),
    )
    return {"record": record, "exit_code": proc.returncode, "loop_stdout": proc.stdout.strip()}


def run_due() -> dict[str, Any]:
    due = slots_due_now()
    if not due:
        record = append_run_record(trigger="scheduled", status="skipped", error="no due slot")
        return {"ran": False, "reason": "no due slot", "record": record}
    return {"ran": True, **run_loop(trigger="scheduled")}


def run_now() -> dict[str, Any]:
    return run_loop(trigger="manual")


def self_check() -> None:
    schedule = load_schedule()
    assert "runs" in schedule
    hist = load_run_history()
    assert isinstance(hist.get("attempts"), list)
    print("loop-schedule: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo schedule runner")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--run-due", action="store_true")
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.show:
        print(json.dumps(load_schedule(), indent=2))
        return 0
    if args.history:
        print(json.dumps(load_run_history(), indent=2))
        return 0
    if args.run_due:
        print(json.dumps(run_due(), indent=2))
        return 0 if True else 1
    if args.run_now:
        result = run_now()
        print(json.dumps(result, indent=2))
        return 0 if result["record"]["status"] == "success" else 1
    parser.error("specify --self-check, --show, --run-due, --run-now, or --history")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''

LOOP_RUNNER = '''#!/usr/bin/env python3
"""Launcher for purple_halo scheduled autonomous execution. Stdlib only."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEDULE = ROOT / "scripts" / "loop_schedule.py"


def _call_schedule(flag: str) -> int:
    proc = subprocess.run(["python3", str(SCHEDULE), flag], cwd=ROOT, capture_output=True, text=True)
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo loop runner launcher")
    parser.add_argument("command", nargs="?", choices=["run-due", "run-now", "status", "history"])
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        proc = subprocess.run(["python3", str(SCHEDULE), "--self-check"], cwd=ROOT)
        if proc.returncode != 0:
            return proc.returncode
        print("loop-runner: PASS")
        return 0
    if args.command == "run-due":
        return _call_schedule("--run-due")
    if args.command == "run-now":
        return _call_schedule("--run-now")
    if args.command == "history":
        return _call_schedule("--history")
    if args.command == "status":
        proc = subprocess.run(["python3", str(SCHEDULE), "--show"], cwd=ROOT, capture_output=True, text=True)
        schedule = json.loads(proc.stdout) if proc.returncode == 0 else {}
        hist_proc = subprocess.run(["python3", str(SCHEDULE), "--history"], cwd=ROOT, capture_output=True, text=True)
        history = json.loads(hist_proc.stdout) if hist_proc.returncode == 0 else {}
        print(json.dumps({"schedule": schedule, "history": history}, indent=2))
        return 0
    parser.error("specify command or --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''

SLICES: dict[str, dict[str, Any]] = {
    "product_open_gaps_state_hydrate": {
        "files": {},
        "note": "Canonical hydrator: scripts/loop_open_gaps_state.py",
    },
    "product_continuity_state_resume": {
        "files": {},
        "note": "Canonical continuity resume: scripts/loop_continuity_state.py",
    },
    "scheduled_runner": {
        "files": {
            "scripts/loop_schedule.py": SCHEDULE_RUNNER,
            "scripts/loop_runner.py": LOOP_RUNNER,
        },
        "expected_symbols": {
            "scripts/loop_schedule.py": ["run_now", "run_due", "append_run_record", "load_run_history"],
            "scripts/loop_runner.py": ["main"],
        },
        "runtime_artifacts": ["project_memory/runtime/schedule_run_history.json"],
    }
}


def apply_slice(slice_id: str) -> list[str]:
    spec = SLICES.get(slice_id)
    if not spec:
        raise ValueError(f"unknown code slice: {slice_id}")
    changed: list[str] = []
    for rel, content in spec["files"].items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = content if content.endswith("\n") else content + "\n"
        path.write_text(text, encoding="utf-8")
        changed.append(rel)
    return changed


def slice_spec(slice_id: str) -> dict[str, Any]:
    spec = SLICES.get(slice_id)
    if not spec:
        raise ValueError(f"unknown code slice: {slice_id}")
    return spec


def self_check() -> None:
    assert "product_open_gaps_state_hydrate" in SLICES
    assert "product_continuity_state_resume" in SLICES
    assert "scheduled_runner" in SLICES
    assert "run_now" in SLICES["scheduled_runner"]["files"]["scripts/loop_schedule.py"]
    print("loop-product-slices: PASS")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="purple_halo product code slices")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--apply", metavar="SLICE")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.apply:
        changed = apply_slice(args.apply)
        print("applied:", ",".join(changed))
        return 0
    parser.error("specify --self-check or --apply SLICE")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())