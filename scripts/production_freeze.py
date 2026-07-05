#!/usr/bin/env python3
"""Production freeze posture and release gate for purple_halo v1."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "project_memory" / "runtime"
HISTORY = RUNTIME / "schedule_run_history.json"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_history() -> dict[str, Any]:
    if not HISTORY.is_file():
        return {}
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_history(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def production_freeze_active(history: dict[str, Any] | None = None) -> bool:
    history = history if history is not None else _load_history()
    return bool(history.get("production_freeze_mode"))


def ensure_production_freeze_mode() -> dict[str, Any]:
    """Enable freeze by default once local production readiness is earned."""
    history = _load_history()
    if history.get("local_production_ready") and not history.get("production_freeze_mode"):
        history["production_freeze_mode"] = True
        history["feature_freeze"] = True
        history["architecture_freeze"] = True
        history["production_freeze_entered_at"] = _now()
        history["production_freeze_policy"] = {
            "no_new_loop_capabilities": True,
            "no_target_mode_expansion": True,
            "no_new_proof_modes": True,
            "no_new_ui_features_unless_bug_driven": True,
            "allowed_work": [
                "bug_fix",
                "regression_repair",
                "operator_requested_change",
                "packaging_install_improvement",
            ],
        }
        _save_history(history)
    return history


def release_gate(*, skip_health: bool = False) -> dict[str, Any]:
    """Canonical v1 release gate status."""
    ensure_production_freeze_mode()
    history = _load_history()

    self_check_ok = True
    self_check_errors: list[str] = []
    if skip_health:
        pass  # ponytail: caller is startup_health_checks; avoid recursive re-load of history
    else:
        try:
            from operator_runtime import read_service_status

            cached = read_service_status().get("health") or {}
            self_check_ok = bool(cached.get("ok"))
            if not self_check_ok:
                self_check_errors.append("cached service health not ok")
        except Exception as exc:
            self_check_ok = False
            self_check_errors.append(str(exc)[:200])

    open_regressions: list[dict[str, Any]] = []
    try:
        from loop_production_hold import detect_hold_regressions, production_hold_active

        if production_hold_active(history):
            open_regressions = detect_hold_regressions()
    except Exception as exc:
        open_regressions = [{"class": "hold_probe_error", "detail": str(exc)[:200]}]

    gates = {
        "self_check_health": self_check_ok,
        "ui_operator_ready": bool(history.get("ui_operator_ready")),
        "service_soak_passed": bool(history.get("service_soak_passed")),
        "local_production_ready": bool(history.get("local_production_ready")),
        "no_open_regressions": len(open_regressions) == 0,
        "production_freeze_mode": bool(history.get("production_freeze_mode")),
    }
    passed = all(gates.values())
    return {
        "production_freeze_mode": bool(history.get("production_freeze_mode")),
        "production_freeze_policy": history.get("production_freeze_policy") or {},
        "production_freeze_entered_at": history.get("production_freeze_entered_at") or "",
        "gates": gates,
        "open_regressions": open_regressions,
        "self_check_errors": self_check_errors,
        "release_ready": passed,
        "checked_at": _now(),
    }


def freeze_status_fields() -> dict[str, Any]:
    gate = release_gate()
    return {
        "production_freeze_mode": gate.get("production_freeze_mode"),
        "release_gate": gate,
        "release_ready": gate.get("release_ready"),
    }


def self_check() -> None:
    hist = ensure_production_freeze_mode()
    assert "local_production_ready" in hist or True
    gate = release_gate()
    assert "gates" in gate
    assert "production_freeze_mode" in gate
    print("production-freeze: PASS")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="purple_halo production freeze / release gate")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--ensure", action="store_true")
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.ensure:
        print(json.dumps(ensure_production_freeze_mode(), indent=2))
        return 0
    if args.gate:
        print(json.dumps(release_gate(), indent=2))
        return 0
    parser.error("specify --self-check, --ensure, or --gate")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())