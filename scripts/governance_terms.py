#!/usr/bin/env python3
"""Canonical governance terminology — replaces legacy Architect MCP naming in runtime code."""

from __future__ import annotations

from typing import Any


def requires_approval_gate(details: dict[str, Any] | None) -> bool:
    payload = details or {}
    return bool(payload.get("requires_approval_gate") or payload.get("requires_architect"))


def approval_request_id(details: dict[str, Any] | None) -> str | None:
    payload = details or {}
    value = payload.get("approval_request_id") or payload.get("architect_request_id")
    return str(value) if value else None


def route_for_details(details: dict[str, Any] | None, *, default: str = "direct") -> str:
    payload = details or {}
    explicit = payload.get("route")
    if explicit:
        route = str(explicit)
        if route == "architect_direct":
            return "approval_gated"
        return route
    return "approval_gated" if requires_approval_gate(payload) else default


def normalize_hook_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Write canonical keys; drop legacy architect-prefixed fields when redundant."""
    payload = dict(details or {})
    if requires_approval_gate(payload):
        payload["requires_approval_gate"] = True
    request_id = approval_request_id(payload)
    if request_id:
        payload["approval_request_id"] = request_id
    if payload.get("requires_architect_review") and "requires_governance_review" not in payload:
        payload["requires_governance_review"] = payload["requires_architect_review"]
    payload.pop("requires_architect", None)
    payload.pop("architect_request_id", None)
    return payload


def _self_check() -> int:
    details = {"requires_architect": True, "architect_request_id": "apr-1"}
    assert requires_approval_gate(details)
    assert approval_request_id(details) == "apr-1"
    assert route_for_details(details) == "approval_gated"
    normalized = normalize_hook_details(details)
    assert normalized["requires_approval_gate"] is True
    assert "requires_architect" not in normalized
    print("governance-terms: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
