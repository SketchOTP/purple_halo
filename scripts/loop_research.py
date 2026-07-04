#!/usr/bin/env python3
"""Targeted online research step for purple_halo loop cycles. Stdlib only."""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _fetch_ddg(query: str) -> dict[str, Any]:
    url = (
        "https://api.duckduckgo.com/?"
        + urllib.parse.urlencode({"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
    )
    req = urllib.request.Request(url, headers={"User-Agent": "purple_halo-loop/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _first_sentences(text: str, limit: int = 3) -> str:
    chunks = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(chunks[:limit]).strip()


def _top_open_gap(state: dict[str, Any], goal_text: str, status_text: str) -> dict[str, str]:
    from loop_continuity_state import load_continuity_state
    from loop_open_gaps_state import load_open_gaps_state

    cont = load_continuity_state()
    active = cont.get("active_gap_focus") or {}
    if cont.get("resumed_prior_intent") and active.get("id"):
        return {"id": str(active["id"]), "description": str(active.get("description") or active["id"])}
    ogs = load_open_gaps_state()
    top = ogs.get("top_gap") or {}
    if top.get("id"):
        return {"id": str(top["id"]), "description": str(top.get("reason") or top["id"])}
    for gap in ogs.get("open_gaps") or []:
        if isinstance(gap, dict) and gap.get("id"):
            return {"id": str(gap["id"]), "description": str(gap.get("description") or gap["id"])}
    for gap in state.get("open_gaps") or []:
        if isinstance(gap, dict) and gap.get("id"):
            return {"id": str(gap["id"]), "description": str(gap.get("description") or gap["id"])}
    focus = str(state.get("next_recommended_focus") or state.get("next_focus") or "autonomous product loop")
    if "schedule" in focus.lower():
        return {"id": "gap_scheduler_status", "description": focus}
    if "verif" in focus.lower():
        return {"id": "gap_verification_evidence", "description": focus}
    return {"id": "gap_product_realization", "description": _first_sentences(goal_text.replace("\n", " "), limit=1) or focus}


CAPABILITY_AREAS = {
    "gap_scaffold_planner": "plan_generation",
    "gap_verification_evidence": "verification_dispatch",
    "gap_executor_actions": "implementation_dispatch",
    "gap_research_goal_binding": "research_synthesis",
    "gap_research_artifact_binding": "research_synthesis",
    "gap_continuity_open_gaps": "persistence_resume",
    "gap_verify_schedule": "schedule_control",
    "gap_scheduler_status": "schedule_control",
    "gap_status_open_gaps": "repo_status_analysis",
    "gap_product_realization": "plan_generation",
    "gap_scheduled_execution": "schedule_control",
}


def _capability_area_for_gap(gap_id: str) -> str:
    return CAPABILITY_AREAS.get(gap_id, "repo_status_analysis")


def build_query(*, goal_gap: dict[str, str], next_focus: str) -> str:
    return f"autonomous software agent loop {goal_gap['id']} {goal_gap['description'][:100]} {next_focus[:80]}"


def _record_to_findings(record: dict[str, Any], gap_id: str) -> dict[str, Any]:
    return {
        "query": record.get("query") or record.get("research_summary") or gap_id,
        "goal_gap_addressed": gap_id,
        "goal_gap_fact": record.get("research_summary") or record.get("goal_gap_fact") or gap_id,
        "capability_area": record.get("capability_area") or _capability_area_for_gap(gap_id),
        "sources": list(record.get("sources") or []),
        "notes": list(record.get("notes") or ["reused cached research"]),
        "summary": record.get("research_summary") or record.get("summary") or f"[{gap_id}] cached research",
        "research_source": "cached",
        "cached_from": record.get("work_id") or record.get("synthesized_at") or "research_log",
    }


def fresh_research_for_gap(gap_id: str) -> dict[str, Any] | None:
    from loop_artifact_inputs import STALE_SECONDS, _age_seconds, load_research_log

    log = load_research_log()
    records = log.get("records") or []
    matches = [
        r
        for r in records
        if str(r.get("goal_gap_addressed") or r.get("goal_gap") or "") == gap_id
    ]
    if not matches and records:
        matches = records[-1:]
    for record in reversed(matches):
        synthesized = str(record.get("synthesized_at") or log.get("updated_at") or "")
        age = _age_seconds(synthesized)
        if age is not None and age <= STALE_SECONDS:
            return _record_to_findings(record, gap_id)
    return None


def should_fetch_fresh_research(
    *,
    gap_id: str,
    work_item: dict[str, Any] | None = None,
    failure_reason: str = "",
) -> tuple[bool, str]:
    item = work_item or {}
    blocked = str(item.get("blocked_by") or "")
    failure = str(failure_reason or item.get("failure_reason") or "")
    if failure == "missing_research_basis" or blocked == "missing_research_basis":
        return True, "missing_research_basis"
    if item.get("requires_external_evidence") or item.get("evidence_required"):
        return True, "requires_external_evidence"
    if fresh_research_for_gap(gap_id):
        return False, "cached_gap_research_fresh"
    from loop_artifact_inputs import artifact_freshness

    freshness = artifact_freshness()
    if freshness.get("research_synthesis_log", {}).get("fresh"):
        return False, "synthesis_log_fresh"
    return True, "no_fresh_research_artifact"


def resolve_research(
    *,
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
    state: dict[str, Any],
    work_item: dict[str, Any] | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    goal_gap = _top_open_gap(state, goal_text, status_text)
    gap_id = goal_gap["id"]
    failure_reason = str((work_item or {}).get("failure_reason") or (work_item or {}).get("blocked_by") or "")
    should_fetch, reason = should_fetch_fresh_research(
        gap_id=gap_id,
        work_item=work_item,
        failure_reason=failure_reason,
    )
    if not should_fetch and not force:
        cached = fresh_research_for_gap(gap_id)
        if cached is None:
            from loop_artifact_inputs import load_research_log

            log = load_research_log()
            records = log.get("records") or []
            if records:
                cached = _record_to_findings(records[-1], gap_id)
        if cached is not None:
            meta = {"cached": True, "reason": reason, "research_call_made": False, "budget_decision_reason": reason}
            return cached, meta
    from loop_cost_policy import allow_research_call, get_run_profile

    if get_run_profile() == "cheap_default_shadow" and not force:
        goal_gap = _top_open_gap(state, goal_text, status_text)
        findings = {
            "query": build_query(goal_gap=goal_gap, next_focus=str(state.get("next_focus") or "")),
            "goal_gap_addressed": goal_gap["id"],
            "goal_gap_fact": goal_gap["description"],
            "capability_area": _capability_area_for_gap(goal_gap["id"]),
            "sources": [],
            "notes": ["research skipped: cheap_default_shadow"],
            "summary": f"[{goal_gap['id']}] research skipped (cheap_default_shadow)",
            "research_source": "cheap_shadow_stub",
        }
        meta = {
            "cached": False,
            "reason": "cheap_default_shadow",
            "research_call_made": False,
            "budget_decision_reason": "cheap_default_shadow",
        }
        return findings, meta

    allowed, gate_reason = allow_research_call()
    if not allowed and not force:
        cached = fresh_research_for_gap(gap_id)
        if cached is not None:
            meta = {
                "cached": True,
                "reason": gate_reason,
                "research_call_made": False,
                "budget_decision_reason": f"research_cap_reuse:{gate_reason}",
            }
            return cached, meta
        goal_gap = _top_open_gap(state, goal_text, status_text)
        findings = {
            "query": build_query(goal_gap=goal_gap, next_focus=str(state.get("next_focus") or "")),
            "goal_gap_addressed": goal_gap["id"],
            "goal_gap_fact": goal_gap["description"],
            "capability_area": _capability_area_for_gap(goal_gap["id"]),
            "sources": [],
            "notes": [f"research skipped: {gate_reason}"],
            "summary": f"[{goal_gap['id']}] research skipped ({gate_reason})",
            "research_source": "budget_blocked_stub",
        }
        meta = {
            "cached": False,
            "reason": gate_reason,
            "research_call_made": False,
            "budget_decision_reason": gate_reason,
        }
        return findings, meta
    findings = run_research(
        goal_text=goal_text,
        status_text=status_text,
        repo_snapshot=repo_snapshot,
        state=state,
    )
    findings["research_source"] = "fresh"
    meta = {
        "cached": False,
        "reason": reason,
        "research_call_made": True,
        "budget_decision_reason": reason,
    }
    return findings, meta


def run_research(
    *,
    goal_text: str,
    status_text: str,
    repo_snapshot: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    goal_summary = _first_sentences(goal_text.replace("\n", " "), limit=2)
    status_summary = _first_sentences(status_text.replace("\n", " "), limit=2)
    next_focus = str(state.get("next_recommended_focus") or state.get("next_focus") or "next implementation step")
    goal_gap = _top_open_gap(state, goal_text, status_text)
    query = build_query(goal_gap=goal_gap, next_focus=next_focus)

    findings: dict[str, Any] = {
        "query": query,
        "goal_gap_addressed": goal_gap["id"],
        "goal_gap_fact": goal_gap["description"],
        "capability_area": _capability_area_for_gap(goal_gap["id"]),
        "sources": [],
        "notes": [],
    }
    try:
        payload = _fetch_ddg(query)
        abstract = (payload.get("AbstractText") or "").strip()
        if abstract:
            findings["sources"].append({"kind": "abstract", "text": abstract, "goal_gap": goal_gap["id"]})
        for topic in payload.get("RelatedTopics") or []:
            if isinstance(topic, dict) and topic.get("Text"):
                findings["sources"].append({"kind": "related", "text": topic["Text"], "goal_gap": goal_gap["id"]})
            if len(findings["sources"]) >= 5:
                break
    except Exception as exc:  # ponytail: single provider; upgrade path = pluggable research backends
        findings["notes"].append(f"online lookup failed: {exc}")

    findings["notes"].append(f"goal_gap_addressed: {goal_gap['id']}")
    findings["notes"].append(f"repo files tracked: {len(repo_snapshot.get('tracked_files', []))}")
    findings["notes"].append(f"completed milestones: {state.get('completed_milestones', [])}")
    findings["goal_gap_fact"] = (
        findings["sources"][0]["text"]
        if findings["sources"]
        else f"Repo-truth focus for {goal_gap['id']}: {goal_gap['description']}"
    )
    findings["summary"] = (
        f"[{goal_gap['id']}] {findings['goal_gap_fact'][:240]}"
    )
    return findings


def self_check() -> None:
    state = {
        "next_focus": "schedule contract",
        "completed_milestones": [],
        "open_gaps": [{"id": "gap_product_realization", "description": "product loop"}],
    }
    sample, meta = resolve_research(
        goal_text="Build minimal autonomous loop.",
        status_text="Loop scaffold installed.",
        repo_snapshot={"tracked_files": ["scripts/purple_halo_loop.py"]},
        state=state,
        force=True,
    )
    assert sample["query"] or sample.get("summary")
    assert sample.get("goal_gap_addressed")
    assert sample.get("capability_area")
    assert "summary" in sample
    assert meta.get("research_call_made") is True
    should_fetch, _ = should_fetch_fresh_research(gap_id="gap_product_realization")
    assert isinstance(should_fetch, bool)
    print("loop-research: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo loop research step")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    parser.error("use purple_halo_loop.py run or --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())