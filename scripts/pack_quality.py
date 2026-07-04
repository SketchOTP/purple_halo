#!/usr/bin/env python3
"""Retrieval-pack quality model — presence is phase 1; quality is phase 2."""

from __future__ import annotations

from typing import Any

PACK_ABSENT = "absent"
PACK_PRESENT = "present"
PACK_RELEVANT = "relevant"
PACK_STALE = "stale"
PACK_WASTEFUL = "wasteful"
PACK_MISLEADING = "misleading"

PACK_QUALITY_STATES = (
    PACK_ABSENT,
    PACK_PRESENT,
    PACK_RELEVANT,
    PACK_STALE,
    PACK_WASTEFUL,
    PACK_MISLEADING,
)


def _avg_top_score(top_results: list[dict[str, Any]]) -> float:
    scores = [float(item.get("score") or 0) for item in top_results if item.get("score") is not None]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def assess_pack_quality(
    *,
    task: str,
    nav_payload: dict[str, Any] | None,
    verification_passed: bool | None = None,
    wrong_tool: bool = False,
    repair_loops: int = 0,
    failure_classes: list[str] | None = None,
) -> dict[str, Any]:
    """Classify pack usefulness vs token cost; correlate with downstream signals when available."""
    nav = dict(nav_payload or {})
    pack_ids = list(nav.get("pack_ids") or [])
    top_results = list(nav.get("top_results") or [])
    suggested_files = list(nav.get("suggested_files") or [])
    avg_score = _avg_top_score(top_results)
    file_count = len(suggested_files)
    # ponytail: coarse token proxy — ~400 tokens per suggested file until trace token attribution exists
    token_estimate = file_count * 400

    if not pack_ids:
        state = PACK_ABSENT
    elif avg_score >= 0.55 and file_count >= 1:
        state = PACK_RELEVANT
    elif file_count > 14:
        state = PACK_WASTEFUL
    elif top_results and avg_score < 0.25:
        state = PACK_MISLEADING
    elif avg_score < 0.4:
        state = PACK_STALE
    else:
        state = PACK_PRESENT

    failures = list(failure_classes or [])
    if wrong_tool or "wrong_tool_selected" in failures:
        state = PACK_MISLEADING
    if verification_passed is False and state in {PACK_RELEVANT, PACK_PRESENT}:
        state = PACK_MISLEADING
    if repair_loops >= 2 and state == PACK_RELEVANT:
        state = PACK_WASTEFUL

    justified = state == PACK_RELEVANT and (verification_passed is not False)
    return {
        "pack_ids": pack_ids,
        "pack_quality_state": state,
        "pack_relevance_score": round(avg_score, 4),
        "pack_file_count": file_count,
        "pack_token_estimate": token_estimate,
        "pack_justified_token_cost": justified,
        "pack_query": task,
        "pack_top_hit_count": len(top_results),
    }


def merge_pack_quality_into_context(context: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    merged = dict(context)
    merged["pack_quality"] = quality
    merged["pack_quality_state"] = quality.get("pack_quality_state")
    merged["pack_relevance_score"] = quality.get("pack_relevance_score")
    merged["pack_token_estimate"] = quality.get("pack_token_estimate")
    return merged


def _self_check() -> int:
    relevant = assess_pack_quality(
        task="session orchestrator trace guard",
        nav_payload={
            "pack_ids": ["cnp_x"],
            "top_results": [{"score": 0.82, "path": "scripts/session_orchestrator.py"}],
            "suggested_files": ["scripts/session_orchestrator.py"],
        },
        verification_passed=True,
    )
    assert relevant["pack_quality_state"] == PACK_RELEVANT

    misleading = assess_pack_quality(
        task="unrelated task",
        nav_payload={
            "pack_ids": ["cnp_y"],
            "top_results": [{"score": 0.1, "path": "README.md"}],
            "suggested_files": ["README.md"],
        },
        verification_passed=False,
    )
    assert misleading["pack_quality_state"] == PACK_MISLEADING
    print("pack-quality: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
