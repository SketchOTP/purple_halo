#!/usr/bin/env python3
"""Bounded structured adapters for install_dependencies and run_application."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

INSTALL_MANIFESTS: tuple[tuple[str, str], ...] = (
    ("pip-editable", "pyproject.toml"),
    ("pip-requirements", "requirements.txt"),
    ("npm-ci", "package-lock.json"),
    ("npm-install", "package.json"),
)

RUN_PROFILES: dict[str, list[str]] = {
    "verify": ["bash", "scripts/verify.sh"],
    "governance_report": [sys.executable, "scripts/governance_operator_report.py"],
    "select_context": [sys.executable, "scripts/select-context.py", "--self-check"],
    "collect_evidence": [sys.executable, "scripts/collect_production_evidence.py", "--self-check"],
}


def _resolve_under_root(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    if resolved != ROOT and ROOT not in resolved.parents:
        raise ValueError(f"path escapes workspace root: {value}")
    return resolved


def detect_install_manifest(root: Path | None = None) -> dict[str, Any]:
    base = (root or ROOT).resolve()
    for manifest_type, filename in INSTALL_MANIFESTS:
        manifest_path = base / filename
        if manifest_path.is_file():
            return {
                "ok": True,
                "manifest_type": manifest_type,
                "manifest_path": str(manifest_path.relative_to(ROOT)) if manifest_path.is_relative_to(ROOT) else str(manifest_path),
                "command": build_install_command(manifest_type, base),
            }
    return {
        "ok": False,
        "blocked_reason": "no_supported_install_manifest",
        "message": "No pyproject.toml, requirements.txt, package.json, or package-lock.json found",
    }


def build_install_command(manifest_type: str, root: Path | None = None) -> list[str]:
    base = (root or ROOT).resolve()
    if manifest_type == "pip-editable":
        if not (base / "pyproject.toml").is_file():
            raise ValueError("pyproject.toml not found")
        return [sys.executable, "-m", "pip", "install", "-e", str(base)]
    if manifest_type == "pip-requirements":
        req = base / "requirements.txt"
        if not req.is_file():
            raise ValueError("requirements.txt not found")
        return [sys.executable, "-m", "pip", "install", "-r", str(req)]
    if manifest_type == "npm-ci":
        if not (base / "package-lock.json").is_file():
            raise ValueError("package-lock.json not found")
        return ["npm", "ci"]
    if manifest_type == "npm-install":
        if not (base / "package.json").is_file():
            raise ValueError("package.json not found")
        return ["npm", "install"]
    raise ValueError(f"unsupported manifest_type: {manifest_type:}")


def resolve_install(*, manifest_type: str | None, path: str | None) -> dict[str, Any]:
    base = _resolve_under_root(path or ".")
    if manifest_type in {None, "", "auto"}:
        detected = detect_install_manifest(base)
        if not detected.get("ok"):
            return detected
        manifest_type = str(detected["manifest_type"])
    try:
        command = build_install_command(str(manifest_type), base)
    except ValueError as exc:
        return {"ok": False, "blocked_reason": "invalid_install_manifest", "message": str(exc)}
    return {
        "ok": True,
        "manifest_type": manifest_type,
        "manifest_path": str(base),
        "command": command,
        "command_text": " ".join(command),
        "action_class": "install_dependencies",
    }


def validate_script_target(script_path: str) -> dict[str, Any]:
    try:
        resolved = _resolve_under_root(script_path)
    except ValueError as exc:
        return {"ok": False, "blocked_reason": "path_escapes_workspace", "message": str(exc)}
    rel = resolved.relative_to(ROOT).as_posix()
    if not rel.startswith("scripts/"):
        return {"ok": False, "blocked_reason": "script_outside_scripts_dir", "message": rel}
    if resolved.suffix != ".py":
        return {"ok": False, "blocked_reason": "script_not_python", "message": rel}
    if not resolved.is_file():
        return {"ok": False, "blocked_reason": "script_not_found", "message": rel}
    return {"ok": True, "script_path": rel, "resolved": str(resolved)}


def resolve_run(*, profile: str | None, script_path: str | None, extra_args: list[str] | None) -> dict[str, Any]:
    extra = list(extra_args or [])
    if profile:
        command = list(RUN_PROFILES.get(profile) or [])
        if not command:
            return {"ok": False, "blocked_reason": "unknown_run_profile", "message": profile}
        if extra:
            command.extend(extra)
        return {
            "ok": True,
            "profile": profile,
            "command": command,
            "command_text": " ".join(command),
            "action_class": "run_application",
            "target_paths": ["scripts"],
        }
    if script_path:
        checked = validate_script_target(script_path)
        if not checked.get("ok"):
            return checked
        command = [sys.executable, checked["script_path"], *extra]
        return {
            "ok": True,
            "script_path": checked["script_path"],
            "command": command,
            "command_text": " ".join(command),
            "action_class": "run_application",
            "target_paths": [checked["script_path"]],
        }
    return {
        "ok": False,
        "blocked_reason": "run_target_required",
        "message": "Provide --profile or --script-path",
    }


def execute_command(command: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {
        "exit_code": int(completed.returncode),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _self_check() -> int:
    blocked = resolve_install(manifest_type="auto", path=".")
    assert blocked["ok"] is False
    assert blocked["blocked_reason"] == "no_supported_install_manifest"

    run_plan = resolve_run(profile="governance_report", script_path=None, extra_args=None)
    assert run_plan["ok"] is True
    assert run_plan["action_class"] == "run_application"

    bad_script = validate_script_target("../outside.py")
    assert bad_script["ok"] is False

    good_script = validate_script_target("scripts/governance_tier.py")
    assert good_script["ok"] is True

    script_run = resolve_run(profile=None, script_path="scripts/governance_tier.py", extra_args=[])
    assert script_run["ok"] is True
    print("structured-adapters: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Bounded install/run structured adapters.")
    parser.add_argument("--self-check", action="store_true")
    sub = parser.add_subparsers(dest="command")

    install = sub.add_parser("install-plan")
    install.add_argument("--manifest", default="auto")
    install.add_argument("--path", default=".")

    run = sub.add_parser("run-plan")
    run.add_argument("--profile")
    run.add_argument("--script-path")
    run.add_argument("--extra-arg", action="append", default=[])

    args = parser.parse_args(argv)
    if args.self_check:
        return _self_check()
    if args.command == "install-plan":
        print(json.dumps(resolve_install(manifest_type=args.manifest, path=args.path), indent=2, sort_keys=True))
        return 0
    if args.command == "run-plan":
        print(json.dumps(resolve_run(profile=args.profile, script_path=args.script_path, extra_args=args.extra_arg), indent=2, sort_keys=True))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
