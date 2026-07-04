#!/usr/bin/env python3
"""Goal parser runtime — canonical loop engine for goal ingestion."""
from __future__ import annotations
from typing import Any

def parse_goals(text: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    caps, constraints = [], []
    for ln in lines:
        low = ln.lower()
        if low.startswith("- constraint:") or low.startswith("constraint:"):
            constraints.append(ln.split(":", 1)[-1].strip())
        elif ln.startswith("-") or ln.startswith("*"):
            caps.append(ln.lstrip("-* ").strip())
        elif ln and not ln.startswith("#"):
            caps.append(ln)
    return {"capabilities": caps[:30], "constraints": constraints[:20], "raw_line_count": len(lines)}

def self_check() -> None:
    out = parse_goals("- build loop\n- verify cycles\nConstraint: stdlib only")
    assert out["capabilities"]
    print("goal-parser-runtime: PASS")

if __name__ == "__main__":
    self_check()
