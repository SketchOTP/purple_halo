#!/usr/bin/env python3
"""Persist and resume open-gap continuity across purple_halo cycles. Stdlib only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from loop_target_workspace import runtime_root  # noqa: E402

CONTINUITY_STATE_PATH = runtime_root() / "continuity_state.json"
OPEN_GAPS_STATE_PATH = runtime_root() / "open_gaps_state.json"
GOAL_MODEL_PATH = runtime_root() / "goal_model.json"
VERIFY_BRIEF_PATH = runtime_root() / "verification_brief.json"
STATE_PATH = runtime_root() / "loop_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _path_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _latest_verification_outcome(state: dict[str, Any] | None = None) -> dict[str, Any]:
    st = state if isinstance(state, dict) else _load_json(STATE_PATH)
    last = st.get("last_cycle") if isinstance(st.get("last_cycle"), dict) else {}
    delta = st.get("last_verified_repo_delta") if isinstance(st.get("last_verified_repo_delta"), dict) else {}
    return {
        "verification_passed": bool(last.get("verification_passed")),
        "plan_id": str(last.get("plan_id") or ""),
        "artifact_dir": str(last.get("artifact_dir") or ""),
        "repo_delta_summary": str(delta.get("summary") or ""),
        "status": str(st.get("status") or ""),
    }


def compute_source_hash(
    *,
    state: dict[str, Any] | None = None,
    open_gaps_doc: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    open_gaps_doc = open_gaps_doc if open_gaps_doc is not None else _load_json(OPEN_GAPS_STATE_PATH)
    verification_outcome = _latest_verification_outcome(state)
    source_inputs: dict[str, Any] = {
        "open_gaps_state.json": {
            "path": _rel(OPEN_GAPS_STATE_PATH),
            "hash": str(open_gaps_doc.get("source_hash") or _path_hash(OPEN_GAPS_STATE_PATH)),
            "top_gap_id": str((open_gaps_doc.get("top_gap") or {}).get("id") or ""),
        },
        "goal_model.json": {"path": _rel(GOAL_MODEL_PATH), "hash": _path_hash(GOAL_MODEL_PATH)},
        "verification_brief.json": {"path": _rel(VERIFY_BRIEF_PATH), "hash": _path_hash(VERIFY_BRIEF_PATH)},
        "verification_outcome": verification_outcome,
    }
    source_hash = hashlib.sha256(json.dumps(source_inputs, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return source_hash, source_inputs


def load_continuity_state() -> dict[str, Any]:
    if not CONTINUITY_STATE_PATH.is_file():
        return {"version": 1, "freshness": "missing", "carried_forward_open_gaps": []}
    return _load_json(CONTINUITY_STATE_PATH)


def save_continuity_state(payload: dict[str, Any]) -> Path:
    CONTINUITY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTINUITY_STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return CONTINUITY_STATE_PATH


def continuity_state_freshness(*, state: dict[str, Any] | None = None) -> dict[str, Any]:
    current_hash, _ = compute_source_hash(state=state)
    if not CONTINUITY_STATE_PATH.is_file():
        return {
            "status": "missing",
            "fresh": False,
            "stale": True,
            "present": False,
            "source_hash": None,
            "current_source_hash": current_hash,
            "hash_match": False,
            "active_gap_focus": {},
            "resumed_from": {},
        }
    payload = _load_json(CONTINUITY_STATE_PATH)
    stored = str(payload.get("source_hash") or "")
    hash_match = bool(stored and stored == current_hash)
    return {
        "status": "fresh" if hash_match else "stale",
        "fresh": hash_match,
        "stale": not hash_match,
        "present": True,
        "source_hash": stored or None,
        "current_source_hash": current_hash,
        "hash_match": hash_match,
        "last_refreshed_at": payload.get("last_refreshed_at"),
        "active_gap_focus": dict(payload.get("active_gap_focus") or {}),
        "resumed_from": dict(payload.get("resumed_from") or {}),
        "resume_eligibility": dict(payload.get("resume_eligibility") or {}),
        "prior_cycle_status": payload.get("prior_cycle_status") or "",
    }


def _gap_focus(gaps: list[dict[str, Any]], preferred_id: str = "") -> dict[str, Any]:
    if preferred_id:
        for gap in gaps:
            if isinstance(gap, dict) and str(gap.get("id") or "") == preferred_id:
                return {
                    "id": preferred_id,
                    "description": str(gap.get("description") or preferred_id),
                    "classification": str(gap.get("classification") or ""),
                    "priority": int(gap.get("priority") or 99),
                }
    for gap in gaps:
        if isinstance(gap, dict) and gap.get("id"):
            return {
                "id": str(gap["id"]),
                "description": str(gap.get("description") or gap["id"]),
                "classification": str(gap.get("classification") or ""),
                "priority": int(gap.get("priority") or 99),
            }
    return {"id": "", "description": "", "classification": "", "priority": 99}


def _merge_retry_history(
    prior: list[dict[str, Any]],
    *,
    cycle_id: int,
    work_id: str,
    reason: str,
    status: str,
) -> list[dict[str, Any]]:
    history = [dict(h) for h in prior if isinstance(h, dict)]
    if status in {"partial", "blocked"} or reason:
        history.append(
            {
                "cycle_id": cycle_id,
                "work_id": work_id,
                "reason": reason or status,
                "status": status,
                "at": _now_iso(),
            }
        )
    return history[-20:]


def write_continuity_after_cycle(
    *,
    cycle_id: int,
    state: dict[str, Any],
    plan: dict[str, Any],
    verification: dict[str, Any],
    open_gaps: list[dict[str, Any]],
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write continuity handoff for the next cycle after completion/partial/blocked."""
    prior = prior if prior is not None else load_continuity_state()
    open_gaps_doc = _load_json(OPEN_GAPS_STATE_PATH)
    if not open_gaps and open_gaps_doc.get("open_gaps"):
        open_gaps = list(open_gaps_doc.get("open_gaps") or [])
    work_id = str(plan.get("backlog_work_id") or (plan.get("work_package") or {}).get("work_id") or "")
    plan_id = str(plan.get("plan_id") or "")
    gap_id = str(plan.get("goal_gap_addressed") or "")
    passed = bool(verification.get("passed"))
    prior_status = "ready" if passed else ("blocked" if state.get("retry_blocked") or state.get("budget_blocked") else "partial")
    if work_id.startswith("operational_") or work_id.startswith("improve_") or work_id.startswith("deliver_"):
        active = {
            "id": work_id,
            "description": str(plan.get("description") or plan.get("focus") or work_id),
            "classification": str(plan.get("capability") or ""),
            "priority": 1,
        }
    else:
        active = _gap_focus(open_gaps, preferred_id=gap_id if not passed else "")
        if passed and active.get("id") == gap_id:
            remaining = [g for g in open_gaps if str(g.get("id") or "") != gap_id]
            active = _gap_focus(remaining)
    source_hash, source_inputs = compute_source_hash(state=state, open_gaps_doc=open_gaps_doc)
    reason = ""
    if not passed:
        reason = str(
            (verification.get("summary") or "")
            or (plan.get("resume_reason") or "")
            or state.get("retry_blocked_reason")
            or state.get("budget_blocked_reason")
            or "verification_failed"
        )
    eligible = bool(active.get("id")) and prior_status in {"partial", "blocked", "ready"}
    eligibility_reason = (
        f"resume_{prior_status}_focus_{active.get('id')}"
        if eligible
        else "no_active_gap_focus"
    )
    payload = {
        "version": 1,
        "active_gap_focus": active,
        "resumed_from": {
            "cycle_id": cycle_id,
            "work_id": work_id,
            "plan_id": plan_id,
            "status": prior_status,
            "goal_gap_addressed": gap_id,
        },
        "carried_forward_open_gaps": [dict(g) for g in open_gaps if isinstance(g, dict)],
        "blocked_retry_history": _merge_retry_history(
            list(prior.get("blocked_retry_history") or []),
            cycle_id=cycle_id,
            work_id=work_id,
            reason=reason,
            status=prior_status,
        ),
        "next_intended_capability_step": str(
            plan.get("next_focus_after") or state.get("next_recommended_focus") or active.get("id") or ""
        ),
        "resume_eligibility": {"eligible": eligible, "reason": eligibility_reason},
        "prior_cycle_status": prior_status,
        "source_hash": source_hash,
        "source_inputs": source_inputs,
        "last_refreshed_at": _now_iso(),
        "freshness": "fresh",
        "resumed_prior_intent": False,
    }
    save_continuity_state(payload)
    return payload


