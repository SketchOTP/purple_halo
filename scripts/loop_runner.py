#!/usr/bin/env python3
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
