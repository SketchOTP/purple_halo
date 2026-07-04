#!/usr/bin/env python3
"""Target workspace contract: control plane vs product repo. Stdlib only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTROL_ROOT = Path(__file__).resolve().parent.parent
TARGET_CONFIG_PATH = CONTROL_ROOT / "project_memory" / "runtime" / "target_workspace.json"

ROUTING_TARGET = "target_product_work"
ROUTING_CONTROL = "control_plane_maintenance"

MODE_SELF = "self_product_mode"
MODE_EXTERNAL = "external_target_mode"

CONTROL_PATH_PREFIXES = (
    "scripts/loop_",
    "scripts/purple_halo_loop.py",
    "scripts/cursor_session.py",
    "scripts/session_orchestrator.py",
    "scripts/verify-loop.sh",
    "project_memory/runtime/loop_",
    "project_memory/runtime/control_",
    "project_memory/runtime/target_workspace.json",
    "contracts/",
)

DEFAULT_GOAL = "# Product Goal\n\nDefine the product goal for this repository.\n"
DEFAULT_STATUS = "# Project Status\n\n## Current state\n\n- Bootstrapped by purple_halo control plane\n"
DEFAULT_REPO_MAP = "# Repo Map\n\n## Layout\n\n- Describe key directories and entrypoints here.\n"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return slug or "target_repo"


def load_target_config() -> dict[str, Any] | None:
    if not TARGET_CONFIG_PATH.is_file():
        return None
    try:
        payload = json.loads(TARGET_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    path = str(payload.get("target_repo_path") or "").strip()
    if not path:
        return None
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        return None
    payload["target_repo_path"] = str(resolved)
    return payload


def target_configured() -> bool:
    return load_target_config() is not None


def resolve_contract(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    config = config or load_target_config()
    if not config:
        return None
    root = Path(str(config["target_repo_path"]))
    slug = str(config.get("target_repo_slug") or _slugify(root.name))
    runtime_root = root / "project_memory" / "runtime"
    return {
        "target_repo_path": str(root),
        "target_repo_slug": slug,
        "target_goal_path": str(root / "project_goals.md"),
        "target_status_path": str(root / "project_status.md"),
        "target_repo_map_path": str(root / "repo_map.md"),
        "target_runtime_root": str(runtime_root),
        "target_verification_commands": [
            list(c) for c in (config.get("target_verification_commands") or [])
        ],
    }


def active_execution_mode() -> str:
    cfg = load_target_config()
    if not cfg:
        return MODE_SELF
    if bool(cfg.get("external_target_enabled")) and str(cfg.get("execution_mode") or MODE_SELF) == MODE_EXTERNAL:
        return MODE_EXTERNAL
    return MODE_SELF


def external_target_enabled() -> bool:
    return active_execution_mode() == MODE_EXTERNAL


def external_target_capability_proven() -> bool:
    cfg = load_target_config() or {}
    if cfg.get("external_target_proven") is True:
        return True
    if not TARGET_WORKER_BRIDGE_PROVEN_PATH.is_file():
        return False
    try:
        latch = json.loads(TARGET_WORKER_BRIDGE_PROVEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not bool(latch.get("proven")):
        return False
    cfg_path = str(cfg.get("target_repo_path") or latch.get("target_repo_path") or "").strip()
    latch_path = str(latch.get("target_repo_path") or "").strip()
    if cfg_path and latch_path and Path(cfg_path).resolve() != Path(latch_path).resolve():
        return False
    return True


def active_contract() -> dict[str, Any] | None:
    if not external_target_enabled():
        return None
    return resolve_contract()


def is_target_active() -> bool:
    return active_contract() is not None


def product_root() -> Path:
    contract = active_contract()
    if contract:
        return Path(contract["target_repo_path"])
    return CONTROL_ROOT


def control_root() -> Path:
    return CONTROL_ROOT


def goal_path() -> Path:
    contract = active_contract()
    if contract:
        return Path(contract["target_goal_path"])
    return CONTROL_ROOT / "project_goals.md"


def status_path() -> Path:
    contract = active_contract()
    if contract:
        return Path(contract["target_status_path"])
    return CONTROL_ROOT / "project_status.md"


def repo_map_path() -> Path:
    contract = active_contract()
    if contract:
        return Path(contract["target_repo_map_path"])
    return CONTROL_ROOT / "repo_map.md"


def runtime_root() -> Path:
    contract = active_contract()
    if contract:
        return Path(contract["target_runtime_root"])
    return CONTROL_ROOT / "project_memory" / "runtime"


def backlog_path() -> Path:
    return runtime_root() / "goal_backlog.json"


def cycle_artifact_root() -> Path:
    return runtime_root() / "loop_cycles"


def cycle_index_path() -> Path:
    return cycle_artifact_root() / "index.json"


def verification_commands() -> list[list[str]]:
    contract = active_contract()
    if contract and contract.get("target_verification_commands"):
        return [list(c) for c in contract["target_verification_commands"]]
    return [["python3", "scripts/verify-loop.sh"]]


def _paths_for_item(item: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("target_files", "proposed_repo_delta", "expected_outputs", "expected_repo_delta"):
        for rel in item.get(key) or []:
            paths.append(str(rel))
    return paths


def is_control_plane_path(rel: str) -> bool:
    rel = rel.replace("\\", "/").lstrip("./")
    if rel.startswith("../"):
        return True
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in CONTROL_PATH_PREFIXES)


def classify_work_item(item: dict[str, Any]) -> str:
    explicit = str(item.get("routing_class") or "").strip()
    if explicit in {ROUTING_TARGET, ROUTING_CONTROL}:
        return explicit
    paths = _paths_for_item(item)
    if paths and all(is_control_plane_path(p) for p in paths):
        return ROUTING_CONTROL
    if is_target_active():
        return ROUTING_TARGET
    return ROUTING_CONTROL if paths and all(is_control_plane_path(p) for p in paths) else ROUTING_TARGET


def routing_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    """Lower sorts first. Target product work wins over control maintenance."""
    cls = classify_work_item(item)
    class_rank = 0 if cls == ROUTING_TARGET else 1
    if is_target_active() and cls == ROUTING_CONTROL:
        priority = int(item.get("priority") or 99) + 1000
    else:
        priority = int(item.get("priority") or 99)
    score = int(item.get("artifact_score") or priority)
    return (class_rank, score, priority)


def tag_item_routing(item: dict[str, Any]) -> dict[str, Any]:
    tagged = dict(item)
    tagged["routing_class"] = classify_work_item(item)
    return tagged


def inspect_target_repo() -> dict[str, Any]:
    contract = active_contract()
    if not contract:
        return {"configured": False}
    root = Path(contract["target_repo_path"])
    truth = {
        "project_goals.md": Path(contract["target_goal_path"]).is_file(),
        "project_status.md": Path(contract["target_status_path"]).is_file(),
        "repo_map.md": Path(contract["target_repo_map_path"]).is_file(),
    }
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    return {
        "configured": True,
        "target_repo_slug": contract["target_repo_slug"],
        "target_repo_path": contract["target_repo_path"],
        "truth_files_present": truth,
        "truth_complete": all(truth.values()),
        "git_porcelain_lines": len(proc.stdout.splitlines()),
        "runtime_root_exists": Path(contract["target_runtime_root"]).is_dir(),
    }


def bootstrap_target(*, force: bool = False) -> dict[str, Any]:
    contract = active_contract()
    if not contract:
        return {"bootstrapped": False, "reason": "no_target_configured", "actions": []}
    actions: list[str] = []
    root = Path(contract["target_repo_path"])
    runtime = Path(contract["target_runtime_root"])
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "loop_cycles").mkdir(parents=True, exist_ok=True)
    if force or not actions:
        actions.append(f"ensured runtime root: {runtime}")

    templates = (
        (Path(contract["target_goal_path"]), DEFAULT_GOAL, "created project_goals.md"),
        (Path(contract["target_status_path"]), DEFAULT_STATUS, "created project_status.md"),
        (Path(contract["target_repo_map_path"]), DEFAULT_REPO_MAP, "created repo_map.md"),
    )
    for path, content, label in templates:
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            actions.append(label)

    backlog = backlog_path()
    if not backlog.is_file():
        backlog.write_text(
            json.dumps({"product_work_items": [], "updated_at": _now_iso()}, indent=2) + "\n",
            encoding="utf-8",
        )
        actions.append(f"initialized backlog: {backlog}")

    bootstrap_record = {
        "bootstrapped": True,
        "target_repo_slug": contract["target_repo_slug"],
        "target_repo_path": contract["target_repo_path"],
        "actions": actions,
        "inspected_at": _now_iso(),
        "inspection": inspect_target_repo(),
    }
    record_path = runtime / "target_bootstrap.json"
    record_path.write_text(json.dumps(bootstrap_record, indent=2) + "\n", encoding="utf-8")
    return bootstrap_record


LIVE_TARGET_CYCLE_PROOF_PATH = CONTROL_ROOT / "project_memory" / "runtime" / "live_target_cycle_proof.json"
TARGET_WORK_PROOF_PATH = CONTROL_ROOT / "project_memory" / "runtime" / "target_work_proof.json"


def is_external_target() -> bool:
    return is_target_active() and product_root().resolve() != CONTROL_ROOT.resolve()


def force_proof_mode() -> bool:
    import os

    cfg = load_target_config() or {}
    if cfg.get("force_proof_mode"):
        return True
    return os.environ.get("PURPLE_HALO_FORCE_PROOF_MODE", "").strip() in {"1", "true", "yes"}


TARGET_WORKER_BRIDGE_PROVEN_PATH = CONTROL_ROOT / "project_memory" / "runtime" / "target_worker_bridge_proven.json"


def target_worker_bridge_proven() -> bool:
    if not external_target_enabled() or not TARGET_WORKER_BRIDGE_PROVEN_PATH.is_file():
        return False
    try:
        latch = json.loads(TARGET_WORKER_BRIDGE_PROVEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    contract = active_contract()
    if not contract:
        return False
    latch_path = str(latch.get("target_repo_path") or "").strip()
    if latch_path and Path(latch_path).resolve() != Path(contract["target_repo_path"]).resolve():
        return False
    return bool(latch.get("proven"))


def target_proof_satisfied() -> bool:
    if not is_external_target() or force_proof_mode():
        return False
    if not LIVE_TARGET_CYCLE_PROOF_PATH.is_file():
        return False
    try:
        proof = json.loads(LIVE_TARGET_CYCLE_PROOF_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    contract = active_contract()
    if not contract:
        return False
    proof_path = str((proof.get("target_repo") or {}).get("path") or "").strip()
    if proof_path and Path(proof_path).resolve() != Path(contract["target_repo_path"]).resolve():
        return False
    if target_worker_bridge_proven():
        return True
    return proof.get("passed") is True and not proof.get("failure_class")


def workspace_status() -> dict[str, Any]:
    contract = active_contract()
    control_health = {
        "root": str(CONTROL_ROOT),
        "loop_state": (CONTROL_ROOT / "project_memory/runtime/loop_state.json").is_file(),
        "target_config": TARGET_CONFIG_PATH.is_file(),
    }
    proven_cfg = load_target_config()
    product_backlog = {}
    next_capability = None
    try:
        import loop_backlog as lb

        bl = lb.load_backlog()
        items = bl.get("product_work_items") or []
        product_backlog = {
            "open": sum(1 for i in items if i.get("status") == "open"),
            "verified": sum(1 for i in items if i.get("status") == "verified"),
        }
        nxt = lb.pick_next_item(bl)
        if nxt:
            next_capability = {
                "work_id": nxt.get("work_id"),
                "title": nxt.get("title"),
                "capability": nxt.get("capability"),
            }
        elif not external_target_enabled():
            gaps = sorted(bl.get("capability_gaps") or [], key=lambda g: int(g.get("priority") or 99))
            if gaps:
                top = gaps[0]
                next_capability = {
                    "work_id": f"product_gap_{str(top.get('id') or '').removeprefix('gap_')}",
                    "title": top.get("description"),
                    "capability": "pending_gap",
                    "gap_id": top.get("id"),
                }
    except Exception:
        product_backlog = {"error": "backlog_unavailable"}

    if not contract:
        return {
            "mode": MODE_SELF,
            "active_execution_mode": active_execution_mode(),
            "active_target_repo": None,
            "control_plane": control_health,
            "product_backlog": product_backlog,
            "next_product_capability": next_capability,
            "external_target_mode": {
                "enabled": external_target_enabled(),
                "proven": external_target_capability_proven(),
                "configured": proven_cfg is not None,
                "configured_target_slug": (proven_cfg or {}).get("target_repo_slug"),
            },
            "target_product": {"configured": False, "active": False},
        }
    inspection = inspect_target_repo()
    target_state_path = Path(contract["target_runtime_root"]) / "target_state.json"
    target_backlog = backlog_path()
    backlog_summary = {}
    if target_backlog.is_file():
        try:
            bl = json.loads(target_backlog.read_text(encoding="utf-8"))
            items = bl.get("product_work_items") or []
            backlog_summary = {
                "open": sum(1 for i in items if i.get("status") == "open"),
                "in_progress": sum(1 for i in items if i.get("status") == "in_progress"),
                "verified": sum(1 for i in items if i.get("status") == "verified"),
            }
        except json.JSONDecodeError:
            backlog_summary = {"error": "invalid_backlog_json"}
    bootstrap_path = Path(contract["target_runtime_root"]) / "target_bootstrap.json"
    bootstrap_done = bootstrap_path.is_file()
    return {
        "mode": MODE_EXTERNAL,
        "active_execution_mode": active_execution_mode(),
        "active_target_repo": contract["target_repo_slug"],
        "contract": contract,
        "control_plane": control_health,
        "target_product": {
            "configured": True,
            "inspection": inspection,
            "bootstrap_completed": bootstrap_done,
            "backlog": backlog_summary,
            "target_state_present": target_state_path.is_file(),
            "target_proof_satisfied": target_proof_satisfied(),
            "target_work_proof_present": TARGET_WORK_PROOF_PATH.is_file(),
        },
    }


def ensure_target_ready() -> dict[str, Any]:
    if not is_target_active():
        return {"ready": True, "mode": "self"}
    inspection = inspect_target_repo()
    if inspection.get("truth_complete") and inspection.get("runtime_root_exists"):
        return {"ready": True, "mode": "target_workspace", "inspection": inspection}
    bootstrap = bootstrap_target()
    return {"ready": True, "mode": "target_workspace", "bootstrapped": True, "bootstrap": bootstrap}


def rel_to_product(abs_or_rel: str) -> str:
    path = Path(abs_or_rel)
    root = product_root()
    if path.is_absolute():
        try:
            return str(path.relative_to(root))
        except ValueError:
            return abs_or_rel
    return abs_or_rel.lstrip("./")


def self_check() -> None:
    assert CONTROL_ROOT.is_dir()
    assert classify_work_item({"target_files": ["scripts/loop_state.py"]}) == ROUTING_CONTROL
    assert classify_work_item({"target_files": ["src/main.py"]}) == ROUTING_TARGET
    tagged = tag_item_routing({"work_id": "x", "target_files": ["src/app.py"], "priority": 5})
    assert tagged["routing_class"] == ROUTING_TARGET

    global TARGET_CONFIG_PATH
    original_config_path = TARGET_CONFIG_PATH
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target_repo = tmp_path / "fake_product_repo"
        target_repo.mkdir()
        config_path = tmp_path / "target_workspace.json"
        config_path.write_text(
            json.dumps(
                {
                    "target_repo_path": str(target_repo),
                    "target_repo_slug": "fake_product",
                    "target_verification_commands": [["echo", "verify-ok"]],
                    "execution_mode": MODE_SELF,
                    "external_target_enabled": False,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        TARGET_CONFIG_PATH = config_path

        assert not is_target_active()
        config_path.write_text(
            json.dumps(
                {
                    **json.loads(config_path.read_text(encoding="utf-8")),
                    "execution_mode": MODE_EXTERNAL,
                    "external_target_enabled": True,
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        assert is_target_active()
        key_target = routing_sort_key({"routing_class": ROUTING_TARGET, "priority": 50})
        key_control = routing_sort_key({"routing_class": ROUTING_CONTROL, "priority": 1})
        assert key_target < key_control
        assert routing_sort_key({"routing_class": ROUTING_CONTROL, "priority": 1})[2] == 1001

        record = bootstrap_target()
        assert record["bootstrapped"] is True
        for rel in ("project_goals.md", "project_status.md", "repo_map.md"):
            assert (target_repo / rel).is_file(), rel
        runtime = target_repo / "project_memory" / "runtime"
        assert (runtime / "goal_backlog.json").is_file()
        assert (runtime / "target_bootstrap.json").is_file()
        assert inspect_target_repo()["truth_complete"]

        config_path.write_text(
            json.dumps(
                {
                    "target_repo_path": str(target_repo),
                    "target_repo_slug": "fake_product",
                    "target_verification_commands": [["echo", "verify-ok"]],
                    "execution_mode": MODE_SELF,
                    "external_target_enabled": False,
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        assert not is_target_active()
        assert routing_sort_key({"routing_class": ROUTING_CONTROL, "priority": 1})[2] == 1

    TARGET_CONFIG_PATH = original_config_path
    print("loop-target-workspace: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo target workspace utilities")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--show", action="store_true", help="Print workspace status JSON")
    parser.add_argument("--bootstrap", action="store_true", help="Bootstrap target repo truth files")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.bootstrap:
        print(json.dumps(bootstrap_target(), indent=2))
        return 0
    if args.show:
        print(json.dumps(workspace_status(), indent=2))
        return 0
    parser.error("specify --self-check, --show, or --bootstrap")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
