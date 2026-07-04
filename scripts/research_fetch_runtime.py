#!/usr/bin/env python3
"""Research fetch runtime — canonical loop engine for research synthesis."""
from __future__ import annotations
from typing import Any

def fetch_research_context(*, goal_excerpt: str, status_excerpt: str, goal_model: dict[str, Any] | None = None) -> dict[str, Any]:
    caps = list((goal_model or {}).get("capabilities") or [])
    return {
        "summary": goal_excerpt[:400] or status_excerpt[:400],
        "status_hint": status_excerpt[:400],
        "capability_area": "research_synthesis",
        "parsed_capabilities": caps[:10],
        "runtime_source": "research_fetch_runtime",
    }

def self_check() -> None:
    ctx = fetch_research_context(goal_excerpt="goal", status_excerpt="status", goal_model={"capabilities": ["a"]})
    assert ctx["summary"]
    print("research-fetch-runtime: PASS")

if __name__ == "__main__":
    self_check()
