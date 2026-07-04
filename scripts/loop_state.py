#!/usr/bin/env python3
"""Load, validate, and persist purple_halo loop continuity state. Stdlib only."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import loop_target_workspace as ltw

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "project_memory" / "runtime" / "loop_state.json"
CYCLES_DIR = ROOT / "project_memory" / "runtime" / "loop_cycles"
SCHEMA_VERSION = 2
REQUIRED_CONTROL = frozenset(
    {
        "cycle_id",
        "status",
        "last_run_at",
        "next_focus",
        "completed_milestones",
        "last_cycle",
        "open_gaps",
        "rejected_milestones",
        "last_verified_repo_delta",
        "next_recommended_focus",
    }
)
REQUIRED_TARGET = frozenset({"status", "cycle_id", "last_cycle", "last_verified_repo_delta", "open_gaps"})
VALID_STATUS = frozenset({"ready", "blocked", "partial", "idle"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_control_state() -> dict[str, Any]:
    return {
        "cycle_id": 0,
        "status": "ready",
        "last_run_at": _now_iso(),
        "next_focus": "Run first autonomous cycle",
        "next_recommended_focus": "Run first autonomous cycle",
        "completed_milestones": [],
        "rejected_milestones": [],
        "open_gaps": [],
        "regression_milestones": {},
        "last_verified_repo_delta": {"files": [], "summary": "No verified cycle yet."},
        "last_cycle": {
            "artifact_dir": "",
            "plan_id": "bootstrap",
            "verification_passed": False,
            "summary": "No cycles completed yet.",
        },
    }


def default_target_state() -> dict[str, Any]:
    return {
        "configured": False,
        "target_repo_slug": "",
        "target_repo_path": "",
        "bootstrap_completed": False,
        "bootstrap_actions": [],
        "cycle_id": 0,
        "status": "idle",
        "last_run_at": "",
        "next_focus": "",
        "open_gaps": [],
        "last_verified_repo_delta": {"files": [], "summary": "No target cycles yet."},
        "last_cycle": {
            "artifact_dir": "",
            "plan_id": "",
            "verification_passed": False,
            "summary": "No target cycles yet.",
        },
    }


def default_state() -> dict[str, Any]:
    return default_control_state()


def migrate_state(state: dict[str, Any]) -> dict[str, Any]:
    """Upgrade persisted control state from v1 schema without breaking load."""
    base = default_control_state()
    merged = {**base, **state}
    if not isinstance(merged.get("open_gaps"), list):
        merged["open_gaps"] = []
    if not isinstance(merged.get("rejected_milestones"), list):
        merged["rejected_milestones"] = []
    if not isinstance(merged.get("regression_milestones"), dict):
        merged["regression_milestones"] = {}
    delta = merged.get("last_verified_repo_delta")
    if not isinstance(delta, dict):
        merged["last_verified_repo_delta"] = base["last_verified_repo_delta"]
    elif "files" not in delta:
        delta["files"] = []
    if not merged.get("next_recommended_focus"):
        merged["next_recommended_focus"] = str(merged.get("next_focus") or base["next_focus"])
    return merged


def migrate_target_state(state: dict[str, Any]) -> dict[str, Any]:
    base = default_target_state()
    merged = {**base, **state}
    if not isinstance(merged.get("open_gaps"), list):
        merged["open_gaps"] = []
    if not isinstance(merged.get("bootstrap_actions"), list):
        merged["bootstrap_actions"] = []
    delta = merged.get("last_verified_repo_delta")
    if not isinstance(delta, dict):
        merged["last_verified_repo_delta"] = base["last_verified_repo_delta"]
    elif "files" not in delta:
        delta["files"] = []
    return merged


def validate_control_state(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_CONTROL - set(state)
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    status = state.get("status")
    if status not in VALID_STATUS - {"idle"}:
        errors.append(f"invalid status: {status!r}")
    cycle_id = state.get("cycle_id")
    if not isinstance(cycle_id, int) or cycle_id < 0:
        errors.append("cycle_id must be a non-negative integer")
    last_cycle = state.get("last_cycle")
    if not isinstance(last_cycle, dict):
        errors.append("last_cycle must be an object")
    elif not {"artifact_dir", "plan_id", "verification_passed"}.issubset(last_cycle):
        errors.append("last_cycle missing required fields")
    for key in ("completed_milestones", "rejected_milestones", "open_gaps"):
        if not isinstance(state.get(key), list):
            errors.append(f"{key} must be an array")
    delta = state.get("last_verified_repo_delta")
    if not isinstance(delta, dict) or "files" not in delta:
        errors.append("last_verified_repo_delta must be an object with files")
    return errors


def validate_target_state(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_TARGET - set(state)
    if missing:
        errors.append(f"missing target keys: {sorted(missing)}")
    status = state.get("status")
    if status not in VALID_STATUS:
        errors.append(f"invalid target status: {status!r}")
    cycle_id = state.get("cycle_id")
    if not isinstance(cycle_id, int) or cycle_id < 0:
        errors.append("target cycle_id must be a non-negative integer")
    return errors


def validate_state(state: dict[str, Any]) -> list[str]:
    return validate_control_state(state)


def load_combined_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "control_state": default_control_state(),
            "target_state": default_target_state(),
        }
    raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if "control_state" in raw:
        control = migrate_state(raw.get("control_state") or {})
        target = migrate_target_state(raw.get("target_state") or {})
    else:
        control = migrate_state(raw)
        target = default_target_state()
    return {"schema_version": SCHEMA_VERSION, "control_state": control, "target_state": target}


def load_state() -> dict[str, Any]:
    combined = load_combined_state()
    return combined["control_state"]


def load_target_state() -> dict[str, Any]:
    return load_combined_state()["target_state"]


def save_combined_state(combined: dict[str, Any]) -> Path:
    control = migrate_state(combined.get("control_state") or {})
    target = migrate_target_state(combined.get("target_state") or {})
    errors = validate_control_state(control)
    if errors:
        raise ValueError(f"refusing to save invalid control state: {errors}")
    terrors = validate_target_state(target)
    if terrors:
        raise ValueError(f"refusing to save invalid target state: {terrors}")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "control_state": control,
        "target_state": target,
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return STATE_PATH


def save_state(state: dict[str, Any]) -> Path:
    combined = load_combined_state()
    combined["control_state"] = migrate_state(state)
    return save_combined_state(combined)


def save_target_state(state: dict[str, Any]) -> Path:
    combined = load_combined_state()
    combined["target_state"] = migrate_target_state(state)
    return save_combined_state(combined)


def sync_target_state_from_contract(*, persist: bool = False) -> dict[str, Any]:
    target = migrate_target_state(load_target_state())
    contract = ltw.active_contract()
    if contract:
        target["configured"] = True
        target["target_repo_slug"] = str(contract.get("target_repo_slug") or "")
        target["target_repo_path"] = str(contract.get("target_repo_path") or "")
    else:
        target["configured"] = False
        target["target_repo_slug"] = ""
        target["target_repo_path"] = ""

    ws = ltw.workspace_status()
    product = ws.get("target_product") or {}
    target["bootstrap_completed"] = bool(product.get("bootstrap_completed"))
    actions: list[str] = []
    if contract:
        bootstrap_path = Path(contract["target_runtime_root"]) / "target_bootstrap.json"
        if bootstrap_path.is_file():
            try:
                record = json.loads(bootstrap_path.read_text(encoding="utf-8"))
                actions = [str(a) for a in record.get("actions") or []]
            except (OSError, json.JSONDecodeError):
                actions = []
    target["bootstrap_actions"] = actions
    if not contract:
        target["status"] = "idle"
    elif target.get("status") == "idle":
        target["status"] = "ready"

    if persist:
        save_target_state(target)
    return target


def cycle_artifact_dir(cycle_id: int) -> Path:
    path = ltw.cycle_artifact_root() / f"cycle_{int(cycle_id):04d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_cycle_artifact(cycle_id: int, name: str, payload: dict[str, Any]) -> Path:
    path = cycle_artifact_dir(cycle_id) / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def self_check() -> None:
    control = default_control_state()
    assert not validate_control_state(control)
    target = default_target_state()
    assert not validate_target_state(target)

    v1 = {
        "cycle_id": 2,
        "status": "ready",
        "last_run_at": _now_iso(),
        "next_focus": "test",
        "next_recommended_focus": "test",
        "completed_milestones": [],
        "rejected_milestones": [],
        "open_gaps": [],
        "last_verified_repo_delta": {"files": [], "summary": "test"},
        "last_cycle": {
            "artifact_dir": "",
            "plan_id": "bootstrap",
            "verification_passed": False,
            "summary": "test",
        },
    }
    migrated = migrate_state(v1)
    assert migrated["cycle_id"] == 2
    assert not validate_control_state(migrated)

    global STATE_PATH
    original_state_path = STATE_PATH
    with tempfile.TemporaryDirectory() as tmp:
        STATE_PATH = Path(tmp) / "loop_state.json"
        save_combined_state(
            {
                "schema_version": SCHEMA_VERSION,
                "control_state": control,
                "target_state": target,
            }
        )
        loaded = load_combined_state()
        assert loaded["schema_version"] == SCHEMA_VERSION
        assert loaded["control_state"]["cycle_id"] == control["cycle_id"]
        assert loaded["target_state"]["configured"] is False

        STATE_PATH.write_text(json.dumps(v1, indent=2) + "\n", encoding="utf-8")
        loaded_v1 = load_combined_state()
        assert loaded_v1["control_state"]["cycle_id"] == 2
        assert loaded_v1["target_state"]["configured"] is False
    STATE_PATH = original_state_path

    probe_id = 99999
    art_dir = cycle_artifact_dir(probe_id)
    assert art_dir.name == "cycle_99999"
    assert art_dir.is_dir()
    artifact = write_cycle_artifact(probe_id, "self_check.json", {"ok": True})
    assert artifact.is_file()
    for path in (artifact, art_dir):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            for child in path.iterdir():
                child.unlink()
            path.rmdir()

    synced = sync_target_state_from_contract()
    assert isinstance(synced.get("configured"), bool)
    assert not validate_target_state(synced)

    from loop_open_gaps_state import open_gaps_state_freshness

    ogs = open_gaps_state_freshness()
    assert ogs.get("status") in {"fresh", "stale", "missing"}
    print("loop-state: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo loop continuity state")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--show", action="store_true", help="Print combined loop state JSON")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.show:
        print(json.dumps(load_combined_state(), indent=2))
        return 0
    parser.error("specify --self-check or --show")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