def resume_from_continuity(
    *,
    state: dict[str, Any],
    open_gaps: list[dict[str, Any]] | None = None,
    allow_stale: bool = True,
) -> dict[str, Any]:
    """Read continuity at cycle start; prefer carried-forward focus when still valid."""
    freshness = continuity_state_freshness(state=state)
    meta: dict[str, Any] = {
        "source": "continuity_state.json",
        "freshness": freshness,
        "used_stale": False,
        "regenerated": False,
        "resumed_prior_intent": False,
        "active_gap_focus": {},
        "resumed_from": {},
        "reason": "",
    }
    if not freshness.get("present"):
        meta["reason"] = "missing"
        return meta

    payload = load_continuity_state()
    if not freshness.get("hash_match"):
        if allow_stale:
            meta["used_stale"] = True
            meta["reason"] = "source_hash_mismatch"
            payload = dict(payload)
            payload["freshness"] = "stale"
            save_continuity_state(payload)
        else:
            # regenerate from current open gaps while keeping retry history
            open_gaps_doc = _load_json(OPEN_GAPS_STATE_PATH)
            gaps = open_gaps if open_gaps is not None else list(open_gaps_doc.get("open_gaps") or [])
            source_hash, source_inputs = compute_source_hash(state=state, open_gaps_doc=open_gaps_doc)
            active = _gap_focus(gaps, preferred_id=str((payload.get("active_gap_focus") or {}).get("id") or ""))
            payload = {
                **payload,
                "active_gap_focus": active,
                "carried_forward_open_gaps": [dict(g) for g in gaps if isinstance(g, dict)],
                "source_hash": source_hash,
                "source_inputs": source_inputs,
                "last_refreshed_at": _now_iso(),
                "freshness": "fresh",
                "resume_eligibility": {
                    "eligible": bool(active.get("id")),
                    "reason": "regenerated_after_stale",
                },
            }
            save_continuity_state(payload)
            meta["regenerated"] = True
            meta["freshness"] = continuity_state_freshness(state=state)
            meta["reason"] = "regenerated_after_stale"

    active = dict(payload.get("active_gap_focus") or {})
    resumed_from = dict(payload.get("resumed_from") or {})
    eligibility = dict(payload.get("resume_eligibility") or {})
    open_ids = {str(g.get("id") or "") for g in (open_gaps or payload.get("carried_forward_open_gaps") or []) if isinstance(g, dict)}
    focus_id = str(active.get("id") or "")
    still_valid = bool(focus_id and focus_id in open_ids) if open_ids else bool(focus_id)
    if focus_id.startswith("operational_") or focus_id.startswith("improve_") or focus_id.startswith("deliver_"):
        try:
            from loop_autonomous import evaluate_product_complete, live_soak_active
            from loop_production_ops import production_ops_active
            assessment = evaluate_product_complete()
            if focus_id.startswith("improve_") and production_ops_active():
                still_valid = True
            elif assessment.get("mechanics_complete") and (
                not assessment.get("operationally_realized") or live_soak_active()
            ):
                still_valid = True
            else:
                from loop_backlog import load_backlog, open_items
                open_work = {str(i.get("work_id") or "") for i in open_items(load_backlog())}
                still_valid = focus_id in open_work or still_valid
        except Exception:
            still_valid = True
    prior_status = str(payload.get("prior_cycle_status") or resumed_from.get("status") or "")
    resume = bool(eligibility.get("eligible")) and still_valid and prior_status in {"partial", "blocked", "ready"}
    if resume and prior_status in {"partial", "blocked"}:
        meta["resumed_prior_intent"] = True
        meta["reason"] = eligibility.get("reason") or f"resume_{prior_status}"
    elif resume:
        meta["resumed_prior_intent"] = True
        meta["reason"] = "carry_forward_top_gap"
    elif focus_id and not still_valid:
        meta["reason"] = "active_gap_no_longer_open"
    else:
        meta["reason"] = meta.get("reason") or eligibility.get("reason") or "not_eligible"

    meta["active_gap_focus"] = active
    meta["resumed_from"] = resumed_from
    meta["next_intended_capability_step"] = payload.get("next_intended_capability_step") or ""
    meta["blocked_retry_history"] = list(payload.get("blocked_retry_history") or [])
    meta["carried_forward_open_gaps"] = list(payload.get("carried_forward_open_gaps") or [])
    meta["prior_cycle_status"] = prior_status

    # mark resume application on the artifact for status/operators
    payload = dict(payload)
    payload["resumed_prior_intent"] = bool(meta["resumed_prior_intent"])
    payload["last_resume_at"] = _now_iso()
    payload["last_resume_reason"] = meta["reason"]
    if meta.get("used_stale"):
        payload["freshness"] = "stale"
    save_continuity_state(payload)
    return meta


