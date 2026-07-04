#!/usr/bin/env python3
"""Dispatch artifact inputs for planner, backlog scoring, and work packages. Stdlib only."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from loop_target_workspace import goal_path, runtime_root  # noqa: E402

GOAL_INDEX_PATH = runtime_root() / "goal_ingestion_index.json"
GOAL_MODEL_PATH = runtime_root() / "goal_model.json"
VERIFY_BRIEF_PATH = runtime_root() / "verification_brief.json"
RESEARCH_LOG_PATH = runtime_root() / "research_synthesis_log.json"
VERIFY_REGISTRY_PATH = runtime_root() / "verification_dispatch_registry.json"
FAILURE_CLASSIFICATIONS = (
    "verification_failed",
    "missing_output",
    "command_failed",
    "done_when_failed",
    "implementation_error",
)

STALE_SECONDS = 72 * 3600  # ponytail: fixed 72h freshness window; upgrade path = configurable TTL
WEAK_PATTERN_QUALITY = 0.4
GOAL_LOOP_CAPABILITIES = (
    "goal_ingestion",
    "repo_status_analysis",
    "research_synthesis",
    "plan_generation",
    "implementation_dispatch",
    "verification_dispatch",
    "persistence_resume",
    "schedule_control",
)
BOOTSTRAP_WORK_IDS = frozenset(
    {
        "product_dispatch_goal_index",
        "product_dispatch_research_log",
        "product_dispatch_verification_registry",
    }
)
BLOCKERS = frozenset(
    {
        "missing_goal_index",
        "missing_research_basis",
        "missing_verification_basis",
        "dependency_unready",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(ts: str) -> float | None:
    dt = _parse_iso(ts)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_goal_index() -> dict[str, Any]:
    return _load_json(GOAL_INDEX_PATH)


def load_research_log() -> dict[str, Any]:
    return _load_json(RESEARCH_LOG_PATH)


def load_verification_registry() -> dict[str, Any]:
    return _load_json(VERIFY_REGISTRY_PATH)


def extract_goal_model(goal_text: str, goal_index: dict[str, Any] | None = None) -> dict[str, Any]:
    goal_index = goal_index or load_goal_index()
    capabilities = list(goal_index.get("capabilities") or [])
    constraints = list(goal_index.get("constraints") or [])
    completion_criteria = list(goal_index.get("completion_criteria") or [])

    if not capabilities:
        step_map = (
            ("analyzes the goal", "goal_ingestion"),
            ("repository", "repo_status_analysis"),
            ("status", "repo_status_analysis"),
            ("research", "research_synthesis"),
            ("implementation plan", "plan_generation"),
            ("executes the plan", "implementation_dispatch"),
            ("verifies the work", "verification_dispatch"),
            ("continue from", "persistence_resume"),
            ("schedul", "schedule_control"),
        )
        for line in goal_text.splitlines():
            lower = line.lower()
            for needle, cap in step_map:
                if needle in lower and cap not in capabilities:
                    capabilities.append(cap)
    if len(capabilities) < 3:
        for cap in GOAL_LOOP_CAPABILITIES:
            if cap not in capabilities:
                capabilities.append(cap)
            if len(capabilities) >= 8:
                break

    if not constraints:
        in_non = False
        for line in goal_text.splitlines():
            if line.strip() == "## Non Goals":
                in_non = True
                continue
            if in_non and line.startswith("## "):
                break
            if in_non and line.strip().startswith("- "):
                constraints.append(line.strip()[2:].strip())

    if not completion_criteria:
        in_success = False
        for line in goal_text.splitlines():
            if line.strip() == "## Success Criteria":
                in_success = True
                continue
            if in_success and line.startswith("## "):
                break
            if in_success and line.strip().startswith("- "):
                completion_criteria.append(line.strip()[2:].strip())

    return {
        "capabilities": capabilities,
        "constraints": constraints,
        "completion_criteria": completion_criteria[:20],
        "source_updated_at": goal_index.get("updated_at") or goal_index.get("last_refreshed_at") or "",
    }


def goal_source_hash(goal_text: str) -> str:
    return hashlib.sha256(goal_text.encode("utf-8")).hexdigest()[:16]


def _extract_section_text(goal_text: str, heading: str) -> str:
    lines: list[str] = []
    in_section = False
    for line in goal_text.splitlines():
        if line.strip() == heading:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.strip():
            lines.append(line.strip())
    return " ".join(lines)[:800]


def _extract_maturity(goal_text: str) -> str:
    for line in goal_text.splitlines():
        if line.strip().startswith("Level ") and "—" in line:
            return line.strip()[:200]
    block = _extract_section_text(goal_text, "## Repository Maturity Level")
    return block[:200]


def build_structured_goal_model(goal_text: str, goal_index: dict[str, Any] | None = None) -> dict[str, Any]:
    base = extract_goal_model(goal_text, goal_index)
    now = _now_iso()
    return {
        "version": 1,
        "mission": _extract_section_text(goal_text, "## Product Goal"),
        "capabilities": base["capabilities"],
        "constraints": base["constraints"],
        "completion_criteria": base["completion_criteria"],
        "maturity": _extract_maturity(goal_text),
        "source_hash": goal_source_hash(goal_text),
        "source_path": "project_goals.md",
        "last_refreshed_at": now,
        "freshness": "fresh",
    }


def goal_model_freshness(*, goal_text: str | None = None) -> dict[str, Any]:
    goal_text = goal_text if goal_text is not None else (goal_path().read_text(encoding="utf-8") if goal_path().is_file() else "")
    current_hash = goal_source_hash(goal_text) if goal_text.strip() else ""
    if not GOAL_MODEL_PATH.is_file():
        return {
            "status": "missing",
            "fresh": False,
            "stale": True,
            "present": False,
            "updated_at": None,
            "last_refreshed_at": None,
            "source_hash": None,
            "current_source_hash": current_hash,
            "hash_match": False,
        }
    payload = _load_json(GOAL_MODEL_PATH)
    stored_hash = str(payload.get("source_hash") or "")
    hash_match = bool(stored_hash and stored_hash == current_hash)
    stored_freshness = str(payload.get("freshness") or "")
    if hash_match and stored_freshness != "stale":
        status = "fresh"
    elif hash_match:
        status = "fresh"
    else:
        status = "stale"
    updated = payload.get("last_refreshed_at") or payload.get("updated_at") or ""
    age = _age_seconds(updated)
    time_fresh = age is not None and age <= STALE_SECONDS
    fresh = hash_match and time_fresh and status == "fresh"
    return {
        "status": status,
        "fresh": fresh,
        "stale": not fresh,
        "present": True,
        "updated_at": updated,
        "last_refreshed_at": updated,
        "source_hash": stored_hash or None,
        "current_source_hash": current_hash,
        "hash_match": hash_match,
        "age_seconds": age,
    }


def load_goal_model(*, goal_text: str | None = None, allow_stale: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    goal_text = goal_text if goal_text is not None else (goal_path().read_text(encoding="utf-8") if goal_path().is_file() else "")
    freshness = goal_model_freshness(goal_text=goal_text)
    meta: dict[str, Any] = {
        "source": "goal_model.json",
        "freshness": freshness,
        "used_stale": False,
        "regenerated": False,
    }
    if not freshness.get("present"):
        model = build_structured_goal_model(goal_text)
        meta["regenerated"] = True
        meta["reason"] = "missing_artifact"
        return model, meta
    payload = _load_json(GOAL_MODEL_PATH)
    if not freshness.get("hash_match"):
        if allow_stale:
            payload = dict(payload)
            payload["freshness"] = "stale"
            meta["used_stale"] = True
            meta["reason"] = "source_hash_mismatch"
            return payload, meta
        model = build_structured_goal_model(goal_text)
        meta["regenerated"] = True
        meta["reason"] = "source_hash_mismatch_regenerated"
        return model, meta
    return payload, meta


def persist_goal_model_file(goal_text: str) -> dict[str, Any]:
    """Write canonical structured goal_model.json from project_goals.md."""
    model = build_structured_goal_model(goal_text)
    GOAL_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOAL_MODEL_PATH.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    persist_goal_model_to_index(goal_text)
    return model


def ensure_goal_model(goal_text: str | None = None, *, regenerate: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    goal_text = goal_text if goal_text is not None else (goal_path().read_text(encoding="utf-8") if goal_path().is_file() else "")
    freshness = goal_model_freshness(goal_text=goal_text)
    if regenerate or not freshness.get("present") or not freshness.get("hash_match"):
        model = persist_goal_model_file(goal_text)
        meta = {
            "source": "goal_model.json",
            "freshness": goal_model_freshness(goal_text=goal_text),
            "used_stale": False,
            "regenerated": True,
            "reason": "regenerated" if regenerate else ("missing_artifact" if not freshness.get("present") else "source_hash_mismatch"),
        }
        return model, meta
    return load_goal_model(goal_text=goal_text, allow_stale=False)


def _verification_contract_from_sources(
    *,
    plan: dict[str, Any] | None = None,
    package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = plan or {}
    package = package or plan.get("work_package") or {}
    wid = str(package.get("work_id") or plan.get("backlog_work_id") or plan.get("plan_id") or "")
    plan_id = str(plan.get("plan_id") or wid)
    cycle_id = int(package.get("cycle_id") or plan.get("cycle_id") or 0)
    vcmds = [list(c) for c in (package.get("verification_commands") or plan.get("verification_commands") or [])]
    expected = list(package.get("expected_outputs") or plan.get("expected_outputs") or [])
    done_when = list(package.get("done_when") or plan.get("done_when") or [])
    proposed = list(
        package.get("proposed_repo_delta")
        or plan.get("proposed_repo_delta")
        or plan.get("expected_repo_delta")
        or []
    )
    return {
        "work_id": wid,
        "plan_id": plan_id,
        "cycle_id": cycle_id,
        "verification_commands": vcmds,
        "expected_outputs": expected,
        "done_when": done_when,
        "proposed_repo_delta": proposed,
        "verification_objective": package.get("objective") or plan.get("focus") or plan.get("description") or "",
        "source_inputs": package.get("inputs_used")
        or {
            "goal_inputs": package.get("goal_inputs") or plan.get("goal_inputs") or {},
            "research_inputs": package.get("research_inputs") or plan.get("research_inputs") or {},
            "verification_basis": package.get("verification_basis") or plan.get("verification_basis") or {},
        },
    }


def verification_brief_source_hash(*, contract: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "work_id": contract.get("work_id"),
            "plan_id": contract.get("plan_id"),
            "cycle_id": contract.get("cycle_id"),
            "verification_commands": contract.get("verification_commands") or [],
            "expected_outputs": contract.get("expected_outputs") or [],
            "done_when": contract.get("done_when") or [],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_structured_verification_brief(
    *,
    plan: dict[str, Any] | None = None,
    package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = _verification_contract_from_sources(plan=plan, package=package)
    now = _now_iso()
    source_hash = verification_brief_source_hash(contract=contract)
    return {
        "version": 1,
        "work_id": contract["work_id"],
        "plan_id": contract["plan_id"],
        "cycle_id": contract["cycle_id"],
        "verification_objective": contract["verification_objective"],
        "verification_commands": contract["verification_commands"],
        "expected_outputs": contract["expected_outputs"],
        "proposed_repo_delta": contract["proposed_repo_delta"],
        "success_conditions": contract["done_when"],
        "failure_classifications": list(FAILURE_CLASSIFICATIONS),
        "source_inputs": contract["source_inputs"],
        "source_hash": source_hash,
        "source_path": "verification_brief.json",
        "last_refreshed_at": now,
        "freshness": "fresh",
    }


def verification_brief_freshness(
    *,
    plan: dict[str, Any] | None = None,
    package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = _verification_contract_from_sources(plan=plan, package=package) if (plan or package) else {}
    current_hash = verification_brief_source_hash(contract=contract) if contract.get("work_id") else ""
    if not VERIFY_BRIEF_PATH.is_file():
        return {
            "status": "missing",
            "fresh": False,
            "stale": True,
            "present": False,
            "updated_at": None,
            "last_refreshed_at": None,
            "source_hash": None,
            "current_source_hash": current_hash or None,
            "hash_match": False,
        }
    payload = _load_json(VERIFY_BRIEF_PATH)
    stored_hash = str(payload.get("source_hash") or "")
    hash_match = bool(current_hash and stored_hash == current_hash) if current_hash else bool(stored_hash)
    status = "fresh" if hash_match else "stale"
    updated = payload.get("last_refreshed_at") or payload.get("updated_at") or ""
    age = _age_seconds(updated)
    time_fresh = age is not None and age <= STALE_SECONDS
    fresh = hash_match and time_fresh and str(payload.get("freshness") or "") != "stale"
    return {
        "status": status,
        "fresh": fresh,
        "stale": not fresh,
        "present": True,
        "updated_at": updated,
        "last_refreshed_at": updated,
        "source_hash": stored_hash or None,
        "current_source_hash": current_hash or None,
        "hash_match": hash_match,
        "age_seconds": age,
    }


def load_verification_brief(
    *,
    plan: dict[str, Any] | None = None,
    package: dict[str, Any] | None = None,
    allow_stale: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    freshness = verification_brief_freshness(plan=plan, package=package)
    meta: dict[str, Any] = {
        "source": "verification_brief.json",
        "freshness": freshness,
        "used_stale": False,
        "regenerated": False,
    }
    if not freshness.get("present"):
        brief = build_structured_verification_brief(plan=plan, package=package)
        meta["regenerated"] = True
        meta["reason"] = "missing_artifact"
        return brief, meta
    payload = _load_json(VERIFY_BRIEF_PATH)
    if not freshness.get("hash_match") and (plan or package):
        if allow_stale:
            payload = dict(payload)
            payload["freshness"] = "stale"
            meta["used_stale"] = True
            meta["reason"] = "source_hash_mismatch"
            return payload, meta
        brief = build_structured_verification_brief(plan=plan, package=package)
        meta["regenerated"] = True
        meta["reason"] = "source_hash_mismatch_regenerated"
        return brief, meta
    return payload, meta


def persist_verification_brief_file(
    *,
    plan: dict[str, Any] | None = None,
    package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    brief = build_structured_verification_brief(plan=plan, package=package)
    VERIFY_BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
    VERIFY_BRIEF_PATH.write_text(json.dumps(brief, indent=2) + "\n", encoding="utf-8")
    return brief


def ensure_verification_brief(
    *,
    plan: dict[str, Any] | None = None,
    package: dict[str, Any] | None = None,
    regenerate: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    freshness = verification_brief_freshness(plan=plan, package=package)
    if regenerate or not freshness.get("present") or not freshness.get("hash_match"):
        brief = persist_verification_brief_file(plan=plan, package=package)
        meta = {
            "source": "verification_brief.json",
            "freshness": verification_brief_freshness(plan=plan, package=package),
            "used_stale": False,
            "regenerated": True,
            "reason": "regenerated"
            if regenerate
            else ("missing_artifact" if not freshness.get("present") else "source_hash_mismatch"),
        }
        return brief, meta
    return load_verification_brief(plan=plan, package=package, allow_stale=False)


def resolve_verification_contract(
    *,
    plan: dict[str, Any] | None = None,
    package: dict[str, Any] | None = None,
    allow_stale: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    brief, meta = load_verification_brief(plan=plan, package=package, allow_stale=allow_stale)
    contract = {
        "work_id": brief.get("work_id"),
        "plan_id": brief.get("plan_id"),
        "verification_objective": brief.get("verification_objective"),
        "verification_commands": list(brief.get("verification_commands") or []),
        "expected_outputs": list(brief.get("expected_outputs") or []),
        "success_conditions": list(brief.get("success_conditions") or []),
        "proposed_repo_delta": list(brief.get("proposed_repo_delta") or []),
        "failure_classifications": list(brief.get("failure_classifications") or []),
        "source_hash": brief.get("source_hash"),
        "source_inputs": brief.get("source_inputs") or {},
    }
    meta["verification_brief_used"] = bool(brief.get("work_id"))
    return contract, meta



def artifact_freshness(*, goal_text: str | None = None) -> dict[str, Any]:
    goal_text_arg = goal_text
    artifacts = {
        "goal_ingestion_index": GOAL_INDEX_PATH,
        "goal_model": GOAL_MODEL_PATH,
        "verification_brief": VERIFY_BRIEF_PATH,
        "research_synthesis_log": RESEARCH_LOG_PATH,
        "verification_dispatch_registry": VERIFY_REGISTRY_PATH,
    }
    out: dict[str, Any] = {}
    gm_fresh = goal_model_freshness(goal_text=goal_text_arg)
    out["goal_model"] = gm_fresh
    out["verification_brief"] = verification_brief_freshness()
    from loop_open_gaps_state import open_gaps_state_freshness
    from loop_continuity_state import continuity_state_freshness

    out["open_gaps_state"] = open_gaps_state_freshness(goal_text=goal_text_arg)
    out["continuity_state"] = continuity_state_freshness()
    for name, path in artifacts.items():
        if name in {"goal_model", "verification_brief", "open_gaps_state"}:
            continue
        if not path.is_file():
            out[name] = {"status": "missing", "fresh": False, "stale": True, "updated_at": None}
            continue
        payload = _load_json(path)
        updated = payload.get("updated_at") or payload.get("last_refreshed_at") or ""
        age = _age_seconds(updated)
        fresh = age is not None and age <= STALE_SECONDS
        out[name] = {
            "status": "fresh" if fresh else "stale",
            "fresh": fresh,
            "stale": not fresh,
            "updated_at": updated,
            "age_seconds": age,
        }
    return out


def _research_for_capability(log: dict[str, Any], capability: str) -> dict[str, Any] | None:
    records = log.get("records") or []
    matches = [r for r in records if str(r.get("capability_area") or "") == capability or capability in str(r.get("research_summary") or "")]
    if not matches:
        matches = records[-1:] if records else []
    if not matches:
        return None
    return max(matches, key=lambda r: r.get("synthesized_at") or "")


def _best_verification_pattern(registry: dict[str, Any], capability: str, task_type: str) -> dict[str, Any] | None:
    patterns = registry.get("patterns") or []
    candidates = [
        p
        for p in patterns
        if p.get("passed")
        and float(p.get("quality") or 0) > WEAK_PATTERN_QUALITY
        and p.get("capability") == capability
    ]
    if task_type:
        typed = [p for p in candidates if p.get("task_type") == task_type]
        if typed:
            candidates = typed
    if not candidates and task_type:
        candidates = [
            p
            for p in patterns
            if p.get("passed")
            and float(p.get("quality") or 0) > WEAK_PATTERN_QUALITY
            and p.get("task_type") == task_type
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: float(p.get("quality") or 0))


def _dependency_blocker(item: dict[str, Any], freshness: dict[str, Any]) -> str:
    wid = str(item.get("work_id") or "")
    if wid == "product_dispatch_goal_index":
        return ""
    gm = freshness.get("goal_model") or {}
    goal_ok = gm.get("fresh") or (gm.get("present") and gm.get("hash_match"))
    if not goal_ok:
        goal_ok = freshness.get("goal_ingestion_index", {}).get("fresh") or GOAL_INDEX_PATH.is_file()
    research_ok = freshness.get("research_synthesis_log", {}).get("fresh") or RESEARCH_LOG_PATH.is_file()
    if wid == "product_dispatch_research_log":
        return "" if goal_ok else "missing_goal_index"
    if wid == "product_dispatch_verification_registry":
        if not goal_ok:
            return "missing_goal_index"
        return "" if research_ok else "missing_research_basis"
    if not goal_ok and wid not in BOOTSTRAP_WORK_IDS:
        return "missing_goal_index"
    if wid not in BOOTSTRAP_WORK_IDS and item.get("task_type") == "verification_hardening" and not research_ok:
        return "missing_research_basis"
    return ""


def score_backlog_item(
    item: dict[str, Any],
    *,
    goal_model: dict[str, Any],
    research_log: dict[str, Any],
    verify_registry: dict[str, Any],
    freshness: dict[str, Any],
    verified_capabilities: set[str] | None = None,
) -> dict[str, Any]:
    verified_capabilities = verified_capabilities or set()
    capability = str(item.get("capability") or "")
    base = int(item.get("priority") or 99)
    blocker = _dependency_blocker(item, freshness)

    goal_coverage = 0.0
    if capability in goal_model.get("capabilities") or []:
        goal_coverage = 2.0 if capability not in verified_capabilities else 0.5

    research_record = _research_for_capability(research_log, capability)
    evidence_fresh = False
    if research_record:
        age = _age_seconds(str(research_record.get("synthesized_at") or ""))
        evidence_fresh = age is not None and age <= STALE_SECONDS

    pattern = _best_verification_pattern(verify_registry, capability, str(item.get("task_type") or ""))
    verification_ready = pattern is not None

    requires_evidence = (
        item.get("task_type") == "verification_hardening"
        or item.get("dispatch_target") in {"research_synthesis", "verification_dispatch"}
    ) and str(item.get("work_id")) not in BOOTSTRAP_WORK_IDS

    if requires_evidence and not research_record and not blocker:
        blocker = "missing_research_basis"
    registry = load_verification_registry()
    if item.get("dispatch_target") == "verification_dispatch" and not (registry.get("patterns") or []) and not VERIFY_REGISTRY_PATH.is_file():
        if not blocker:
            blocker = "missing_verification_basis"
    if pattern and float(pattern.get("quality") or 0) <= WEAK_PATTERN_QUALITY:
        blocker = blocker or "missing_verification_basis"

    score = base - goal_coverage * 5
    if evidence_fresh:
        score -= 3
    elif research_record:
        score += 2
    if verification_ready:
        score -= 2
    if blocker:
        score += 50

    goal_inputs = {
        "capabilities": [c for c in goal_model.get("capabilities") or [] if c == capability or capability in (goal_model.get("capabilities") or [])],
        "matching_capability": capability,
        "completion_criteria": (goal_model.get("completion_criteria") or [])[:3],
        "constraints": (goal_model.get("constraints") or [])[:2],
        "index_updated_at": goal_model.get("source_updated_at"),
    }
    research_inputs = {
        "capability_area": (research_record or {}).get("capability_area"),
        "research_summary": (research_record or {}).get("research_summary"),
        "synthesized_at": (research_record or {}).get("synthesized_at"),
        "fresh": evidence_fresh,
        "work_id": (research_record or {}).get("work_id"),
    }
    verification_basis = {
        "reused_pattern_work_id": (pattern or {}).get("work_id"),
        "verification_commands": (pattern or {}).get("verification_commands") or [],
        "done_when": (pattern or {}).get("done_when") or [],
        "quality": (pattern or {}).get("quality"),
        "registry_updated_at": verify_registry.get("updated_at"),
    }

    rationale_parts = [f"capability={capability}", f"goal_coverage={goal_coverage:.1f}"]
    if research_record:
        rationale_parts.append(f"evidence={research_record.get('work_id')} fresh={evidence_fresh}")
    if pattern:
        rationale_parts.append(f"verify_pattern={pattern.get('work_id')}")
    if blocker:
        rationale_parts.append(f"blocker={blocker}")

    return {
        "artifact_score": score,
        "blocker": blocker,
        "goal_inputs": goal_inputs,
        "research_inputs": research_inputs,
        "verification_basis": verification_basis,
        "evidence_backed": bool(research_record) and evidence_fresh,
        "selection_rationale": "; ".join(rationale_parts),
    }


def apply_artifact_scoring(
    items: list[dict[str, Any]],
    *,
    goal_text: str,
    research: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    goal_model, _goal_meta = ensure_goal_model(goal_text)
    research_log = load_research_log()
    verify_registry = load_verification_registry()
    freshness = artifact_freshness(goal_text=goal_text)
    verified_caps = {
        str(i.get("capability") or "")
        for i in items
        if i.get("status") == "verified" and i.get("capability")
    }

    scored: list[dict[str, Any]] = []
    for item in items:
        merged = dict(item)
        if item.get("status") != "open":
            scored.append(merged)
            continue
        meta = score_backlog_item(
            item,
            goal_model=goal_model,
            research_log=research_log,
            verify_registry=verify_registry,
            freshness=freshness,
            verified_capabilities=verified_caps,
        )
        merged["artifact_score"] = meta["artifact_score"]
        merged["goal_inputs"] = meta["goal_inputs"]
        merged["research_inputs"] = meta["research_inputs"]
        merged["verification_basis"] = meta["verification_basis"]
        merged["evidence_backed"] = meta["evidence_backed"]
        merged["selection_rationale"] = meta["selection_rationale"]
        if meta["blocker"]:
            merged["blocked_by"] = meta["blocker"]
        elif merged.get("blocked_by") in BLOCKERS:
            merged["blocked_by"] = ""

        pattern = _best_verification_pattern(
            verify_registry,
            str(item.get("capability") or ""),
            str(item.get("task_type") or ""),
        )
        if (
            pattern
            and pattern.get("verification_commands")
            and item.get("generated_from") != "target_backlog_refresh"
            and not item.get("local_only")
            and not item.get("verification_commands")
        ):
            merged["verification_commands"] = [list(c) for c in pattern["verification_commands"]]
            merged["done_when"] = list(pattern.get("done_when") or merged.get("done_when") or [])
            why = merged.get("why_now") or merged.get("objective") or ""
            if pattern.get("work_id") and str(pattern.get("work_id")) not in why:
                merged["why_now"] = f"{why} Reuses verification pattern from {pattern['work_id']}.".strip()
        elif item.get("generated_from") == "target_backlog_refresh" and item.get("verification_commands"):
            merged["verification_commands"] = [list(c) for c in item["verification_commands"]]
            merged["done_when"] = list(item.get("done_when") or [])
            merged["verification_basis"] = {
                "reused_pattern_work_id": str(item.get("work_id") or ""),
                "verification_commands": merged["verification_commands"],
                "done_when": merged["done_when"],
                "quality": 1.0,
                "registry_updated_at": verify_registry.get("updated_at"),
            }

        if meta["research_inputs"].get("research_summary"):
            fact = str(meta["research_inputs"]["research_summary"])[:180]
            why = merged.get("why_now") or merged.get("objective") or ""
            if fact[:40] not in why:
                merged["why_now"] = f"{why} Evidence: {fact}".strip()
                merged["research_fact"] = fact

        scored.append(merged)

    scored.sort(key=lambda i: (int(i.get("artifact_score") or i.get("priority") or 99), int(i.get("priority") or 99)))
    return scored


def build_package_lineage(
    item: dict[str, Any],
    *,
    goal_text: str,
    research: dict[str, Any],
) -> dict[str, Any]:
    verify_registry = load_verification_registry()
    capability = str(item.get("capability") or "")
    pattern = _best_verification_pattern(verify_registry, capability, str(item.get("task_type") or ""))
    if pattern and pattern.get("verification_commands") and not item.get("verification_commands"):
        item = {
            **item,
            "verification_commands": [list(c) for c in pattern["verification_commands"]],
            "done_when": list(pattern.get("done_when") or item.get("done_when") or []),
        }
    goal_model, goal_model_meta = ensure_goal_model(goal_text)
    meta = score_backlog_item(
        item,
        goal_model=goal_model,
        research_log=load_research_log(),
        verify_registry=load_verification_registry(),
        freshness=artifact_freshness(goal_text=goal_text),
    )
    if research.get("summary") and not meta["research_inputs"].get("research_summary"):
        meta["research_inputs"]["research_summary"] = str(research["summary"])[:400]
        meta["research_inputs"]["capability_area"] = research.get("capability_area")
    meta["selection_rationale"] = item.get("selection_rationale") or meta["selection_rationale"]
    meta["evidence_backed"] = item.get("evidence_backed", meta["evidence_backed"])
    return {
        "goal_inputs": item.get("goal_inputs") or meta["goal_inputs"],
        "research_inputs": item.get("research_inputs") or meta["research_inputs"],
        "verification_basis": item.get("verification_basis") or meta["verification_basis"],
        "selection_rationale": meta["selection_rationale"],
        "evidence_backed": meta["evidence_backed"],
    }


def record_verification_pattern(
    *,
    plan: dict[str, Any],
    verification: dict[str, Any],
    execution: dict[str, Any],
) -> None:
    pkg = plan.get("work_package") or {}
    passed = bool(verification.get("passed"))
    checks = verification.get("checks") or []
    passed_count = sum(1 for c in checks if c.get("passed"))
    total = len(checks) or 1
    quality = round(passed_count / total, 3) if passed else round(passed_count / total * 0.5, 3)

    registry = load_verification_registry()
    patterns = list(registry.get("patterns") or [])
    entry = {
        "work_id": plan.get("backlog_work_id") or plan.get("work_id"),
        "capability": plan.get("capability") or pkg.get("capability"),
        "task_type": plan.get("task_type") or pkg.get("task_type"),
        "verification_commands": plan.get("verification_commands") or pkg.get("verification_commands") or [],
        "done_when": plan.get("done_when") or pkg.get("done_when") or [],
        "quality": quality,
        "passed": passed,
        "recorded_at": _now_iso(),
        "dispatch_target": execution.get("dispatch_target") or pkg.get("dispatch_target"),
    }
    patterns = [p for p in patterns if p.get("work_id") != entry["work_id"]]
    patterns.append(entry)
    registry["patterns"] = patterns[-100:]
    registry["updated_at"] = _now_iso()
    if not registry.get("entries"):
        registry["entries"] = []
    VERIFY_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    VERIFY_REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


def persist_goal_model_to_index(goal_text: str) -> None:
    """Refresh extracted goal model on goal index after ingestion handler runs."""
    index = load_goal_index()
    structured = build_structured_goal_model(goal_text, index)
    index["capabilities"] = structured["capabilities"]
    index["constraints"] = structured["constraints"]
    index["completion_criteria"] = structured["completion_criteria"]
    index["mission"] = structured.get("mission") or ""
    index["maturity"] = structured.get("maturity") or ""
    index["source_hash"] = structured.get("source_hash") or ""
    index["updated_at"] = _now_iso()
    index.setdefault("version", 1)
    GOAL_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOAL_INDEX_PATH.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def self_check() -> None:
    goal_text = goal_path().read_text(encoding="utf-8")
    model = extract_goal_model(goal_text)
    assert len(model["capabilities"]) >= 3
    persisted = persist_goal_model_file(goal_text)
    assert GOAL_MODEL_PATH.is_file()
    assert persisted.get("mission")
    assert persisted.get("source_hash")
    assert persisted.get("maturity")
    loaded, meta = load_goal_model(goal_text=goal_text)
    assert loaded.get("capabilities")
    assert meta["source"] == "goal_model.json"
    fresh = artifact_freshness(goal_text=goal_text)
    assert "goal_ingestion_index" in fresh
    assert "goal_model" in fresh
    assert fresh["goal_model"].get("present")
    stale_text = goal_text + "\n# self-check mutation\n"
    stale_meta = goal_model_freshness(goal_text=stale_text)
    assert stale_meta["status"] == "stale"
    stale_model, stale_load = load_goal_model(goal_text=stale_text, allow_stale=True)
    assert stale_load["used_stale"] is True
    regen, regen_meta = ensure_goal_model(stale_text, regenerate=True)
    assert regen_meta["regenerated"] is True
    assert regen.get("source_hash") == goal_source_hash(stale_text)
    persist_goal_model_file(goal_text)

    pkg = {
        "work_id": "self_check_verify",
        "cycle_id": 0,
        "objective": "self-check verification brief",
        "verification_commands": [["python3", "scripts/loop_artifact_inputs.py", "--self-check"]],
        "expected_outputs": ["project_memory/runtime/verification_brief.json"],
        "done_when": ["file exists: project_memory/runtime/verification_brief.json"],
        "proposed_repo_delta": ["project_memory/runtime/verification_brief.json"],
    }
    plan = {"plan_id": "self_check_verify", "cycle_id": 0, "verification_commands": pkg["verification_commands"]}
    brief = persist_verification_brief_file(plan=plan, package=pkg)
    assert VERIFY_BRIEF_PATH.is_file()
    assert brief.get("source_hash")
    loaded, vmeta = load_verification_brief(plan=plan, package=pkg)
    assert loaded.get("work_id") == "self_check_verify"
    assert vmeta["source"] == "verification_brief.json"
    stale_pkg = {**pkg, "verification_commands": [["echo", "stale"]]}
    assert verification_brief_freshness(plan=plan, package=stale_pkg)["status"] == "stale"
    contract, cmeta = resolve_verification_contract(plan=plan, package=pkg)
    assert contract.get("verification_commands")
    assert cmeta.get("verification_brief_used")

    items = [
        {
            "work_id": "a",
            "capability": "goal_ingestion",
            "task_type": "code_implementation",
            "priority": 20,
            "status": "open",
            "objective": "test goal item",
            "target_files": ["x"],
            "proposed_repo_delta": ["x"],
            "verification_commands": [],
            "done_when": ["file exists: x"],
        },
        {
            "work_id": "b",
            "capability": "schedule_control",
            "task_type": "code_implementation",
            "priority": 10,
            "status": "open",
            "objective": "test schedule item",
            "target_files": ["y"],
            "proposed_repo_delta": ["y"],
            "verification_commands": [],
            "done_when": ["file exists: y"],
        },
    ]
    scored = apply_artifact_scoring(items, goal_text=goal_text)
    assert scored[0].get("artifact_score") is not None
    assert scored[0].get("selection_rationale")

    lineage = build_package_lineage(scored[0], goal_text=goal_text, research={"summary": "t"})
    assert lineage.get("goal_inputs")
    assert lineage.get("research_inputs") is not None
    assert lineage.get("verification_basis") is not None

    verify_item = {
        "work_id": "verify_reuse_test",
        "capability": "verification_dispatch",
        "task_type": "verification_hardening",
        "priority": 17,
        "verification_commands": [],
        "done_when": [],
    }
    record_verification_pattern(
        plan={
            "backlog_work_id": "prior_verify",
            "capability": "research_synthesis",
            "task_type": "verification_hardening",
            "verification_commands": [["python3", "scripts/loop_dispatch.py", "--self-check"]],
            "done_when": ["file exists: test.json"],
            "work_package": {},
        },
        verification={"passed": True, "checks": [{"passed": True}]},
        execution={"dispatch_target": "research_synthesis"},
    )
    lineage2 = build_package_lineage(verify_item, goal_text=goal_text, research={"summary": "t"})
    assert lineage2["verification_basis"].get("reused_pattern_work_id")
    assert lineage2["verification_basis"].get("verification_commands")
    print("loop-artifact-inputs: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo artifact inputs")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    parser.error("specify --self-check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
