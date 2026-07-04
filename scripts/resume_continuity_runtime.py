#!/usr/bin/env python3
"""Resume continuity runtime — canonical loop engine for persistence/resume."""
from __future__ import annotations
from typing import Any

def build_resume_context(
    *,
    cycle_id: int,
    state: dict[str, Any] | None = None,
    last_worker: dict[str, Any] | None = None,
    goal_model: dict[str, Any] | None = None,
    verification_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    st = state or {}
    gm = goal_model or st.get("goal_model") or {}
    vb = verification_brief or st.get("verification_brief") or {}
    return {
        "cycle_id": cycle_id,
        "last_worker": last_worker or st.get("last_worker") or {},
        "open_gaps": list(st.get("open_gaps") or [])[:10],
        "next_focus": st.get("next_recommended_focus") or st.get("next_focus") or "",
        "goal_model": gm,
        "goal_model_source": "goal_model.json" if gm else "",
        "goal_maturity": str(gm.get("maturity") or ""),
        "verification_brief": vb,
        "verification_brief_source": "verification_brief.json" if vb else "",
        "verification_brief_hash": str(vb.get("source_hash") or ""),
        "resume_reason": "continue product capability work",
        "runtime_source": "resume_continuity_runtime",
    }

def self_check() -> None:
    ctx = build_resume_context(cycle_id=1, state={"next_focus": "x"}, goal_model={"mission": "m", "maturity": "L0"}, verification_brief={"work_id": "w", "source_hash": "abc"})
    assert ctx["cycle_id"] == 1
    assert ctx["goal_model"]["mission"] == "m"
    assert ctx["verification_brief"]["work_id"] == "w"
    print("resume-continuity-runtime: PASS")

if __name__ == "__main__":
    self_check()
