#!/usr/bin/env python3
"""Runtime governance tier classification and degraded/denied reason taxonomy."""

from __future__ import annotations

from typing import Any

FULLY_GOVERNED = "fully_governed"
PARTIALLY_GOVERNED = "partially_governed"
UNGCOVERNED = "ungoverned"

GOVERNANCE_TIERS = (FULLY_GOVERNED, PARTIALLY_GOVERNED, UNGCOVERNED)

CURSOR_SESSION_ENTRYPOINT = "cursor_session"

# Canonical reason codes — observability must use these, not free text.
DEGRADED_REASONS = (
    "not_cursor_session_entrypoint",
    "navigation_blocked_at_start",
    "navigation_required_but_no_pack_ids",
    "navigation_required_but_mimir_unavailable",
    "policy_denied",
    "raw_shell_requires_structured_adapter",
    "navigation_required_for_broad_edit",
    "blocked_destructive_command",
    "missing_approval_request_id",
    "pack_quality_misleading",
    "pack_quality_wasteful",
    "ungoverned_success_risk",
    "unknown",
)

DENIED_REASONS = (
    "policy_denied",
    "navigation_blocked_at_start",
    "navigation_required_but_no_pack_ids",
    "navigation_required_but_mimir_unavailable",
    "raw_shell_requires_structured_adapter",
    "navigation_required_for_broad_edit",
    "blocked_destructive_command",
    "missing_approval_request_id",
    "unknown",
)

_REASON_ALIASES = {
    "navigation_required_but_no_pack_ids": "navigation_required_but_no_pack_ids",
    "navigation_required_but_mimir_unavailable": "navigation_required_but_mimir_unavailable",
    "raw_shell_requires_structured_adapter": "raw_shell_requires_structured_adapter",
    "navigation_required_for_broad_edit": "navigation_required_for_broad_edit",
    "blocked_destructive_command": "blocked_destructive_command",
    "missing_approval_request_id": "missing_approval_request_id",
    "not_cursor_session_entrypoint": "not_cursor_session_entrypoint",
}


def _pack_ids(details: dict[str, Any]) -> list[str]:
    packs = list(details.get("code_navigation_pack_ids") or [])
    if packs:
        return packs
    context_plan = details.get("context_plan") or {}
    packs = list(context_plan.get("code_navigation_pack_ids") or [])
    if packs:
        return packs
    nav = context_plan.get("code_navigation") or {}
    return list(nav.get("pack_ids") or [])


def canonicalize_reason(raw: str | None, *, allowed: tuple[str, ...]) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if text in allowed:
        return text
    if text in _REASON_ALIASES:
        return _REASON_ALIASES[text]
    if "navigation_required_but" in text:
        return "navigation_required_but_no_pack_ids"
    if "raw_shell" in text:
        return "raw_shell_requires_structured_adapter"
    if "policy" in text.lower():
        return "policy_denied"
    return "unknown"


def infer_denied_reason(details: dict[str, Any] | None) -> str | None:
    payload = details or {}
    hook_status = str(payload.get("hook_status") or payload.get("status") or "")
    if hook_status != "deny":
        return None
    for key in ("denied_reason", "denial_reason", "blocked_reason"):
        reason = canonicalize_reason(str(payload.get(key) or ""), allowed=DENIED_REASONS)
        if reason and reason != "unknown":
            return reason
    if payload.get("policy_reasons"):
        return "policy_denied"
    return "unknown"


def infer_degraded_reason(details: dict[str, Any] | None) -> str | None:
    payload = details or {}
    denied = infer_denied_reason(payload)
    if denied:
        return denied
    explicit = payload.get("degraded_reason") or payload.get("blocked_reason")
    reason = canonicalize_reason(str(explicit or ""), allowed=DEGRADED_REASONS)
    if reason:
        return reason
    pack_state = str((payload.get("pack_quality") or {}).get("pack_quality_state") or payload.get("pack_quality_state") or "")
    if pack_state == "misleading":
        return "pack_quality_misleading"
    if pack_state == "wasteful":
        return "pack_quality_wasteful"
    entrypoint = str(payload.get("entrypoint") or payload.get("governance_entrypoint") or "")
    if entrypoint and entrypoint != CURSOR_SESSION_ENTRYPOINT:
        return "not_cursor_session_entrypoint"
    if payload.get("ungoverned_success_risk"):
        return "ungoverned_success_risk"
    return None


def classify_governance_tier(details: dict[str, Any] | None) -> str:
    payload = details or {}
    entrypoint = str(payload.get("entrypoint") or payload.get("governance_entrypoint") or "unknown")
    if entrypoint != CURSOR_SESSION_ENTRYPOINT:
        return UNGCOVERNED

    navigation_required = payload.get("navigation_required")
    if navigation_required is None:
        navigation_required = (payload.get("context_plan") or {}).get("navigation_required")
    packs = _pack_ids(payload)
    blocked = bool(payload.get("navigation_blocked") or payload.get("blocked_reason"))
    hook_status = str(payload.get("hook_status") or payload.get("status") or "")
    pack_state = str((payload.get("pack_quality") or {}).get("pack_quality_state") or payload.get("pack_quality_state") or "")

    if hook_status == "deny" or blocked:
        return PARTIALLY_GOVERNED
    if navigation_required and not packs:
        return PARTIALLY_GOVERNED
    if navigation_required and not payload.get("mimir_available", True):
        return PARTIALLY_GOVERNED
    if pack_state in {"misleading", "absent"}:
        return PARTIALLY_GOVERNED
    return FULLY_GOVERNED


def is_fully_governed(details: dict[str, Any] | None) -> bool:
    return classify_governance_tier(details) == FULLY_GOVERNED


def build_governance_observability(details: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(details or {})
    tier = classify_governance_tier(payload)
    denied_reason = infer_denied_reason(payload)
    degraded_reason = infer_degraded_reason(payload) if tier != FULLY_GOVERNED else None
    entrypoint = str(payload.get("entrypoint") or payload.get("governance_entrypoint") or "unknown")
    hook_status = str(payload.get("hook_status") or payload.get("status") or "")
    ungoverned_success_risk = tier != FULLY_GOVERNED and hook_status == "allow"
    return {
        "governance_tier": tier,
        "governance_entrypoint": entrypoint,
        "is_fully_governed": tier == FULLY_GOVERNED,
        "degraded_reason": degraded_reason,
        "denied_reason": denied_reason,
        "ungoverned_success_risk": ungoverned_success_risk,
    }


def merge_governance_observability(details: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(details or {})
    payload.update(build_governance_observability(payload))
    return payload


def _self_check() -> int:
    fully = merge_governance_observability(
        {
            "entrypoint": CURSOR_SESSION_ENTRYPOINT,
            "navigation_required": True,
            "code_navigation_pack_ids": ["cnp_test"],
            "mimir_available": True,
            "pack_quality_state": "relevant",
        }
    )
    assert fully["governance_tier"] == FULLY_GOVERNED
    assert fully["is_fully_governed"] is True
    assert fully["degraded_reason"] is None

    denied = merge_governance_observability(
        {
            "entrypoint": CURSOR_SESSION_ENTRYPOINT,
            "hook_status": "deny",
            "denial_reason": "raw_shell_requires_structured_adapter",
        }
    )
    assert denied["governance_tier"] == PARTIALLY_GOVERNED
    assert denied["denied_reason"] == "raw_shell_requires_structured_adapter"

    ungoverned = merge_governance_observability({"entrypoint": "cursor_direct_mcp"})
    assert ungoverned["governance_tier"] == UNGCOVERNED
    print("governance-tier: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
