#!/usr/bin/env python3
"""Shared runtime helpers for Atlas production service: safe JSON, health, slot lock."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "project_memory" / "runtime"
SERVICE_STATUS = RUNTIME / "service_status.json"
INSTALL_META = RUNTIME / "install_meta.json"
SLOT_LOCK = RUNTIME / "schedule_slot_lock.json"

REQUIRED_RUNTIME = {
    "schedule": RUNTIME / "schedule.json",
    "cost_policy": RUNTIME / "cost_policy.json",
    "goal_ledger": RUNTIME / "goal_delivery_ledger.json",
    "schedule_history": RUNTIME / "schedule_run_history.json",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json_safe(path: Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load JSON; quarantine corrupt files and return default."""
    default = default if default is not None else {}
    if not path.is_file():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("json root must be object")
        return data
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine = path.with_name(path.name + ".corrupt." + stamp)
        try:
            shutil.move(str(path), str(quarantine))
        except OSError:
            pass
        note = RUNTIME / "last_json_corruption.json"
        note.write_text(
            json.dumps(
                {
                    "path": str(path),
                    "quarantine": str(quarantine),
                    "error": str(exc),
                    "at": _now(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return dict(default)


def save_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_service_status(**fields: Any) -> dict[str, Any]:
    current = load_json_safe(SERVICE_STATUS)
    current.update(fields)
    current["updated_at"] = _now()
    save_json_atomic(SERVICE_STATUS, current)
    return current


def read_service_status() -> dict[str, Any]:
    return load_json_safe(
        SERVICE_STATUS,
        default={
            "state": "down",
            "last_failure": "",
            "last_start": "",
            "health": {},
        },
    )


def repo_slug(name: str) -> str:
    """Match install_to_repo.sh slug for systemd unit names."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")


def read_install_meta(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    return load_json_safe(root / "project_memory" / "runtime" / "install_meta.json")


def service_unit_for_repo(root: Path | None = None) -> str:
    root = root or ROOT
    meta = read_install_meta(root)
    unit = str(meta.get("unit") or "").strip()
    if unit:
        return unit
    if root.name == "purple_halo":
        return "purple-halo-operator.service"
    return f"purple-halo-{repo_slug(root.name)}.service"


def claim_schedule_slot(slot_at: str, *, today: str | None = None) -> dict[str, Any]:
    """Claim a schedule slot for today. Prevents duplicate fires across restarts."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    key = today + ":" + str(slot_at)
    lock = load_json_safe(SLOT_LOCK)
    if lock.get("last_claimed") == key:
        return {"claimed": False, "reason": "already_claimed", "key": key, "lock": lock}
    lock = {
        "last_claimed": key,
        "claimed_at": _now(),
        "slot_at": slot_at,
        "day": today,
    }
    save_json_atomic(SLOT_LOCK, lock)
    return {"claimed": True, "reason": "claimed", "key": key, "lock": lock}


def startup_health_checks() -> dict[str, Any]:
    """Validate runtime artifacts required for unattended operation."""
    checks: dict[str, Any] = {}
    ok = True

    # Ensure defaults exist for missing critical files.
    if not (RUNTIME / "schedule.json").is_file() and (RUNTIME / "schedule.default.json").is_file():
        shutil.copyfile(RUNTIME / "schedule.default.json", RUNTIME / "schedule.json")

    schedule = load_json_safe(RUNTIME / "schedule.json")
    checks["schedule_loaded"] = bool(schedule) and (
        "runs" in schedule
        or schedule.get("every_hours")
        or schedule.get("schedule_kind") == "interval"
    )
    ok = ok and checks["schedule_loaded"]

    policy = load_json_safe(RUNTIME / "cost_policy.json", default={"budget_mode": "cheap_default"})
    if not (RUNTIME / "cost_policy.json").is_file():
        save_json_atomic(RUNTIME / "cost_policy.json", {"budget_mode": "cheap_default", "allow_expensive_execution": False})
        policy = load_json_safe(RUNTIME / "cost_policy.json")
    checks["cost_policy_loaded"] = str(policy.get("budget_mode") or "") != ""
    ok = ok and checks["cost_policy_loaded"]

    ledger = load_json_safe(RUNTIME / "goal_delivery_ledger.json")
    checks["goal_ledger_loaded"] = bool(ledger.get("criteria")) or (RUNTIME / "goal_delivery_ledger.json").is_file()
    # ledger may be regenerated; treat missing-but-regenerable as soft ok
    if not checks["goal_ledger_loaded"]:
        try:
            import sys

            sys.path.insert(0, str(ROOT / "scripts"))
            from loop_goal_delivery import refresh_ledger

            ledger = refresh_ledger()
            checks["goal_ledger_loaded"] = bool(ledger.get("criteria"))
        except Exception as exc:
            checks["goal_ledger_error"] = str(exc)[:200]
    ok = ok and checks["goal_ledger_loaded"]

    history_path = RUNTIME / "schedule_run_history.json"
    history = load_json_safe(
        history_path,
        default={"attempts": [], "sequence": [], "autonomous_allowed": True},
    )
    if not history_path.is_file():
        # Prefer latest quarantine backup over a blank history (avoids wiping flags).
        backups = sorted(RUNTIME.glob("schedule_run_history.json.corrupt.*"))
        seed: dict[str, Any] = {
            "attempts": [],
            "sequence": [],
            "autonomous_allowed": True,
            "production_hold_mode": True,
            "production_freeze_mode": True,
            "stop_classification": "",
            "stop_reason": "",
        }
        if backups:
            recovered = load_json_safe(backups[-1], default={})
            if recovered:
                seed.update(recovered)
        save_json_atomic(history_path, seed)
        history = load_json_safe(history_path)
    checks["runtime_state_loaded"] = "autonomous_allowed" in history or "sequence" in history
    ok = ok and checks["runtime_state_loaded"]

    # Preserve pause/hold fields explicitly in health snapshot.
    checks["preserved_state"] = {
        "autonomous_allowed": history.get("autonomous_allowed", True),
        "production_hold_mode": history.get("production_hold_mode"),
        "stop_classification": history.get("stop_classification") or "",
        "stop_reason": history.get("stop_reason") or "",
        "ui_operator_ready": history.get("ui_operator_ready"),
    }
    # Enter production freeze once local production readiness is earned.
    try:
        from production_freeze import ensure_production_freeze_mode, release_gate

        ensure_production_freeze_mode()
        gate = release_gate()
        checks["production_freeze_mode"] = bool(gate.get("production_freeze_mode"))
        checks["release_ready"] = bool(gate.get("release_ready"))
    except Exception as exc:
        checks["production_freeze_error"] = str(exc)[:200]
    checks["ok"] = ok
    checks["checked_at"] = _now()
    return checks


def self_check() -> None:
    claim1 = claim_schedule_slot("99:99", today="2099-01-01")
    claim2 = claim_schedule_slot("99:99", today="2099-01-01")
    assert claim1["claimed"] is True
    assert claim2["claimed"] is False
    # cleanup test lock if it was only our test key
    lock = load_json_safe(SLOT_LOCK)
    if lock.get("last_claimed") == "2099-01-01:99:99":
        SLOT_LOCK.unlink(missing_ok=True)
    health = startup_health_checks()
    assert "schedule_loaded" in health
    assert "cost_policy_loaded" in health
    print("operator-runtime: PASS")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.health:
        health = startup_health_checks()
        print(json.dumps(health, indent=2))
        raise SystemExit(0 if health.get("ok") else 1)
    else:
        parser.error("specify --self-check or --health")