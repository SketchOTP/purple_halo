#!/usr/bin/env python3
"""Plan generator runtime — canonical loop engine for plan generation."""
from __future__ import annotations
from typing import Any

def generate_plan_brief(*, research: dict[str, Any], backlog_item: dict[str, Any] | None = None, goal_model: dict[str, Any] | None = None) -> dict[str, Any]:
    item = backlog_item or {}
    return {
        "focus": item.get("title") or item.get("work_id") or "plan",
        "objective": item.get("objective") or str(research.get("summary") or "")[:200],
        "research_summary": str(research.get("summary") or "")[:400],
        "goal_capabilities": list((goal_model or {}).get("capabilities") or [])[:10],
        "runtime_source": "plan_generator_runtime",
    }

def self_check() -> None:
    brief = generate_plan_brief(research={"summary": "r"}, backlog_item={"title": "t"}, goal_model={"capabilities": ["x"]})
    assert brief["focus"]
    print("plan-generator-runtime: PASS")

if __name__ == "__main__":
    self_check()
