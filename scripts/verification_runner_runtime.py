#!/usr/bin/env python3
"""Verification runner runtime — canonical loop engine for verification dispatch."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parent.parent

def run_verification_suite(commands: list[list[str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cmd in commands:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out.append({"command": cmd, "exit_code": proc.returncode, "passed": proc.returncode == 0, "stdout": proc.stdout.strip()[:500], "stderr": proc.stderr.strip()[:500], "runtime_source": "verification_runner_runtime"})
    return out

def self_check() -> None:
    res = run_verification_suite([["python3", "scripts/goal_parser_runtime.py"]])
    assert res[0]["passed"]
    print("verification-runner-runtime: PASS")

if __name__ == "__main__":
    self_check()