def prefer_carried_forward_gaps(
    gaps: list[dict[str, Any]],
    continuity_meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Reorder gaps so valid carried-forward top focus stays first (no pointless rediscovery)."""
    if not continuity_meta or not continuity_meta.get("resumed_prior_intent"):
        return list(gaps)
    focus_id = str((continuity_meta.get("active_gap_focus") or {}).get("id") or "")
    if not focus_id:
        return list(gaps)
    preferred: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        if str(gap.get("id") or "") == focus_id:
            preferred.append(gap)
        else:
            rest.append(gap)
    return preferred + rest if preferred else list(gaps)


def prefer_continuity_work_item(
    items: list[dict[str, Any]],
    continuity_meta: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Pick backlog item tied to carried-forward gap / prior partial work when present."""
    if not items or not continuity_meta or not continuity_meta.get("resumed_prior_intent"):
        return None
    focus_id = str((continuity_meta.get("active_gap_focus") or {}).get("id") or "")
    prior_work = str((continuity_meta.get("resumed_from") or {}).get("work_id") or "")
    prior_status = str(continuity_meta.get("prior_cycle_status") or "")

    def _score(item: dict[str, Any]) -> tuple[int, int]:
        wid = str(item.get("work_id") or "")
        gap = str(item.get("goal_gap_addressed") or "")
        score = 50
        if prior_work and wid == prior_work and prior_status in {"partial", "blocked"}:
            score = 0
        elif focus_id and gap == focus_id:
            score = 1
        elif prior_work and (wid.endswith("_repair") or "_followup_" in wid) and prior_work in wid:
            score = 2
        elif focus_id and focus_id in wid:
            score = 3
        return (score, int(item.get("priority") or 99))

    ranked = sorted(items, key=_score)
    best = ranked[0]
    if _score(best)[0] >= 50:
        return None
    return best


def continuity_status_summary(*, state: dict[str, Any] | None = None) -> dict[str, Any]:
    freshness = continuity_state_freshness(state=state)
    payload = load_continuity_state() if freshness.get("present") else {}
    return {
        "present": freshness.get("present"),
        "status": freshness.get("status"),
        "fresh": freshness.get("fresh"),
        "stale": freshness.get("stale"),
        "last_refreshed_at": freshness.get("last_refreshed_at") or payload.get("last_refreshed_at"),
        "source_hash": freshness.get("source_hash"),
        "hash_match": freshness.get("hash_match"),
        "active_gap_focus": freshness.get("active_gap_focus") or payload.get("active_gap_focus") or {},
        "resumed_from": freshness.get("resumed_from") or payload.get("resumed_from") or {},
        "resumed_prior_intent": bool(payload.get("resumed_prior_intent")),
        "prior_cycle_status": payload.get("prior_cycle_status") or "",
        "next_intended_capability_step": payload.get("next_intended_capability_step") or "",
        "resume_eligibility": payload.get("resume_eligibility") or freshness.get("resume_eligibility") or {},
        "used_stale": bool(freshness.get("present") and freshness.get("stale")),
    }


def self_check() -> None:
    state = {
        "cycle_id": 3,
        "status": "partial",
        "open_gaps": [{"id": "gap_continuity_open_gaps", "description": "need continuity", "priority": 12}],
        "last_cycle": {"plan_id": "plan_a", "verification_passed": False, "artifact_dir": "x"},
        "last_verified_repo_delta": {"summary": "none"},
    }
    open_gaps = [
        {
            "id": "gap_continuity_open_gaps",
            "description": "need continuity",
            "classification": "persistence_resume",
            "priority": 12,
        },
        {
            "id": "gap_product_realization",
            "description": "product",
            "classification": "plan_generation",
            "priority": 40,
        },
    ]
    plan = {
        "plan_id": "product_continuity_state_resume",
        "backlog_work_id": "product_continuity_state_resume",
        "goal_gap_addressed": "gap_continuity_open_gaps",
        "next_focus_after": "resume continuity",
    }
    verification = {"passed": False, "summary": "partial"}
    written = write_continuity_after_cycle(
        cycle_id=3,
        state=state,
        plan=plan,
        verification=verification,
        open_gaps=open_gaps,
    )
    assert CONTINUITY_STATE_PATH.is_file()
    assert written["active_gap_focus"]["id"] == "gap_continuity_open_gaps"
    assert written["resumed_from"]["cycle_id"] == 3
    assert written["prior_cycle_status"] == "partial"
    assert written["blocked_retry_history"]
    meta = resume_from_continuity(state=state, open_gaps=open_gaps)
    assert meta["resumed_prior_intent"] is True
    assert meta["active_gap_focus"]["id"] == "gap_continuity_open_gaps"
    ordered = prefer_carried_forward_gaps(list(reversed(open_gaps)), meta)
    assert ordered[0]["id"] == "gap_continuity_open_gaps"
    item = prefer_continuity_work_item(
        [
            {"work_id": "other", "goal_gap_addressed": "gap_product_realization", "priority": 1},
            {"work_id": "product_continuity_state_resume", "goal_gap_addressed": "gap_continuity_open_gaps", "priority": 12},
        ],
        meta,
    )
    assert item and item["work_id"] == "product_continuity_state_resume"
    # stale path records used_stale
    stale_doc = load_continuity_state()
    stale_doc["source_hash"] = "deadbeef"
    save_continuity_state(stale_doc)
    stale_meta = resume_from_continuity(state=state, open_gaps=open_gaps, allow_stale=True)
    assert stale_meta["used_stale"] is True
    summary = continuity_status_summary(state=state)
    assert summary["present"] is True
    assert summary["status"] in {"fresh", "stale", "missing"}
    print("loop-continuity-state: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="continuity_state resume hydrator")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    parser.error("specify --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())